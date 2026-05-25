from __future__ import annotations

from dataclasses import dataclass, field

from .config import ControllerConfig
from .compat.vmix_compat import normalize_vmix_source_id
from .models.sources import SourceSelector, UnsupportedSourceError
from .models.switcher import SwitcherType
from .switchers.capabilities import get_source_ids


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
        return SourceSelector(
            self.config.sources,
            supported_source_ids=self.valid_switcher_source_ids(),
            normalize_source_id=lambda value: self.normalize_source_id(value),
        )

    def normalize_source_id(self, source_id: str | None) -> str | None:
        """Normalize source IDs according to the active switcher type.

        This is intentionally separate from PTZ mapping. A source may be valid
        for switcher control while having no mapped PTZ camera.
        """

        if source_id is None:
            return None
        if self.config.switcher.type == SwitcherType.VMIX.value:
            return normalize_vmix_source_id(source_id)
        return source_id.strip() if isinstance(source_id, str) else source_id

    def valid_switcher_source_ids(self) -> set[str]:
        """Return source IDs that are valid for switcher control.

        Configured source mappings are PTZ mappings, not the complete switcher
        input list. For vMix, for example, Input 1-100 must remain valid even
        when only a few inputs are mapped to PTZ cameras.
        """

        configured_ids = self.config.sources.source_ids()
        try:
            switcher_ids = set(get_source_ids(self.config.switcher.type))
        except Exception:
            switcher_ids = set()
        return configured_ids | switcher_ids

    def is_supported_switcher_source(self, source_id: str) -> bool:
        return self.source_selector.is_supported_source(source_id)

    def require_supported_source(self, source_id: str) -> str:
        return self.source_selector.require_supported_preview_source(source_id).source_id

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
