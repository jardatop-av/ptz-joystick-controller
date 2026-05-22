from __future__ import annotations

from dataclasses import dataclass, field
import xml.etree.ElementTree as ET

from ..models.sources import Source, UnsupportedSourceError
from ..models.switcher import SwitcherCapabilities, SwitcherConnectionState, SwitcherStatus, SwitcherType
from ..models.tally import SourceTally, TallyState
from .base import AbstractSwitcher
from .capabilities import get_available_sources, get_switcher_capabilities
from .http_client import HttpClient, HttpClientError


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
    return f"Input {text}"


@dataclass
class VmixSwitcher(AbstractSwitcher):
    http: HttpClient
    connected: bool = False
    last_error: str | None = None
    program_source_id: str | None = None
    preview_source_id: str | None = None
    transition_log: list[str] = field(default_factory=list)

    @property
    def capabilities(self) -> SwitcherCapabilities:
        return get_switcher_capabilities(SwitcherType.VMIX)

    def connect(self) -> None:
        self.poll()
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
            response = self.http.get("/api")
            root = ET.fromstring(response.body)
            self.program_source_id = _vmix_source_id(root.findtext("active"))
            self.preview_source_id = _vmix_source_id(root.findtext("preview"))
            self.connected = True
            self.last_error = None
        except (ET.ParseError, HttpClientError) as exc:
            self.connected = False
            self.last_error = str(exc)
            raise

    def get_program_source(self) -> str | None:
        return self.program_source_id

    def get_preview_source(self) -> str | None:
        return self.preview_source_id

    def set_preview_source(self, source_id: str) -> None:
        self._require_source(source_id)
        native = _vmix_input_id(source_id)
        self.http.get("/api", {"Function": "PreviewInput", "Input": native})
        self.preview_source_id = source_id

    def cut(self) -> None:
        self.http.get("/api", {"Function": "Cut"})
        self.transition_log.append("cut")
        self.program_source_id, self.preview_source_id = self.preview_source_id, self.program_source_id

    def auto(self) -> None:
        self.http.get("/api", {"Function": "Fade"})
        self.transition_log.append("auto")
        self.program_source_id, self.preview_source_id = self.preview_source_id, self.program_source_id

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
