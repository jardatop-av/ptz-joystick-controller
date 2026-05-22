from __future__ import annotations

from dataclasses import dataclass

from ..app_state import AppState
from ..event_bus import EventBus
from ..models.commands import EventType


@dataclass
class PtzControlStateMachine:
    state: AppState
    event_bus: EventBus
    movement_enabled: bool = True

    def request_stop(self, reason: str) -> None:
        camera_id = self.state.active_ptz_camera_id
        self.state.stop_requests.append(reason)
        self.event_bus.publish(
            EventType.PTZ_STOP_REQUESTED,
            {"camera_id": camera_id, "reason": reason},
        )

    def disable_movement(self, reason: str) -> None:
        self.movement_enabled = False
        self.request_stop(reason)
        self.event_bus.publish("ptz.movement_disabled", {"reason": reason})

    def enable_movement(self) -> None:
        self.movement_enabled = True
        self.event_bus.publish("ptz.movement_enabled", {})

    def recompute_active_ptz(self) -> str | None:
        old_camera_id = self.state.active_ptz_camera_id
        new_camera_id = self.state.recompute_active_ptz()
        if old_camera_id != new_camera_id:
            self.event_bus.publish(
                EventType.ACTIVE_PTZ_CHANGED,
                {"old_camera_id": old_camera_id, "new_camera_id": new_camera_id},
            )
        return new_camera_id
