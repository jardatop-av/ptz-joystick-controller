from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..config import ControllerConfig, deep_merge_config, dump_config, load_yaml_file, parse_config
from ..joystick.button_metadata import CANONICAL_BUTTON_IDS
from ..models.joystick import ButtonAction, ButtonMapping
from ..storage.atomic_write import atomic_write_text

_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_ALLOWED_BUTTON_ACTIONS = {ButtonAction.PREVIEW_SOURCE, ButtonAction.PRESET_RECALL, ButtonAction.NONE}
_FIXED_ALLOWED_BUTTON_ACTIONS = {
    'trigger': {ButtonAction.CUT, ButtonAction.AUTO, ButtonAction.NONE},
    'thumb': {ButtonAction.COPY_PROGRAM_TO_PREVIEW, ButtonAction.NONE},
}


class ConfigEditError(ValueError):
    """Raised when a web configuration edit is invalid."""


class EditableSwitcherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str | None) -> str | None:
        return _validate_optional_host(value)


class EditablePtzCameraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    host: str | None = None
    port: int = Field(ge=1, le=65535)
    enabled: bool = True

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str | None) -> str | None:
        return _validate_optional_host(value)


class EditableAxisInvertConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pan: bool = False
    tilt: bool = False
    zoom: bool = False


class EditableOutputDeadzoneConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pan_tilt: float = Field(ge=0.0, le=1.0)
    zoom: float = Field(ge=0.0, le=1.0)


class EditableJoystickConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invert: EditableAxisInvertConfig
    buttons: dict[str, ButtonMapping]
    output_deadzone: EditableOutputDeadzoneConfig

    @field_validator("buttons")
    @classmethod
    def validate_buttons(cls, value: dict[str, ButtonMapping]) -> dict[str, ButtonMapping]:
        known = set(CANONICAL_BUTTON_IDS)
        for button_id, mapping in value.items():
            if button_id not in known:
                raise ValueError(f"Unknown joystick button id: {button_id}")
            allowed_actions = _FIXED_ALLOWED_BUTTON_ACTIONS.get(button_id, _ALLOWED_BUTTON_ACTIONS)
            if mapping.action not in allowed_actions:
                raise ValueError(
                    f"Button {button_id} action {mapping.action.value!r} is not allowed from the web config page"
                )
        return value


class EditablePtzConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cameras: list[EditablePtzCameraConfig]


class EditableConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    switcher: EditableSwitcherConfig
    ptz: EditablePtzConfig
    joystick: EditableJoystickConfig

    @model_validator(mode="after")
    def validate_preset_numbers(self) -> "EditableConfigPatch":
        for button_id, mapping in self.joystick.buttons.items():
            if mapping.action == ButtonAction.PRESET_RECALL and mapping.preset_number is not None:
                if not 0 <= mapping.preset_number <= 255:
                    raise ValueError(f"Button {button_id} has invalid preset_number: {mapping.preset_number}")
        return self


@dataclass(frozen=True)
class ConfigEditor:
    """Safe, limited web configuration editor.

    The editor reads from the already loaded merged ControllerConfig and writes
    only machine-specific overrides to config.local.yaml. It never writes the
    generic config.example.yaml.
    """

    current_config: ControllerConfig
    example_config_path: Path = Path("config.example.yaml")
    local_config_path: Path = Path("config.local.yaml")

    def editable_payload(self) -> dict[str, Any]:
        return {
            "switcher": {
                "host": self.current_config.switcher.host,
                "port": self.current_config.switcher.port,
            },
            "ptz": {
                "cameras": [
                    {
                        "id": camera.id,
                        "name": camera.name,
                        "host": camera.host,
                        "port": camera.port,
                        "enabled": camera.enabled,
                    }
                    for camera in self.current_config.ptz.cameras
                ]
            },
            "joystick": {
                "invert": {
                    "pan": self.current_config.joystick.invert.pan,
                    "tilt": self.current_config.joystick.invert.tilt,
                    "zoom": self.current_config.joystick.invert.zoom,
                },
                "output_deadzone": {
                    "pan_tilt": self.current_config.joystick.output_deadzone.pan_tilt,
                    "zoom": self.current_config.joystick.output_deadzone.zoom,
                },
                "buttons": {
                    button_id: mapping.model_dump(mode="json", exclude_none=True)
                    for button_id, mapping in self.current_config.joystick.buttons.items()
                },
            },
        }

    def validate_patch(self, raw_patch: dict[str, Any]) -> EditableConfigPatch:
        try:
            return EditableConfigPatch.model_validate(raw_patch)
        except ValidationError as exc:
            raise ConfigEditError(str(exc)) from exc

    def patch_to_local_override(self, patch: EditableConfigPatch) -> dict[str, Any]:
        return {
            "switcher": {
                "host": patch.switcher.host,
                "port": patch.switcher.port,
            },
            "ptz": {
                "cameras": [
                    {
                        "id": camera.id,
                        "name": camera.name,
                        "host": camera.host,
                        "port": camera.port,
                        "enabled": camera.enabled,
                    }
                    for camera in patch.ptz.cameras
                ]
            },
            "joystick": {
                "invert": patch.joystick.invert.model_dump(mode="json"),
                "output_deadzone": patch.joystick.output_deadzone.model_dump(mode="json"),
                "buttons": {
                    button_id: mapping.model_dump(mode="json", exclude_none=True)
                    for button_id, mapping in patch.joystick.buttons.items()
                },
            },
        }

    def save_patch(self, raw_patch: dict[str, Any]) -> dict[str, Any]:
        patch = self.validate_patch(raw_patch)
        local_override = self.patch_to_local_override(patch)

        # Validate the final merged config before saving the local override.
        base_data = load_yaml_file(self.example_config_path) if self.example_config_path.exists() else dump_config(self.current_config)
        merged = deep_merge_config(base_data, local_override)
        parse_config(merged)

        _backup_local_config(self.local_config_path)
        serialized = yaml.safe_dump(local_override, sort_keys=False, allow_unicode=True)
        atomic_write_text(self.local_config_path, serialized)
        return {
            "status": "saved",
            "message": "Configuration saved. Restart required.",
            "local_config_path": str(self.local_config_path),
        }


def _validate_optional_host(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped == "":
        return None
    if not _HOST_RE.match(stripped):
        raise ValueError(f"Invalid host/IP value: {value}")
    return stripped


def _backup_local_config(local_config_path: Path) -> None:
    backup_path = local_config_path.with_name(local_config_path.name + ".bak")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if local_config_path.exists():
        shutil.copy2(local_config_path, backup_path)
    else:
        atomic_write_text(backup_path, "# No previous config.local.yaml\n")
