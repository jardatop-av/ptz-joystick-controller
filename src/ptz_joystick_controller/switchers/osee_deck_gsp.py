from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import threading
import time
from typing import Protocol

from .osee_gsp import GspCommand, GspTransportError


DECK_SOURCE_TO_GSP_ID: dict[str, int] = {
    "Input 1": 1,
    "Input 2": 2,
    "Input 3": 3,
    "Input 4": 4,
    "AUX": 4001,
    "STILL1": 3010,
    "STILL2": 3020,
    "S/SRC": 5001,
}
DECK_GSP_ID_TO_SOURCE: dict[int, str] = {value: key for key, value in DECK_SOURCE_TO_GSP_ID.items()}


class OseeDeckSourceError(ValueError):
    """Raised when a logical GoStream Deck source cannot be resolved."""


@dataclass(frozen=True, slots=True)
class OseeDeckSourceRef:
    gsp_id: int
    canonical_id: str | None

    @property
    def known(self) -> bool:
        return self.canonical_id is not None

    @property
    def display_name(self) -> str:
        return self.canonical_id or f"Unknown GSP source {self.gsp_id}"


@dataclass(slots=True)
class OseeDeckState:
    preview: OseeDeckSourceRef | None = None
    program: OseeDeckSourceRef | None = None
    transition_status: tuple[int | float | str, ...] | None = None


@dataclass(frozen=True, slots=True)
class OseeDeckStateSnapshot:
    program: str | None
    preview: str | None
    transition: str


class OseeDeckTransport(Protocol):
    connected: bool

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def send_command(self, command: GspCommand) -> bytes: ...
    def send_get(self, command_id: str) -> bytes: ...
    def receive(self) -> tuple[GspCommand, ...]: ...


class OseeDeckSourceMap:
    """Deck-specific logical source mapping.

    Numeric IDs are shared protocol values, but their logical meaning is model
    specific and must not be inferred from the GoStream Duet capability map.
    """

    @staticmethod
    def normalize(source: str | int) -> str:
        if isinstance(source, bool):
            raise OseeDeckSourceError(f"Unsupported GoStream Deck source: {source!r}")
        if isinstance(source, int):
            if 1 <= source <= 4:
                return f"Input {source}"
            raise OseeDeckSourceError(f"Input number must be in range 1..4: {source}")

        raw = source.strip()
        if not raw:
            raise OseeDeckSourceError("GoStream Deck source must not be empty")
        compact = re.sub(r"[\s_-]+", "", raw).upper()
        if compact.isdigit():
            number = int(compact)
            if 1 <= number <= 4:
                return f"Input {number}"
        match = re.fullmatch(r"INPUT([1-4])", compact)
        if match:
            return f"Input {match.group(1)}"
        if compact == "AUX":
            return "AUX"
        if compact in {"STILL1", "STILL01"}:
            return "STILL1"
        if compact in {"STILL2", "STILL02"}:
            return "STILL2"
        # S/SRC is the verified physical GoStream Deck MultiSource label.
        if compact in {"S/SRC", "SSRC", "MULTISOURCE"}:
            return "S/SRC"
        raise OseeDeckSourceError(f"Unsupported GoStream Deck source: {source!r}")

    @classmethod
    def to_gsp_id(cls, source: str | int) -> int:
        return DECK_SOURCE_TO_GSP_ID[cls.normalize(source)]

    @staticmethod
    def from_gsp_id(gsp_id: int) -> OseeDeckSourceRef:
        return OseeDeckSourceRef(gsp_id=gsp_id, canonical_id=DECK_GSP_ID_TO_SOURCE.get(gsp_id))

    @staticmethod
    def available_sources() -> tuple[str, ...]:
        return tuple(DECK_SOURCE_TO_GSP_ID)


class OseeDeckGspController:
    """Isolated GoStream Deck GSP state/write controller for Stage58."""

    def __init__(self, transport: OseeDeckTransport, *, logger: logging.Logger | None = None) -> None:
        self.transport = transport
        self.state = OseeDeckState()
        self._logger = logger or logging.getLogger(__name__)

    def set_preview(self, source: str | int) -> bytes:
        source_id = OseeDeckSourceMap.to_gsp_id(source)
        return self.transport.send_command(GspCommand(id="pvwIndex", type="set", value=(source_id,)))

    def cut(self) -> bytes:
        return self.transport.send_command(GspCommand(id="cutTransition", type="set"))

    def auto(self) -> bytes:
        return self.transport.send_command(GspCommand(id="autoTransition", type="set"))

    def handle_command(self, command: GspCommand) -> bool:
        if command.type not in ("get", "pus", "res"):
            return False
        if command.id == "pvwIndex":
            source_id = self._first_int(command)
            if source_id is None:
                return False
            self.state.preview = self._source_ref(source_id, "preview")
            return True
        if command.id == "pgmIndex":
            source_id = self._first_int(command)
            if source_id is None:
                return False
            self.state.program = self._source_ref(source_id, "program")
            return True
        if command.id == "transitionStatus":
            if command.value is None:
                self._logger.debug("GoStream Deck ignored malformed transitionStatus value=None")
                return False
            self.state.transition_status = command.value
            return True
        if command.id in {"pgmTally", "pvwTally"}:
            self._logger.debug("GoStream Deck tally update %s=%s", command.id, command.value)
            return False
        self._logger.debug("GoStream Deck ignored GSP id=%s type=%s", command.id, command.type)
        return False

    def _source_ref(self, source_id: int, role: str) -> OseeDeckSourceRef:
        source = OseeDeckSourceMap.from_gsp_id(source_id)
        if not source.known:
            self._logger.warning("GoStream Deck reported unknown %s GSP source id=%s", role, source_id)
        return source

    def _first_int(self, command: GspCommand) -> int | None:
        if not command.value or isinstance(command.value[0], bool) or not isinstance(command.value[0], int):
            self._logger.warning("GoStream Deck ignored malformed %s value=%s", command.id, command.value)
            return None
        return command.value[0]


class OseeDeckManualControlClient:
    """Thread-safe isolated manual control client.

    Exactly one background receiver calls ``transport.receive()``. Interactive
    callers send commands and wait on feedback counters/state updated only by
    incoming GSP messages.
    """

    def __init__(
        self,
        transport: OseeDeckTransport,
        *,
        feedback_timeout: float = 2.0,
        logger: logging.Logger | None = None,
    ) -> None:
        if feedback_timeout <= 0:
            raise ValueError("feedback_timeout must be positive")
        self.transport = transport
        self.controller = OseeDeckGspController(transport, logger=logger)
        self.feedback_timeout = feedback_timeout
        self.logger = logger or logging.getLogger(__name__)
        self._condition = threading.Condition(threading.RLock())
        self._stop_event = threading.Event()
        self._receiver_thread: threading.Thread | None = None
        self._receiver_error: str | None = None
        self._preview_feedback_count = 0
        self._program_feedback_count = 0
        self._transition_feedback_count = 0
        self._transition_history: list[tuple[int | float | str, ...]] = []

    @property
    def receiver_alive(self) -> bool:
        thread = self._receiver_thread
        return bool(thread and thread.is_alive())

    @property
    def receiver_error(self) -> str | None:
        with self._condition:
            return self._receiver_error

    def connect(self) -> None:
        self.transport.connect()
        self._stop_event.clear()
        with self._condition:
            self._receiver_error = None
        self._receiver_thread = threading.Thread(
            target=self._receive_loop,
            name="osee-gostream-deck-manual-rx",
            daemon=True,
        )
        self._receiver_thread.start()
        for command_id in ("pgmIndex", "pvwIndex", "transitionStatus"):
            self.transport.send_get(command_id)

    def disconnect(self) -> None:
        self._stop_event.set()
        self.transport.disconnect()
        with self._condition:
            self._condition.notify_all()
        thread = self._receiver_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.2, min(2.0, self.feedback_timeout + 0.2)))
        self._receiver_thread = None

    def snapshot(self) -> OseeDeckStateSnapshot:
        with self._condition:
            program = self.controller.state.program
            preview = self.controller.state.preview
            transition = self._transition_display(self.controller.state.transition_status)
            return OseeDeckStateSnapshot(
                program=program.display_name if program is not None else None,
                preview=preview.display_name if preview is not None else None,
                transition=transition,
            )

    def wait_for_initial_state(self, timeout: float | None = None) -> bool:
        deadline = self.feedback_timeout if timeout is None else timeout
        with self._condition:
            return self._condition.wait_for(
                lambda: (
                    self.controller.state.program is not None
                    and self.controller.state.preview is not None
                    and self.controller.state.transition_status is not None
                ) or self._receiver_error is not None,
                timeout=max(0.0, deadline),
            ) and self._receiver_error is None

    def set_preview(self, source: str | int, timeout: float | None = None) -> bool:
        canonical = OseeDeckSourceMap.normalize(source)
        with self._condition:
            baseline = self._preview_feedback_count
        self.controller.set_preview(canonical)
        self.logger.info("Preview command sent: %s", canonical)
        return self._wait_for(
            lambda: (
                self._preview_feedback_count > baseline
                and self.controller.state.preview is not None
                and self.controller.state.preview.canonical_id == canonical
            ),
            timeout,
        )

    def cut(self, timeout: float | None = None) -> bool:
        with self._condition:
            program_baseline = self._program_feedback_count
            preview_baseline = self._preview_feedback_count
        self.controller.cut()
        self.logger.info("CUT sent")
        return self._wait_for(
            lambda: (
                self._program_feedback_count > program_baseline
                or self._preview_feedback_count > preview_baseline
            ),
            timeout,
        )

    def auto(self, timeout: float | None = None) -> tuple[bool, bool]:
        with self._condition:
            baseline = self._transition_feedback_count
        self.controller.auto()
        self.logger.info("AUTO sent")
        started = self._wait_for(
            lambda: self._transition_event_since(baseline, active=True),
            timeout,
        )
        if not started:
            return False, False
        completed = self._wait_for(
            lambda: self._transition_event_since(baseline, active=False, after_active=True),
            timeout,
        )
        return True, completed

    def copy_program_to_preview(self, timeout: float | None = None) -> tuple[str, bool]:
        with self._condition:
            program = self.controller.state.program
            if program is None:
                raise RuntimeError("Program state is not known yet")
            if not program.known or program.canonical_id is None:
                raise RuntimeError(f"Program source cannot be mapped: {program.display_name}")
            canonical = program.canonical_id
        return canonical, self.set_preview(canonical, timeout=timeout)

    def _wait_for(self, predicate, timeout: float | None) -> bool:
        wait_timeout = self.feedback_timeout if timeout is None else timeout
        with self._condition:
            matched = self._condition.wait_for(
                lambda: predicate() or self._receiver_error is not None or self._stop_event.is_set(),
                timeout=max(0.0, wait_timeout),
            )
            return bool(matched and predicate() and self._receiver_error is None)

    def _transition_event_since(self, baseline: int, *, active: bool, after_active: bool = False) -> bool:
        events = self._transition_history[baseline:]
        wanted = 1 if active else 0
        if not after_active:
            return any(self._first_status_value(event) == wanted for event in events)
        saw_active = False
        for event in events:
            value = self._first_status_value(event)
            if value == 1:
                saw_active = True
            elif value == 0 and saw_active:
                return True
        return False

    def _receive_loop(self) -> None:
        self.logger.debug("GoStream Deck RX loop started")
        try:
            while not self._stop_event.is_set():
                try:
                    commands = self.transport.receive()
                except GspTransportError as exc:
                    if self._stop_event.is_set():
                        break
                    with self._condition:
                        self._receiver_error = str(exc)
                        self._condition.notify_all()
                    self.logger.warning("GoStream Deck receive error: %s", exc)
                    break
                for command in commands:
                    self._handle_incoming(command)
        finally:
            self.logger.debug("GoStream Deck RX loop stopped")
            with self._condition:
                self._condition.notify_all()

    def _handle_incoming(self, command: GspCommand) -> None:
        with self._condition:
            old_program = self.controller.state.program.display_name if self.controller.state.program else None
            old_preview = self.controller.state.preview.display_name if self.controller.state.preview else None
            old_transition = self._transition_display(self.controller.state.transition_status)
            changed = self.controller.handle_command(command)
            if not changed:
                return

            if command.id == "pgmIndex":
                self._program_feedback_count += 1
            elif command.id == "pvwIndex":
                self._preview_feedback_count += 1
            elif command.id == "transitionStatus":
                self._transition_feedback_count += 1
                assert self.controller.state.transition_status is not None
                self._transition_history.append(self.controller.state.transition_status)

            new_program = self.controller.state.program.display_name if self.controller.state.program else None
            new_preview = self.controller.state.preview.display_name if self.controller.state.preview else None
            new_transition = self._transition_display(self.controller.state.transition_status)
            self._condition.notify_all()

        if new_program != old_program:
            self.logger.info("Program changed: %s", new_program)
        if new_preview != old_preview:
            self.logger.info("Preview changed: %s", new_preview)
        if new_transition != old_transition:
            if new_transition == "active":
                self.logger.info("Transition started")
            elif new_transition == "idle":
                self.logger.info("Transition completed")
            else:
                self.logger.info("Transition changed: %s", new_transition)

    @staticmethod
    def _first_status_value(value: tuple[int | float | str, ...] | None) -> int | float | str | None:
        return value[0] if value else None

    @classmethod
    def _transition_display(cls, value: tuple[int | float | str, ...] | None) -> str:
        first = cls._first_status_value(value)
        if first in (0, "0", "idle"):
            return "idle"
        if first in (1, "1", "active"):
            return "active"
        return "unknown" if value is None else str(list(value))
