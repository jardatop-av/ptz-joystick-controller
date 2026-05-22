from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ButtonAction(StrEnum):
    NONE = "none"
    CUT = "cut"
    AUTO = "auto"
    COPY_PROGRAM_TO_PREVIEW = "copy_program_to_preview"
    PREVIEW_SOURCE = "preview_source"


class ButtonMapping(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: ButtonAction = ButtonAction.NONE
    source_id: str | None = None

    @model_validator(mode="after")
    def validate_source_id_usage(self) -> "ButtonMapping":
        if self.action == ButtonAction.PREVIEW_SOURCE and not self.source_id:
            raise ValueError("preview_source action requires source_id")
        if self.action != ButtonAction.PREVIEW_SOURCE and self.source_id:
            raise ValueError("source_id is allowed only for preview_source action")
        return self


class DeadzoneConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    pan: float = Field(default=0.08, ge=0.0, le=1.0)
    tilt: float = Field(default=0.08, ge=0.0, le=1.0)
    zoom: float = Field(default=0.10, ge=0.0, le=1.0)


class AxisInvertConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    pan: bool = False
    tilt: bool = False
    zoom: bool = False


class ThrottleConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_multiplier: float = Field(default=0.20, ge=0.0, le=1.0)
    max_multiplier: float = Field(default=1.00, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_range(self) -> "ThrottleConfig":
        if self.min_multiplier > self.max_multiplier:
            raise ValueError("min_multiplier must be <= max_multiplier")
        return self


class HatConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    fine_pan_speed: int = Field(default=2, ge=0)
    fine_tilt_speed: int = Field(default=2, ge=0)


class JoystickConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str = "logitech_extreme_3d_pro"
    device_path: str = "auto"
    deadzone: DeadzoneConfig = Field(default_factory=DeadzoneConfig)
    invert: AxisInvertConfig = Field(default_factory=AxisInvertConfig)
    throttle: ThrottleConfig = Field(default_factory=ThrottleConfig)
    hat: HatConfig = Field(default_factory=HatConfig)
    buttons: dict[str, ButtonMapping] = Field(default_factory=dict)
