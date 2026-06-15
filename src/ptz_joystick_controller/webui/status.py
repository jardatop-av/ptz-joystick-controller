from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TYPE_CHECKING

from ..app_state import AppState
from ..event_bus import Event, EventBus
from ..joystick.calibration import JoystickCalibration
from ..models.joystick_runtime import JoystickConnectionState, JoystickHealth
from ..models.switcher import SwitcherConnectionState
from ..runtime.ptz_router import PtzRouter
from ..switchers.base import AbstractSwitcher
from ..version import __version__

if TYPE_CHECKING:
    from ..joystick.runtime import JoystickRuntimeMonitor
    from ..runtime.joystick_switcher_bridge import JoystickToSwitcherBridge

try:
    from ..version import __stage__
except ImportError:  # pragma: no cover - compatibility for older archives
    __stage__ = "unknown"


@dataclass
class RuntimeStatusProvider:
    """Build read-only web status from live runtime state objects.

    The provider intentionally does not parse log files. Runtime producers may
    publish events into the EventBus and update AppState/JoystickHealth/etc.;
    this class only formats those objects for the read-only dashboard.
    """

    state: AppState
    event_bus: EventBus | None = None
    joystick_health: JoystickHealth = field(default_factory=JoystickHealth)
    joystick_monitor: "JoystickRuntimeMonitor | None" = None
    switcher: AbstractSwitcher | None = None
    ptz_router: PtzRouter | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    max_recent_events: int = 20
    _recent_events: list[Event] = field(default_factory=list, init=False)


    @classmethod
    def from_bridge(cls, bridge: "JoystickToSwitcherBridge", *, max_recent_events: int = 20) -> "RuntimeStatusProvider":
        """Create a status provider backed by a live runtime bridge.

        This intentionally stores references to the live state objects instead
        of copying values. Every /api/status request therefore reflects the
        latest joystick, switcher, preview/program and PTZ router state without
        restarting the dashboard process.
        """

        return cls(
            state=bridge.state,
            event_bus=bridge.event_bus,
            joystick_health=bridge.joystick_monitor.health,
            joystick_monitor=bridge.joystick_monitor,
            switcher=bridge.switcher,
            ptz_router=bridge.ptz_router,
            max_recent_events=max_recent_events,
        )

    def __post_init__(self) -> None:
        if self.event_bus is not None:
            self.event_bus.subscribe_all(self.record_event)

    def record_event(self, event: Event) -> None:
        self._recent_events.insert(0, event)
        del self._recent_events[self.max_recent_events :]

    @property
    def uptime_seconds(self) -> float:
        return max(0.0, (datetime.now(timezone.utc) - self.started_at).total_seconds())

    def _normalized_axes(self) -> dict[str, float]:
        snapshot = self.joystick_health.last_snapshot
        normalized = (
            self.joystick_monitor.normalized_axes(snapshot)
            if self.joystick_monitor is not None
            else JoystickCalibration().normalize_axes(snapshot.axes)
        )
        return {
            "pan": round(normalized.pan, 4),
            "tilt": round(normalized.tilt, 4),
            "zoom": round(normalized.zoom, 4),
            "throttle": round(normalized.throttle, 4),
        }

    def joystick_status(self) -> dict[str, Any]:
        snapshot = self.joystick_health.last_snapshot
        device = self.joystick_health.device
        return {
            "connected": self.joystick_health.connected,
            "state": self.joystick_health.state.value,
            "device_name": device.name if device is not None else None,
            "device_path": device.path if device is not None else None,
            "backend": device.backend if device is not None else None,
            "pressed_buttons": sorted(snapshot.pressed_buttons),
            "hat": {
                "x": snapshot.hat.x,
                "y": snapshot.hat.y,
                "direction": snapshot.hat.direction.value,
            },
            "normalized_axes": self._normalized_axes(),
            "last_error": self.joystick_health.last_error,
        }

    def switcher_status(self) -> dict[str, Any]:
        if self.switcher is None:
            return {
                "connected": self.state.switcher_connected,
                "state": SwitcherConnectionState.CONNECTED.value if self.state.switcher_connected else SwitcherConnectionState.DISCONNECTED.value,
                "type": self.state.config.switcher.type,
                "message": None,
                "program_source": self.state.program_source_id,
                "preview_source": self.state.preview_source_id,
            }
        status = self.switcher.get_status()
        return {
            "connected": self.switcher.is_connected(),
            "state": status.state.value,
            "type": status.type,
            "message": status.message,
            "program_source": self.switcher.get_program_source() or self.state.program_source_id,
            "preview_source": self.switcher.get_preview_source() or self.state.preview_source_id,
        }

    def ptz_status(self) -> dict[str, Any]:
        diagnostics = self.ptz_router.diagnostics() if self.ptz_router is not None else None
        last_action = None
        moving = False
        pan_tilt_active = False
        zoom_active = False
        hat_active = False
        if self.ptz_router is not None:
            last_action = self.ptz_router.command_log[-1] if self.ptz_router.command_log else None
            moving = diagnostics.active_camera_moving if diagnostics is not None else False
            pan_tilt_active = self.ptz_router.pan_tilt_active
            zoom_active = self.ptz_router.zoom_active
            hat_active = self.ptz_router.hat_active
        cameras = [
            {
                "id": camera.id,
                "name": camera.name,
                "host": camera.host,
                "port": camera.port,
                "visca_id": camera.visca_id,
                "enabled": camera.enabled,
                "active": camera.id == self.state.active_ptz_camera_id,
            }
            for camera in self.state.config.ptz.cameras
        ]
        return {
            "active_camera": self.state.active_ptz_camera_id,
            "moving": moving,
            "pan_tilt_active": pan_tilt_active,
            "zoom_active": zoom_active,
            "hat_active": hat_active,
            "last_action": last_action,
            "configured_cameras": cameras,
        }

    def safety_status(self) -> dict[str, Any]:
        return {
            "watchdog_enabled": self.state.config.ptz.stop_watchdog.enabled,
            "center_confirm_samples": self.state.config.ptz.stop_watchdog.center_confirm_samples,
            "output_deadzone": {
                "pan_tilt": self.state.config.joystick.output_deadzone.pan_tilt,
                "zoom": self.state.config.joystick.output_deadzone.zoom,
            },
        }

    def _json_safe(self, value: Any) -> Any:
        if is_dataclass(value):
            return self._json_safe(asdict(value))
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(key): self._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self._json_safe(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def recent_activity(self) -> list[dict[str, Any]]:
        return [
            {
                "type": event.type,
                "payload": self._json_safe(event.payload),
                "created_at": event.created_at.isoformat(),
            }
            for event in self._recent_events[: self.max_recent_events]
        ]

    def status(self) -> dict[str, Any]:
        switcher = self.switcher_status()
        ptz = self.ptz_status()
        return {
            "system": {
                "application_name": self.state.config.app.name,
                "version": __version__,
                "stage": __stage__,
                "uptime": self.uptime_seconds,
            },
            "joystick": self.joystick_status(),
            "switcher": switcher,
            "ptz": ptz,
            "safety": self.safety_status(),
            "recent_activity": self.recent_activity(),
            "preview": switcher["preview_source"],
            "program": switcher["program_source"],
            "active_ptz_camera": ptz["active_camera"],
            "uptime": self.uptime_seconds,
        }
