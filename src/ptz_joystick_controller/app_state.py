from __future__ import annotations

from dataclasses import dataclass, field

from .config import ControllerConfig
from .models.sources import SourceSelector, UnsupportedSourceError


@dataclass
class AppState:
    config: ControllerConfig
    program_source_id: str | None = None
    preview_source_id: str | None = None
    active_ptz_camera_id: str | None = None
    joystick_connected: bool = False
    switcher_connected: bool = False
    last_error: str | None = None
    stop_requests: list[str] = field(default_factory=list)

    @property
    def preview_source(self) -> str | None:
        return self.preview_source_id

    @property
    def program_source(self) -> str | None:
        return self.program_source_id

    @property
    def source_selector(self) -> SourceSelector:
        return SourceSelector(self.config.sources)

    def require_supported_source(self, source_id: str) -> None:
        self.source_selector.require_supported_preview_source(source_id)

    def recompute_active_ptz(self) -> str | None:
        if self.preview_source_id is None:
            self.active_ptz_camera_id = None
            return None
        try:
            camera_id = self.source_selector.active_ptz_for_preview(self.preview_source_id)
        except UnsupportedSourceError:
            camera_id = None
        self.active_ptz_camera_id = camera_id
        return self.active_ptz_camera_id
