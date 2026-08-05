from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import threading
from typing import Callable, Protocol

from ..models.sources import Source, UnsupportedSourceError
from ..models.switcher import (
    SwitcherCapabilities,
    SwitcherConnectionState,
    SwitcherStatus,
    SwitcherType,
)
from ..models.tally import SourceTally, TallyState
from .base import AbstractSwitcher
from .capabilities import get_available_sources, get_switcher_capabilities
from .osee_duet_gsp import OseeDuetGspController, OseeDuetSourceMap
from .osee_gsp import GspCommand, GspTransportError, OseeGspTransport

LOGGER = logging.getLogger(__name__)


class OseeDuetTransport(Protocol):
    connected: bool

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def reconnect(self) -> None: ...
    def send_command(self, command: GspCommand) -> bytes: ...
    def send_get(self, command_id: str) -> bytes: ...
    def receive(self) -> tuple[GspCommand, ...]: ...


TransportFactory = Callable[[str, int], OseeDuetTransport]


def _default_transport_factory(host: str, port: int) -> OseeDuetTransport:
    return OseeGspTransport(host, port, connect_timeout=2.0, read_timeout=0.02)


@dataclass
class OseeGoStreamDuetSwitcher(AbstractSwitcher):
    """Runtime GSP backend for Osee GoStream Duet 8 ISO.

    Generic callers see only logical sources (``Input 1`` .. ``Input 8``,
    ``MP1``, ``MP2``, ``M/SRC``). Model-specific GSP IDs remain encapsulated in
    :class:`OseeDuetGspController`.
    """

    host: str
    port: int = 19010
    transport_factory: TransportFactory = _default_transport_factory
    logger: logging.Logger = LOGGER
    _transport: OseeDuetTransport = field(init=False, repr=False)
    _controller: OseeDuetGspController = field(init=False, repr=False)
    _connected: bool = field(default=False, init=False)
    _last_error: str | None = field(default=None, init=False)
    _last_sync_at: str | None = field(default=None, init=False)
    _transition_log: list[str] = field(default_factory=list, init=False)
    _reconnect_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _reconnect_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _disconnect_logged: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("Osee Duet GSP backend requires switcher.host")
        if not 1 <= self.port <= 65535:
            raise ValueError("Osee Duet GSP port must be in range 1..65535")
        self.host = self.host.strip()
        self._build_transport()

    def _build_transport(self) -> None:
        self._transport = self.transport_factory(self.host, self.port)
        self._controller = OseeDuetGspController(self._transport, logger=self.logger)

    @property
    def capabilities(self) -> SwitcherCapabilities:
        return get_switcher_capabilities(SwitcherType.OSEE_GOSTREAM_DUET)

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def last_sync_at(self) -> str | None:
        return self._last_sync_at

    @property
    def transition_log(self) -> list[str]:
        return self._transition_log

    @property
    def transition_state(self) -> tuple[int | float | str, ...] | None:
        return self._controller.state.transition_status

    def connect(self) -> None:
        if self.is_connected():
            return
        self._transport.connect()
        self._connected = True
        self._last_error = None
        self._disconnect_logged = False
        self._request_initial_state()

    def _request_initial_state(self) -> None:
        for command_id in ("pvwIndex", "pgmIndex", "transitionStatus"):
            try:
                self._transport.send_get(command_id)
            except Exception as exc:
                self.logger.debug("Osee initial state request failed id=%s: %s", command_id, exc)
                break

    def disconnect(self) -> None:
        self._transport.disconnect()
        self._connected = False

    def reconnect(self) -> None:
        """Start a bounded reconnect attempt in a daemon thread.

        This keeps the joystick polling thread responsive when the TCP cable is
        unplugged or the switcher is rebooting.
        """
        with self._reconnect_lock:
            if self._reconnect_thread is not None and self._reconnect_thread.is_alive():
                return
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_worker,
                name="osee-duet-gsp-reconnect",
                daemon=True,
            )
            self._reconnect_thread.start()

    def _reconnect_worker(self) -> None:
        try:
            self._transport.disconnect()
            self._build_transport()
            self.connect()
            self.logger.info("Osee Duet GSP reconnected to %s:%s", self.host, self.port)
        except Exception as exc:
            self._connected = False
            self._last_error = str(exc)
            self.logger.debug("Osee Duet GSP reconnect attempt failed: %s", exc)

    def is_connected(self) -> bool:
        return self._connected and bool(self._transport.connected)

    def get_status(self) -> SwitcherStatus:
        if self.is_connected():
            state = SwitcherConnectionState.CONNECTED
            message = "connected"
        elif self._last_error:
            state = SwitcherConnectionState.ERROR
            message = self._last_error
        else:
            state = SwitcherConnectionState.DISCONNECTED
            message = "disconnected"
        return SwitcherStatus(type=SwitcherType.OSEE_GOSTREAM_DUET.value, state=state, message=message)

    def get_available_sources(self) -> tuple[Source, ...]:
        return get_available_sources(SwitcherType.OSEE_GOSTREAM_DUET)

    def get_program_source(self) -> str | None:
        ref = self._controller.state.program
        return ref.canonical_id if ref is not None else None

    def get_preview_source(self) -> str | None:
        ref = self._controller.state.preview
        return ref.canonical_id if ref is not None else None

    def _require_source(self, source_id: str) -> str:
        try:
            return OseeDuetSourceMap.normalize(source_id)
        except ValueError as exc:
            raise UnsupportedSourceError(source_id) from exc

    def set_preview_source(self, source_id: str) -> None:
        canonical = self._require_source(source_id)
        self._controller.set_preview(canonical)
        # Optimistic local state; authoritative push/get messages may correct it.
        self._controller.state.preview = OseeDuetSourceMap.from_gsp_id(OseeDuetSourceMap.to_gsp_id(canonical))
        self._transition_log.append(f"preview:{canonical}")

    def set_program_source(self, source_id: str) -> None:
        canonical = self._require_source(source_id)
        self._controller.set_program(canonical)
        self._controller.state.program = OseeDuetSourceMap.from_gsp_id(OseeDuetSourceMap.to_gsp_id(canonical))
        self._transition_log.append(f"program:{canonical}")

    def cut(self) -> None:
        self._controller.cut()
        self._transition_log.append("cut")

    def auto(self) -> None:
        self._controller.auto()
        self._transition_log.append("auto")

    def copy_program_to_preview(self) -> None:
        program = self.get_program_source()
        if program is None:
            raise RuntimeError("Cannot copy Program to Preview before Osee Program state is known")
        self.set_preview_source(program)

    def get_tally_state(self) -> tuple[SourceTally, ...]:
        program = self.get_program_source()
        preview = self.get_preview_source()
        result: list[SourceTally] = []
        for source in self.get_available_sources():
            if source.id == program:
                state = TallyState.PROGRAM
            elif source.id == preview:
                state = TallyState.PREVIEW
            else:
                state = TallyState.OFF
            result.append(SourceTally(source_id=source.id, state=state))
        return tuple(result)

    def poll(self) -> None:
        if not self.is_connected():
            return
        try:
            commands = self._transport.receive()
        except GspTransportError as exc:
            self._connected = False
            self._last_error = str(exc)
            if not self._disconnect_logged:
                self.logger.warning("Osee Duet GSP connection lost: %s", exc)
                self._disconnect_logged = True
            return
        changed = False
        for command in commands:
            changed = self._controller.handle_command(command) or changed
        if commands:
            self._last_sync_at = datetime.now(timezone.utc).isoformat()
        if changed:
            self._last_error = None
