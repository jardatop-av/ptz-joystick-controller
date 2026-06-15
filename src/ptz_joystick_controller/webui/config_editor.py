from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..config import ControllerConfig, deep_merge_config, dump_config, load_yaml_file, parse_config
from ..joystick.button_metadata import CANONICAL_BUTTON_IDS, ButtonMetadataRegistry
from ..models.joystick import ButtonAction, ButtonMapping
from ..storage.atomic_write import atomic_write_text

_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_ALLOWED_FORM_BUTTON_ACTIONS = {
    ButtonAction.PREVIEW_SOURCE,
    ButtonAction.PRESET_RECALL,
    ButtonAction.NONE,
    ButtonAction.CUT,
    ButtonAction.COPY_PROGRAM_TO_PREVIEW,
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
    preset_offset: int = Field(default=0, ge=0, le=255)

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


class EditableStopWatchdogConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    center_confirm_samples: int = Field(default=3, ge=1)


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
            if mapping.action not in _ALLOWED_FORM_BUTTON_ACTIONS:
                raise ValueError(
                    f"Button {button_id} action {mapping.action.value!r} is not allowed from the web config page"
                )
        return value


class EditablePtzConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cameras: list[EditablePtzCameraConfig]
    stop_watchdog: EditableStopWatchdogConfig


class EditableConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    switcher: EditableSwitcherConfig
    ptz: EditablePtzConfig
    joystick: EditableJoystickConfig

    @model_validator(mode="after")
    def validate_preset_numbers(self) -> "EditableConfigPatch":
        for button_id, mapping in self.joystick.buttons.items():
            if mapping.action == ButtonAction.PRESET_RECALL:
                if mapping.preset_number is None or not 0 <= mapping.preset_number <= 255:
                    raise ValueError(f"Button {button_id} has invalid preset_number: {mapping.preset_number}")
        return self


@dataclass
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
        registry = ButtonMetadataRegistry(getattr(self.current_config.joystick, "button_labels", {}))
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
                        "preset_offset": getattr(camera, "preset_offset", 0),
                    }
                    for camera in self.current_config.ptz.cameras
                ],
                "stop_watchdog": {
                    "enabled": self.current_config.ptz.stop_watchdog.enabled,
                    "center_confirm_samples": self.current_config.ptz.stop_watchdog.center_confirm_samples,
                },
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
                    button_id: self.current_config.joystick.buttons.get(button_id, ButtonMapping()).model_dump(
                        mode="json", exclude_none=True
                    )
                    for button_id in CANONICAL_BUTTON_IDS
                },
                "button_metadata": {
                    button_id: {"button_id": button_id, "label": registry.label_for(button_id)}
                    for button_id in CANONICAL_BUTTON_IDS
                },
            },
        }

    def validate_patch(self, raw_patch: dict[str, Any]) -> EditableConfigPatch:
        patch_data = _strip_non_editable_metadata(raw_patch)
        try:
            return EditableConfigPatch.model_validate(patch_data)
        except ValidationError as exc:
            raise ConfigEditError(str(exc)) from exc

    def patch_to_local_override(self, patch: EditableConfigPatch) -> dict[str, Any]:
        return {
            "switcher": {
                "host": patch.switcher.host,
                "port": patch.switcher.port,
            },
            "ptz": {
                "stop_watchdog": {
                    "enabled": patch.ptz.stop_watchdog.enabled,
                    "center_confirm_samples": patch.ptz.stop_watchdog.center_confirm_samples,
                },
                "cameras": [
                    {
                        "id": camera.id,
                        "name": camera.name,
                        "host": camera.host,
                        "port": camera.port,
                        "enabled": camera.enabled,
                        "preset_offset": camera.preset_offset,
                    }
                    for camera in patch.ptz.cameras
                ],
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

    def form_payload_from_mapping(self, form: Mapping[str, Any]) -> dict[str, Any]:
        data = {key: str(value) for key, value in form.items()}
        cameras: list[dict[str, Any]] = []
        for index, camera in enumerate(self.current_config.ptz.cameras):
            prefix = f"camera_{index}_"
            cameras.append(
                {
                    "id": data.get(prefix + "id", camera.id),
                    "name": data.get(prefix + "name", camera.name),
                    "host": data.get(prefix + "host", camera.host or ""),
                    "port": _optional_int(data.get(prefix + "port"), camera.port),
                    "enabled": _checkbox(data, prefix + "enabled"),
                    "preset_offset": _optional_int(data.get(prefix + "preset_offset"), getattr(camera, "preset_offset", 0)),
                }
            )

        buttons: dict[str, dict[str, Any]] = {}
        for button_id in CANONICAL_BUTTON_IDS:
            action = _button_form_value(data, button_id, "action", ButtonAction.NONE.value)
            entry: dict[str, Any] = {"action": action}
            if action == ButtonAction.PREVIEW_SOURCE.value:
                entry["source_id"] = str(_button_form_value(data, button_id, "source_id", "")).strip()
            elif action == ButtonAction.PRESET_RECALL.value:
                entry["preset_number"] = _optional_int(_button_form_value(data, button_id, "preset_number", None), None)
            # For none/cut/copy_program_to_preview, intentionally omit source_id
            # and preset_number so irrelevant values are cleared in the saved
            # runtime structure instead of lingering from a previous mapping.
            buttons[button_id] = entry

        return {
            "switcher": {
                "host": data.get("switcher_host", ""),
                "port": _optional_int(data.get("switcher_port"), None),
            },
            "ptz": {
                "cameras": cameras,
                "stop_watchdog": {
                    "enabled": _checkbox(data, "stop_watchdog_enabled"),
                    "center_confirm_samples": _optional_int(data.get("center_confirm_samples"), 3),
                },
            },
            "joystick": {
                "invert": {
                    "pan": _checkbox(data, "invert_pan"),
                    "tilt": _checkbox(data, "invert_tilt"),
                    "zoom": _checkbox(data, "invert_zoom"),
                },
                "output_deadzone": {
                    "pan_tilt": _optional_float(data.get("output_deadzone_pan_tilt"), 0.05),
                    "zoom": _optional_float(data.get("output_deadzone_zoom"), 0.05),
                },
                "buttons": buttons,
            },
        }

    def save_form(self, form: Mapping[str, Any]) -> dict[str, Any]:
        return self.save_patch(self.form_payload_from_mapping(form))

    def save_patch(self, raw_patch: dict[str, Any]) -> dict[str, Any]:
        patch = self.validate_patch(raw_patch)
        controlled_update = self.patch_to_local_override(patch)

        # Basic form saving is intentionally a partial update.  Start from the
        # current merged YAML data, apply only the fields controlled by the
        # form, validate the known application schema, and then persist the
        # merged result to config.local.yaml.  This preserves unrelated
        # sections such as webui and future/unknown user extensions instead of
        # regenerating a minimal local override from only form fields.
        base_data = load_yaml_file(self.example_config_path) if self.example_config_path.exists() else dump_config(self.current_config)
        existing_local = load_yaml_file(self.local_config_path) if self.local_config_path.exists() else {}
        existing_merged = deep_merge_config(base_data, existing_local)
        merged = deep_merge_config(existing_merged, controlled_update)
        # Button mappings are edited as complete per-button mappings.  Replace
        # the entire button mapping table instead of deep-merging individual
        # button dicts, otherwise stale source_id/preset_number fields from a
        # previous action can survive when the form changes the action to none
        # or to another action type.
        merged.setdefault("joystick", {})["buttons"] = controlled_update["joystick"]["buttons"]
        parsed_config = parse_config(merged)

        _backup_local_config(self.local_config_path)
        serialized = yaml.safe_dump(merged, sort_keys=False, allow_unicode=True)
        atomic_write_text(self.local_config_path, serialized)
        # Make the post-save response and subsequent /config reads reflect the
        # actual saved values.  Unknown sections remain preserved in YAML on
        # disk, while current_config tracks the validated application schema.
        self.current_config = parsed_config
        return {
            "status": "saved",
            "message": "Configuration saved. Restart required.",
            "local_config_path": str(self.local_config_path),
        }


def _strip_non_editable_metadata(raw_patch: dict[str, Any]) -> dict[str, Any]:
    data = dict(raw_patch)
    joystick = data.get("joystick")
    if isinstance(joystick, dict) and "button_metadata" in joystick:
        joystick = dict(joystick)
        joystick.pop("button_metadata", None)
        data["joystick"] = joystick
    return data

def _validate_optional_host(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped == "":
        return None
    if not _HOST_RE.match(stripped):
        raise ValueError(f"Invalid host/IP value: {value}")
    return stripped


def _button_form_value(data: Mapping[str, str], button_id: str, field: str, default: object) -> object:
    """Return a button form value using canonical and legacy-safe names.

    Canonical HTML names are ``button_<button_id>_<field>``.  For canonical
    IDs that already begin with ``button_`` this produces names such as
    ``button_button_10_action``.  Accept ``button_10_action`` as a defensive
    fallback too, because it is the natural name a hand-written form or test
    client may submit.
    """
    canonical_key = f"button_{button_id}_{field}"
    if canonical_key in data:
        return data[canonical_key]
    fallback_key = f"{button_id}_{field}"
    if fallback_key in data:
        return data[fallback_key]
    return default


def _backup_local_config(local_config_path: Path) -> None:
    backup_path = local_config_path.with_name(local_config_path.name + ".bak")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if local_config_path.exists():
        shutil.copy2(local_config_path, backup_path)
    else:
        atomic_write_text(backup_path, "# No previous config.local.yaml\n")


def _checkbox(form: Mapping[str, Any], key: str) -> bool:
    return key in form and str(form[key]).lower() not in {"", "0", "false", "off", "no"}


def _optional_int(value: object, default: int | None) -> int | None:
    if value is None:
        return default
    text = str(value).strip()
    if text == "":
        return default
    try:
        return int(text)
    except ValueError as exc:
        raise ConfigEditError(f"Invalid integer value: {value}") from exc


def _optional_float(value: object, default: float) -> float:
    if value is None:
        return default
    text = str(value).strip()
    if text == "":
        return default
    try:
        return float(text)
    except ValueError as exc:
        raise ConfigEditError(f"Invalid numeric value: {value}") from exc
