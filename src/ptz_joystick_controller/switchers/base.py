from __future__ import annotations

from abc import ABC, abstractmethod

from ..models.sources import Source
from ..models.switcher import SwitcherCapabilities, SwitcherStatus
from ..models.tally import TallyState


class AbstractSwitcher(ABC):
    """Offline-safe switcher contract. Implementations may talk to hardware later."""

    @property
    @abstractmethod
    def capabilities(self) -> SwitcherCapabilities:
        raise NotImplementedError

    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def reconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_connected(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_status(self) -> SwitcherStatus:
        raise NotImplementedError

    @abstractmethod
    def get_available_sources(self) -> tuple[Source, ...]:
        raise NotImplementedError

    @abstractmethod
    def get_program_source(self) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def get_preview_source(self) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def set_preview_source(self, source_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def cut(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def auto(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def copy_program_to_preview(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_tally_state(self) -> TallyState:
        raise NotImplementedError
