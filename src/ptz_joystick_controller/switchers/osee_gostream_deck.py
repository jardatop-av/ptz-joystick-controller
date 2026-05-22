from __future__ import annotations

from dataclasses import dataclass

from ..models.switcher import SwitcherType
from .http_client import HttpClient
from .osee_base import OseeApiProfile, OseeSwitcherBase


@dataclass
class OseeGoStreamDeckSwitcher(OseeSwitcherBase):
    def __init__(self, http: HttpClient, profile: OseeApiProfile | None = None) -> None:
        super().__init__(
            switcher_type=SwitcherType.OSEE_GOSTREAM_DECK,
            http=http,
            profile=profile or OseeApiProfile(),
        )
