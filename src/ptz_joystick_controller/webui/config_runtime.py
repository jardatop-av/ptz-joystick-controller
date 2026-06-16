from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import ControllerConfig, ConfigError, load_config
from .config_editor import ConfigEditError, validate_enabled_ptz_camera_hosts_in_mapping

if TYPE_CHECKING:  # pragma: no cover
    from .status import RuntimeStatusProvider


@dataclass
class RuntimeConfigApplyStatus:
    loaded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_apply_result: str | None = None
    last_apply_error: str | None = None
    last_apply_at: datetime | None = None
    local_config_path: Path | None = None

    def as_dict(self, *, pending_changes: bool = False) -> dict[str, object]:
        return {
            "loaded_at": self.loaded_at.isoformat(),
            "pending_changes": pending_changes,
            "last_apply_result": self.last_apply_result,
            "last_apply_error": self.last_apply_error,
            "last_apply_at": self.last_apply_at.isoformat() if self.last_apply_at else None,
            "local_config_path": str(self.local_config_path) if self.local_config_path else None,
        }


@dataclass
class RuntimeConfigApplier:
    """Safely reload config.local.yaml into the live runtime.

    It validates the full merged configuration before touching runtime state. If
    validation fails, the current runtime config remains unchanged.
    """

    status_provider: "RuntimeStatusProvider"
    example_config_path: Path
    local_config_path: Path
    status: RuntimeConfigApplyStatus = field(default_factory=RuntimeConfigApplyStatus)

    def __post_init__(self) -> None:
        self.status.local_config_path = self.local_config_path

    def has_pending_changes(self) -> bool:
        if not self.local_config_path.exists():
            return False
        local_mtime = datetime.fromtimestamp(self.local_config_path.stat().st_mtime, timezone.utc)
        return local_mtime > self.status.loaded_at

    def load_validated_config(self) -> ControllerConfig:
        if self.local_config_path.exists():
            import yaml

            local_data = yaml.safe_load(self.local_config_path.read_text(encoding="utf-8")) or {}
            if not isinstance(local_data, dict):
                raise ConfigError("Config root must be a mapping")
            try:
                validate_enabled_ptz_camera_hosts_in_mapping(local_data)
            except ConfigEditError as exc:
                raise ConfigError(str(exc)) from exc
        return load_config(self.example_config_path, local_path=self.local_config_path)

    def apply_loaded_config(self, new_config: ControllerConfig) -> dict[str, object]:
        bridge = getattr(self.status_provider, "runtime_bridge", None)
        try:
            if bridge is not None:
                bridge.apply_config(new_config)
                # Keep provider references in sync with newly rebuilt runtime objects.
                self.status_provider.state = bridge.state
                self.status_provider.switcher = bridge.switcher
                self.status_provider.ptz_router = bridge.ptz_router
                self.status_provider.joystick_health = bridge.joystick_monitor.health
                self.status_provider.joystick_monitor = bridge.joystick_monitor
            else:
                if self.status_provider.ptz_router is not None:
                    self.status_provider.ptz_router.stop_all_active_motion(reason="config_apply")
                self.status_provider.state.config = new_config
                if self.status_provider.joystick_monitor is not None:
                    self.status_provider.joystick_monitor.config = new_config
                if self.status_provider.ptz_router is not None:
                    self.status_provider.ptz_router.rebuild_sessions()
            self.status.loaded_at = datetime.now(timezone.utc)
            self.status.last_apply_at = self.status.loaded_at
            self.status.last_apply_result = "ok"
            self.status.last_apply_error = None
            self.status_provider.config_apply_status = self.status
            self.status_provider.event_bus.publish("config.applied", {"result": "ok"}) if self.status_provider.event_bus else None
            return {"status": "applied", "message": "Configuration applied."}
        except Exception as exc:
            self.status.last_apply_at = datetime.now(timezone.utc)
            self.status.last_apply_result = "error"
            self.status.last_apply_error = str(exc)
            self.status_provider.config_apply_status = self.status
            raise

    def apply_from_disk(self) -> dict[str, object]:
        try:
            new_config = self.load_validated_config()
        except ConfigError as exc:
            self.status.last_apply_at = datetime.now(timezone.utc)
            self.status.last_apply_result = "error"
            self.status.last_apply_error = str(exc)
            self.status_provider.config_apply_status = self.status
            raise
        return self.apply_loaded_config(new_config)
