from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Protocol

from .osee_gsp import GspCommand


DUET_SOURCE_TO_GSP_ID: dict[str, int] = {
    "Input 1": 1,
    "Input 2": 2,
    "Input 3": 3,
    "Input 4": 4,
    "Input 5": 4001,
    "Input 6": 4002,
    "Input 7": 4003,
    "Input 8": 4004,
    "MP1": 3010,
    "MP2": 3020,
    "M/SRC": 5001,
}
DUET_GSP_ID_TO_SOURCE: dict[int, str] = {value: key for key, value in DUET_SOURCE_TO_GSP_ID.items()}


class OseeDuetSourceError(ValueError):
    """Raised when a logical Duet source name cannot be resolved."""


@dataclass(frozen=True, slots=True)
class OseeDuetSourceRef:
    """A source selected by the Duet, including unknown device-side IDs."""

    gsp_id: int
    canonical_id: str | None

    @property
    def known(self) -> bool:
        return self.canonical_id is not None

    @property
    def display_name(self) -> str:
        return self.canonical_id or f"Unknown GSP source {self.gsp_id}"


class OseeGspSender(Protocol):
    def send_command(self, command: GspCommand) -> bytes: ...


@dataclass(slots=True)
class OseeDuetState:
    preview: OseeDuetSourceRef | None = None
    program: OseeDuetSourceRef | None = None
    transition_status: tuple[int | float | str, ...] | None = None


class OseeDuetSourceMap:
    """Translate generic logical Duet positions to model-specific GSP IDs."""

    @staticmethod
    def normalize(source: str | int) -> str:
        if isinstance(source, bool):
            raise OseeDuetSourceError(f"Unsupported Duet source: {source!r}")
        if isinstance(source, int):
            if 1 <= source <= 8:
                return f"Input {source}"
            raise OseeDuetSourceError(f"Input number must be in range 1..8: {source}")

        raw = source.strip()
        if not raw:
            raise OseeDuetSourceError("Duet source must not be empty")
        compact = re.sub(r"[\s_-]+", "", raw).upper()
        if compact.isdigit():
            number = int(compact)
            if 1 <= number <= 8:
                return f"Input {number}"
        input_match = re.fullmatch(r"INPUT([1-8])", compact)
        if input_match:
            return f"Input {input_match.group(1)}"
        if compact == "MP1":
            return "MP1"
        if compact == "MP2":
            return "MP2"
        if compact in {"M/SRC", "MSRC"}:
            return "M/SRC"
        raise OseeDuetSourceError(f"Unsupported Duet source: {source!r}")

    @classmethod
    def to_gsp_id(cls, source: str | int) -> int:
        return DUET_SOURCE_TO_GSP_ID[cls.normalize(source)]

    @staticmethod
    def from_gsp_id(gsp_id: int) -> OseeDuetSourceRef:
        return OseeDuetSourceRef(gsp_id=gsp_id, canonical_id=DUET_GSP_ID_TO_SOURCE.get(gsp_id))

    @staticmethod
    def available_sources() -> tuple[str, ...]:
        return tuple(DUET_SOURCE_TO_GSP_ID)


class OseeDuetGspController:
    """Isolated Duet 8 ISO GSP control/state layer.

    It is deliberately not wired into the main runtime switcher adapter yet.
    """

    def __init__(self, transport: OseeGspSender, *, logger: logging.Logger | None = None) -> None:
        self.transport = transport
        self.state = OseeDuetState()
        self._logger = logger or logging.getLogger(__name__)

    def set_preview(self, source: str | int) -> bytes:
        source_id = OseeDuetSourceMap.to_gsp_id(source)
        return self.transport.send_command(GspCommand(id="pvwIndex", type="set", value=(source_id,)))

    def set_program(self, source: str | int) -> bytes:
        source_id = OseeDuetSourceMap.to_gsp_id(source)
        return self.transport.send_command(GspCommand(id="pgmIndex", type="set", value=(source_id,)))

    def cut(self) -> bytes:
        return self.transport.send_command(GspCommand(id="cutTransition", type="set"))

    def auto(self) -> bytes:
        return self.transport.send_command(GspCommand(id="autoTransition", type="set"))

    def handle_command(self, command: GspCommand) -> bool:
        """Apply a get/push state message. Return True if tracked state changed."""
        if command.type not in ("get", "pus"):
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
            self.state.transition_status = command.value
            return True
        if command.id in {"pgmTally", "pvwTally"}:
            self._logger.debug("Osee Duet tally update %s=%s", command.id, command.value)
            return False
        self._logger.debug("Osee Duet ignored unknown GSP command id=%s type=%s", command.id, command.type)
        return False

    def _source_ref(self, source_id: int, role: str) -> OseeDuetSourceRef:
        source = OseeDuetSourceMap.from_gsp_id(source_id)
        if not source.known:
            self._logger.warning("Osee Duet reported unknown %s GSP source id=%s", role, source_id)
        return source

    def _first_int(self, command: GspCommand) -> int | None:
        if not command.value or isinstance(command.value[0], bool) or not isinstance(command.value[0], int):
            self._logger.warning("Osee Duet ignored malformed %s value=%s", command.id, command.value)
            return None
        return command.value[0]
