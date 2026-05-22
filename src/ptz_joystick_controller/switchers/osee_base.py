from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import json

from ..models.sources import Source, UnsupportedSourceError
from ..models.switcher import SwitcherCapabilities, SwitcherConnectionState, SwitcherStatus, SwitcherType
from ..models.tally import SourceTally, TallyState
from .base import AbstractSwitcher
from .capabilities import get_available_sources, get_switcher_capabilities
from .http_client import HttpClient, HttpClientError


@dataclass(frozen=True)
class OseeApiProfile:
    status_path: str = "/api/status"
    command_path: str = "/api/command"
    preview_command: str = "set_preview"
    cut_command: str = "cut"
    auto_command: str = "auto"


@dataclass
class OseeSwitcherBase(AbstractSwitcher):
    switcher_type: SwitcherType
    http: HttpClient
    profile: OseeApiProfile = field(default_factory=OseeApiProfile)
    connected: bool = False
    last_error: str | None = None
    program_source_id: str | None = None
    preview_source_id: str | None = None
    transition_log: list[str] = field(default_factory=list)

    @property
    def capabilities(self) -> SwitcherCapabilities:
        return get_switcher_capabilities(self.switcher_type)

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
        return SwitcherStatus(type=self.switcher_type.value, state=state, message=message)

    def get_available_sources(self) -> tuple[Source, ...]:
        return get_available_sources(self.switcher_type)

    def _source_ids(self) -> set[str]:
        return {source.id for source in self.get_available_sources()}

    def _require_source(self, source_id: str) -> None:
        if source_id not in self._source_ids():
            raise UnsupportedSourceError(source_id)

    def _send_command(self, command: str, **payload: Any) -> None:
        body = json.dumps({"command": command, **payload}).encode("utf-8")
        self.http.post(self.profile.command_path, body=body)

    def poll(self) -> None:
        try:
            response = self.http.get(self.profile.status_path)
            data = json.loads(response.body or "{}")
            self.program_source_id = data.get("program") or data.get("program_source")
            self.preview_source_id = data.get("preview") or data.get("preview_source")
            self.connected = True
            self.last_error = None
        except (json.JSONDecodeError, HttpClientError) as exc:
            self.connected = False
            self.last_error = str(exc)
            raise

    def get_program_source(self) -> str | None:
        return self.program_source_id

    def get_preview_source(self) -> str | None:
        return self.preview_source_id

    def set_preview_source(self, source_id: str) -> None:
        self._require_source(source_id)
        self._send_command(self.profile.preview_command, source=source_id)
        self.preview_source_id = source_id

    def cut(self) -> None:
        self._send_command(self.profile.cut_command)
        self.transition_log.append("cut")
        self.program_source_id, self.preview_source_id = self.preview_source_id, self.program_source_id

    def auto(self) -> None:
        self._send_command(self.profile.auto_command)
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
