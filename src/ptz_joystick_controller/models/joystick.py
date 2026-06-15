from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ButtonAction(StrEnum):
    NONE = "none"
    CUT = "cut"
    AUTO = "auto"
    COPY_PROGRAM_TO_PREVIEW = "copy_program_to_preview"
    PREVIEW_SOURCE = "preview_source"
    PRESET_RECALL = "preset_recall"


class ButtonMapping(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: ButtonAction = ButtonAction.NONE
    source_id: str | None = None
    preset_number: int | None = Field(default=None, ge=0, le=255)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "ButtonMapping":
        if self.action == ButtonAction.PREVIEW_SOURCE:
            if not self.source_id:
                raise ValueError("preview_source action requires source_id")
            if self.preset_number is not None:
                raise ValueError("preset_number is not allowed for preview_source action")
            return self
        if self.action == ButtonAction.PRESET_RECALL:
            if self.preset_number is None:
                raise ValueError("preset_recall action requires preset_number")
            if self.source_id:
                raise ValueError("source_id is not allowed for preset_recall action")
            return self
        if self.source_id:
            raise ValueError("source_id is allowed only for preview_source action")
        if self.preset_number is not None:
            raise ValueError("preset_number is allowed only for preset_recall action")
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


class OutputDeadzoneConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    pan_tilt: float = Field(default=0.05, ge=0.0, le=1.0)
    zoom: float = Field(default=0.05, ge=0.0, le=1.0)


class ThrottleConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_multiplier: float = Field(default=0.20, ge=0.0, le=1.0)
    max_multiplier: float = Field(default=1.00, ge=0.0, le=1.0)
    invert: bool = True

    @model_validator(mode="after")
    def validate_range(self) -> "ThrottleConfig":
        if self.min_multiplier > self.max_multiplier:
            raise ValueError("min_multiplier must be <= max_multiplier")
        return self


class HatConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    fine_pan_speed: int = Field(default=2, ge=0)
    fine_tilt_speed: int = Field(default=2, ge=0)
    apply_throttle: bool = False


class JoystickConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str = "logitech_extreme_3d_pro"
    device_path: str = "auto"
    deadzone: DeadzoneConfig = Field(default_factory=DeadzoneConfig)
    invert: AxisInvertConfig = Field(default_factory=AxisInvertConfig)
    output_deadzone: OutputDeadzoneConfig = Field(default_factory=OutputDeadzoneConfig)
    throttle: ThrottleConfig = Field(default_factory=ThrottleConfig)
    hat: HatConfig = Field(default_factory=HatConfig)
    button_labels: dict[str, str] = Field(default_factory=dict)
    buttons: dict[str, ButtonMapping] = Field(default_factory=dict)
