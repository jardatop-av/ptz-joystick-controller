from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..models.sources import Source, UnsupportedSourceError
from ..models.switcher import SwitcherCapabilities, SwitcherConnectionState, SwitcherStatus, SwitcherType
from ..models.tally import SourceTally, TallyState
from .base import AbstractSwitcher
from .capabilities import get_available_sources, get_switcher_capabilities


class AtemCommandClient(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def poll(self) -> tuple[str | None, str | None]: ...
    def set_preview(self, source_id: str) -> None: ...
    def cut(self) -> None: ...
    def auto(self) -> None: ...


@dataclass
class AtemSwitcher(AbstractSwitcher):
    switcher_type: SwitcherType
    client: AtemCommandClient
    connected: bool = False
    last_error: str | None = None
    program_source_id: str | None = None
    preview_source_id: str | None = None
    transition_log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.switcher_type not in {SwitcherType.ATEM_MINI_PRO, SwitcherType.ATEM_TV_STUDIO_PRO_4K}:
            raise ValueError(f"Invalid ATEM switcher_type: {self.switcher_type}")

    @property
    def capabilities(self) -> SwitcherCapabilities:
        return get_switcher_capabilities(self.switcher_type)

    def connect(self) -> None:
        try:
            self.client.connect()
            self.poll()
            self.connected = True
            self.last_error = None
        except Exception as exc:
            self.connected = False
            self.last_error = str(exc)
            raise

    def disconnect(self) -> None:
        self.client.disconnect()
        self.connected = False

    def reconnect(self) -> None:
        self.disconnect()
        self.connect()

    def is_connected(self) -> bool:
        return self.connected

    def get_status(self) -> SwitcherStatus:
        if self.connected:
            return SwitcherStatus(type=self.switcher_type.value, state=SwitcherConnectionState.CONNECTED, message="connected")
        if self.last_error:
            return SwitcherStatus(type=self.switcher_type.value, state=SwitcherConnectionState.ERROR, message=self.last_error)
        return SwitcherStatus(type=self.switcher_type.value, state=SwitcherConnectionState.DISCONNECTED, message="disconnected")

    def get_available_sources(self) -> tuple[Source, ...]:
        return get_available_sources(self.switcher_type)

    def _require_source(self, source_id: str) -> None:
        if source_id not in {source.id for source in self.get_available_sources()}:
            raise UnsupportedSourceError(source_id)

    def poll(self) -> None:
        try:
            self.program_source_id, self.preview_source_id = self.client.poll()
            self.connected = True
            self.last_error = None
        except Exception as exc:
            self.connected = False
            self.last_error = str(exc)
            raise

    def get_program_source(self) -> str | None:
        return self.program_source_id

    def get_preview_source(self) -> str | None:
        return self.preview_source_id

    def set_preview_source(self, source_id: str) -> None:
        self._require_source(source_id)
        self.client.set_preview(source_id)
        self.preview_source_id = source_id

    def cut(self) -> None:
        self.client.cut()
        self.transition_log.append("cut")
        self.program_source_id, self.preview_source_id = self.preview_source_id, self.program_source_id

    def auto(self) -> None:
        self.client.auto()
        self.transition_log.append("auto")
        self.program_source_id, self.preview_source_id = self.preview_source_id, self.program_source_id

    def copy_program_to_preview(self) -> None:
        if self.program_source_id is None:
            return
        self.set_preview_source(self.program_source_id)

    def get_tally_state(self) -> tuple[SourceTally, ...]:
        return tuple(
            SourceTally(
                source_id=source.id,
                state=TallyState.PROGRAM
                if source.id == self.program_source_id
                else TallyState.PREVIEW
                if source.id == self.preview_source_id
                else TallyState.OFF,
            )
            for source in self.get_available_sources()
        )
