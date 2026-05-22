from __future__ import annotations

from ..models.switcher import SwitcherConfig, SwitcherType
from .base import AbstractSwitcher
from .fake import FakeSwitcher


def create_offline_switcher(config: SwitcherConfig) -> AbstractSwitcher:
    return FakeSwitcher(switcher_type=SwitcherType(config.type))


def create_switcher(config: SwitcherConfig, *, offline: bool = True) -> AbstractSwitcher:
    if offline:
        return create_offline_switcher(config)
    raise NotImplementedError("Real switcher protocols are not implemented yet")
