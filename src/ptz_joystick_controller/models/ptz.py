from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..constants import DEFAULT_VISCA_PORT
from .joystick import AxisInvertConfig


class PtzSpeedConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    pan_min: int = Field(default=1, ge=0)
    pan_max: int = Field(default=24, ge=0)
    tilt_min: int = Field(default=1, ge=0)
    tilt_max: int = Field(default=20, ge=0)
    zoom_min: int = Field(default=1, ge=0)
    zoom_max: int = Field(default=7, ge=0)


class PtzCamera(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    host: str | None = None
    port: int = Field(default=DEFAULT_VISCA_PORT, ge=1, le=65535)
    visca_id: int = Field(default=1, ge=1, le=7)
    enabled: bool = True
    invert: AxisInvertConfig = Field(default_factory=AxisInvertConfig)
    speed: PtzSpeedConfig = Field(default_factory=PtzSpeedConfig)

    @field_validator("id", "name", mode="before")
    @classmethod
    def strip_required_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class PtzStopWatchdogConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    timeout_ms: int = Field(default=500, ge=50)
    center_confirm_samples: int = Field(default=3, ge=1)


class PtzConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    protocol: str = "visca_over_ip"
    default_port: int = Field(default=DEFAULT_VISCA_PORT, ge=1, le=65535)
    stop_on_switch: bool = True
    stop_watchdog: PtzStopWatchdogConfig = Field(default_factory=PtzStopWatchdogConfig)
    cameras: tuple[PtzCamera, ...] = ()

    @field_validator("cameras")
    @classmethod
    def require_unique_camera_ids(cls, cameras: tuple[PtzCamera, ...]) -> tuple[PtzCamera, ...]:
        seen: set[str] = set()
        for camera in cameras:
            if camera.id in seen:
                raise ValueError(f"Duplicate PTZ camera id: {camera.id}")
            seen.add(camera.id)
        return cameras

    def camera_ids(self) -> set[str]:
        return {camera.id for camera in self.cameras}
