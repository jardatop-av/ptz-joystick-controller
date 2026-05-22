from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from .joystick_input import JoystickSnapshot


class JoystickConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass(frozen=True)
class JoystickDeviceInfo:
    name: str
    path: str
    backend: str
    vendor_id: int | None = None
    product_id: int | None = None


@dataclass
class JoystickHealth:
    state: JoystickConnectionState = JoystickConnectionState.DISCONNECTED
    device: JoystickDeviceInfo | None = None
    last_snapshot: JoystickSnapshot = field(default_factory=JoystickSnapshot)
    last_seen_at: datetime | None = None
    last_error: str | None = None
    reconnect_attempts: int = 0

    @property
    def connected(self) -> bool:
        return self.state == JoystickConnectionState.CONNECTED

    def mark_connected(self, device: JoystickDeviceInfo, snapshot: JoystickSnapshot | None = None) -> None:
        self.state = JoystickConnectionState.CONNECTED
        self.device = device
        self.last_error = None
        self.last_seen_at = datetime.now(timezone.utc)
        if snapshot is not None:
            self.last_snapshot = snapshot

    def mark_disconnected(self, error: str | None = None) -> None:
        self.state = JoystickConnectionState.DISCONNECTED
        self.last_error = error
        self.device = None
        self.reconnect_attempts += 1

    def mark_error(self, error: str) -> None:
        self.state = JoystickConnectionState.ERROR
        self.last_error = error
        self.reconnect_attempts += 1

    def update_snapshot(self, snapshot: JoystickSnapshot) -> None:
        self.last_snapshot = snapshot
        self.last_seen_at = datetime.now(timezone.utc)

    def status_text(self) -> str:
        device_name = self.device.name if self.device is not None else "None"
        return (
            f"state={self.state.value} "
            f"connected={self.connected} "
            f"device={device_name} "
            f"last_error={self.last_error}"
        )
