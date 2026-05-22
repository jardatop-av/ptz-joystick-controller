from __future__ import annotations

from dataclasses import dataclass, field
import logging
import xml.etree.ElementTree as ET

from ..models.sources import Source, UnsupportedSourceError
from ..models.switcher import SwitcherCapabilities, SwitcherConnectionState, SwitcherStatus, SwitcherType
from ..models.tally import SourceTally, TallyState
from .base import AbstractSwitcher
from .capabilities import get_available_sources, get_switcher_capabilities
from .http_client import HttpClient, HttpClientError


logger = logging.getLogger(__name__)


def _vmix_input_id(source_id: str) -> str:
    if source_id.startswith("Input "):
        return source_id.split(" ", 1)[1]
    return source_id


def _vmix_source_id(native_id: str | int | None) -> str | None:
    if native_id is None:
        return None
    text = str(native_id).strip()
    if not text:
        return None
    if text.startswith("Input "):
        return text
    return f"Input {text}"


@dataclass(frozen=True)
class VmixState:
    program_source_id: str | None
    preview_source_id: str | None


class VmixApiError(RuntimeError):
    """Raised when vMix XML cannot be parsed or returns unusable state."""


@dataclass
class VmixApiClient:
    """Small HTTP API wrapper around vMix /api endpoint.

    It intentionally exposes only the commands required by the controller.
    The underlying HttpClient owns retry, timeout and debug logging behaviour.
    """

    http: HttpClient

    def fetch_state(self) -> VmixState:
        response = self.http.get("/api")
        try:
            root = ET.fromstring(response.body)
        except ET.ParseError as exc:
            raise VmixApiError(f"Invalid vMix XML response: {exc}") from exc
        return VmixState(
            program_source_id=_vmix_source_id(root.findtext("active")),
            preview_source_id=_vmix_source_id(root.findtext("preview")),
        )

    def preview_input(self, source_id: str) -> None:
        self.http.get("/api", {"Function": "PreviewInput", "Input": _vmix_input_id(source_id)})

    def cut(self) -> None:
        self.http.get("/api", {"Function": "Cut"})

    def fade(self) -> None:
        self.http.get("/api", {"Function": "Fade"})


@dataclass
class VmixSwitcher(AbstractSwitcher):
    http: HttpClient
    connected: bool = False
    last_error: str | None = None
    program_source_id: str | None = None
    preview_source_id: str | None = None
    transition_log: list[str] = field(default_factory=list)
    api: VmixApiClient = field(init=False)

    def __post_init__(self) -> None:
        self.api = VmixApiClient(self.http)

    @property
    def capabilities(self) -> SwitcherCapabilities:
        return get_switcher_capabilities(SwitcherType.VMIX)

    def connect(self) -> None:
        try:
            self.poll()
        except Exception as exc:
            self.connected = False
            self.last_error = str(exc)
            logger.debug("vMix connect failed safely: %s", exc)
            return
        self.connected = True
        self.last_error = None

    def disconnect(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.disconnect()
        self.connect()

    def is_connected(self) -> bool:
        return self.connected

    def get_status(self) -> SwitcherStatus:
        if self.connected:
            state = SwitcherConnectionState.CONNECTED
            message = "connected"
        elif self.last_error:
            state = SwitcherConnectionState.ERROR
            message = self.last_error
        else:
            state = SwitcherConnectionState.DISCONNECTED
            message = "disconnected"
        return SwitcherStatus(type=SwitcherType.VMIX.value, state=state, message=message)

    def get_available_sources(self) -> tuple[Source, ...]:
        return get_available_sources(SwitcherType.VMIX)

    def _require_source(self, source_id: str) -> None:
        if source_id not in {source.id for source in self.get_available_sources()}:
            raise UnsupportedSourceError(source_id)

    def poll(self) -> None:
        try:
            state = self.api.fetch_state()
            self.program_source_id = state.program_source_id
            self.preview_source_id = state.preview_source_id
            self.connected = True
            self.last_error = None
        except (VmixApiError, HttpClientError) as exc:
            self.connected = False
            self.last_error = str(exc)
            raise

    def poll_program(self) -> str | None:
        self.poll()
        return self.program_source_id

    def poll_preview(self) -> str | None:
        self.poll()
        return self.preview_source_id

    def get_program_source(self) -> str | None:
        return self.program_source_id

    def get_preview_source(self) -> str | None:
        return self.preview_source_id

    def set_preview_source(self, source_id: str) -> None:
        self._require_source(source_id)
        self.api.preview_input(source_id)
        self.preview_source_id = source_id
        self.connected = True
        self.last_error = None

    def cut(self) -> None:
        self.api.cut()
        self.transition_log.append("cut")
        self.program_source_id, self.preview_source_id = self.preview_source_id, self.program_source_id
        self.connected = True
        self.last_error = None

    def auto(self) -> None:
        self.fade()

    def fade(self) -> None:
        self.api.fade()
        self.transition_log.append("fade")
        self.program_source_id, self.preview_source_id = self.preview_source_id, self.program_source_id
        self.connected = True
        self.last_error = None

    def copy_program_to_preview(self) -> None:
        if self.program_source_id is None:
            return
        self.set_preview_source(self.program_source_id)

    def get_tally_state(self) -> tuple[SourceTally, ...]:
        tally: list[SourceTally] = []
        for source in self.get_available_sources():
            state = TallyState.OFF
            if source.id == self.program_source_id:
                state = TallyState.PROGRAM
            elif source.id == self.preview_source_id:
                state = TallyState.PREVIEW
            tally.append(SourceTally(source_id=source.id, state=state))
        return tuple(tally)
