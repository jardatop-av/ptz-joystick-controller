from .base import AbstractSwitcher
from .capabilities import get_available_sources, get_source_ids, get_switcher_capabilities
from .factory import create_offline_switcher, create_switcher
from .fake import FakeSwitcher

__all__ = [
    "AbstractSwitcher",
    "FakeSwitcher",
    "create_offline_switcher",
    "create_switcher",
    "get_available_sources",
    "get_source_ids",
    "get_switcher_capabilities",
]
