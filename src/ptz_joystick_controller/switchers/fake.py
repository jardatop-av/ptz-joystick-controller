from __future__ import annotations

from dataclasses import dataclass, field

from ..models.sources import Source, UnsupportedSourceError
from ..models.switcher import SwitcherCapabilities, SwitcherConnectionState, SwitcherStatus, SwitcherType
from ..models.tally import SourceTally, TallyState
from .base import AbstractSwitcher
from .capabilities import get_available_sources, get_switcher_capabilities


@dataclass
class FakeSwitcher(AbstractSwitcher):
    """Offline switcher backend for tests and hardware-free development."""

    switcher_type: SwitcherType
    connected: bool = False
    program_source_id: str | None = None
    preview_source_id: str | None = None
    transition_log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        sources = self.get_available_sources()
        if self.program_source_id is None and sources:
            self.program_source_id = sources[0].id
        if self.preview_source_id is None and len(sources) > 1:
            self.preview_source_id = sources[1].id
        elif self.preview_source_id is None and sources:
            self.preview_source_id = sources[0].id

    @property
    def capabilities(self) -> SwitcherCapabilities:
        return get_switcher_capabilities(self.switcher_type)

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.disconnect()
        self.connect()

    def is_connected(self) -> bool:
        return self.connected

    def get_status(self) -> SwitcherStatus:
        return SwitcherStatus(
            type=self.switcher_type.value,
            state=SwitcherConnectionState.CONNECTED if self.connected else SwitcherConnectionState.DISCONNECTED,
            message="offline fake switcher",
        )

    def get_available_sources(self) -> tuple[Source, ...]:
        return get_available_sources(self.switcher_type)

    def _source_ids(self) -> set[str]:
        return {source.id for source in self.get_available_sources()}

    def _require_source(self, source_id: str) -> None:
        if source_id not in self._source_ids():
            raise UnsupportedSourceError(source_id)

    def get_program_source(self) -> str | None:
        return self.program_source_id

    def get_preview_source(self) -> str | None:
        return self.preview_source_id

    def set_preview_source(self, source_id: str) -> None:
        self._require_source(source_id)
        self.preview_source_id = source_id

    def cut(self) -> None:
        self.transition_log.append("cut")
        self.program_source_id, self.preview_source_id = self.preview_source_id, self.program_source_id

    def auto(self) -> None:
        self.transition_log.append("auto")
        self.program_source_id, self.preview_source_id = self.preview_source_id, self.program_source_id

    def copy_program_to_preview(self) -> None:
        self.preview_source_id = self.program_source_id

    def get_tally_state(self) -> tuple[SourceTally, ...]:
        tally: list[SourceTally] = []
        for source in self.get_available_sources():
            if source.id == self.program_source_id:
                state = TallyState.PROGRAM
            elif source.id == self.preview_source_id:
                state = TallyState.PREVIEW
            else:
                state = TallyState.OFF
            tally.append(SourceTally(source_id=source.id, state=state))
        return tuple(tally)
