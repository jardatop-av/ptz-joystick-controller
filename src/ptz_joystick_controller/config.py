from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .constants import APP_NAME, DEFAULT_WEB_PORT, LOG_LEVELS
from .models.joystick import JoystickConfig, ButtonAction
from .models.network import NetworkConfig
from .models.ptz import PtzConfig
from .models.sources import SourceMap
from .models.switcher import SwitcherConfig


class ConfigError(ValueError):
    """Raised when application configuration cannot be loaded or validated."""


class DiscoveryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    ndi: bool = True
    atem: bool = True
    vmix: bool = True
    osee: bool = True


class WebUiConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    listen_host: str = "0.0.0.0"
    listen_port: int = Field(default=DEFAULT_WEB_PORT, ge=1, le=65535)
    websocket_updates: bool = True
    emergency_controls: bool = True


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = APP_NAME
    device_name: str = "ptz-controller"
    log_level: str = "info"
    web_port: int = Field(default=DEFAULT_WEB_PORT, ge=1, le=65535)
    auto_load_last_preset: bool = True

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in LOG_LEVELS:
            raise ValueError(f"Invalid log_level: {value}")
        return normalized


class ControllerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    app: AppConfig = Field(default_factory=AppConfig)
    switcher: SwitcherConfig
    sources: SourceMap = Field(default_factory=SourceMap)
    ptz: PtzConfig = Field(default_factory=PtzConfig)
    joystick: JoystickConfig = Field(default_factory=JoystickConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    webui: WebUiConfig = Field(default_factory=WebUiConfig)

    @model_validator(mode="after")
    def validate_cross_references(self) -> "ControllerConfig":
        camera_ids = self.ptz.camera_ids()
        for mapping in self.sources.mappings:
            if mapping.ptz_camera_id and mapping.ptz_camera_id not in camera_ids:
                raise ValueError(
                    f"Source {mapping.source_id} references unknown ptz_camera_id: {mapping.ptz_camera_id}"
                )

        source_ids = self.sources.source_ids()
        try:
            from .switchers.capabilities import get_source_ids
            switcher_source_ids = set(get_source_ids(self.switcher.type))
        except Exception:
            switcher_source_ids = set()
        valid_source_ids = source_ids | switcher_source_ids
        for button_name, button in self.joystick.buttons.items():
            if button.action == ButtonAction.PREVIEW_SOURCE and button.source_id not in valid_source_ids:
                raise ValueError(
                    f"Button {button_name} references unknown source_id: {button.source_id}"
                )
        return self

    def ptz_camera_for_source(self, source_id: str) -> str | None:
        return self.sources.camera_for_source(source_id)


def load_yaml_file(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Unable to read config file {file_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {file_path}: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError("Config root must be a mapping")
    return data




def _apply_osee_duet_defaults(data: dict[str, Any], *, fill_unmapped_inputs: bool = False) -> dict[str, Any]:
    """Add missing logical Osee Input 1-8 camera slots and mappings.

    Existing camera and mapping entries always win. The migration is applied only
    for the Duet backend, so vMix configurations remain unchanged.
    """
    switcher = data.get("switcher")
    if not isinstance(switcher, dict):
        return data
    switcher_type = str(switcher.get("type", "")).strip().lower()
    is_osee = switcher_type in {"osee", "osee_gostream_duet"}
    is_osee_deck = switcher_type == "osee_gostream_deck"
    is_atem_4k8 = switcher_type in {"atem", "atem_television_studio_4k8"}
    if not (is_osee or is_osee_deck or is_atem_4k8):
        return data

    migrated = dict(data)
    ptz = dict(migrated.get("ptz") or {})
    default_port = int(ptz.get("default_port") or 52381)
    cameras = list(ptz.get("cameras") or [])
    existing_camera_ids = {str(item.get("id")) for item in cameras if isinstance(item, dict)}
    camera_count = 4 if is_osee_deck else 8
    for number in range(1, camera_count + 1):
        camera_id = f"cam{number}"
        if camera_id not in existing_camera_ids:
            cameras.append({
                "id": camera_id,
                "name": f"PTZ Camera {number}",
                "host": None,
                "port": default_port,
                "visca_id": 1,
                "enabled": False,
                "invert": {"pan": False, "tilt": False, "zoom": False},
                "speed": {
                    "pan_min": 1, "pan_max": 24,
                    "tilt_min": 1, "tilt_max": 20,
                    "zoom_min": 1, "zoom_max": 7,
                },
                "preset_offset": 0,
            })
    ptz["cameras"] = cameras
    migrated["ptz"] = ptz

    sources = dict(migrated.get("sources") or {})
    mappings = list(sources.get("mappings") or [])
    mapping_by_source = {str(item.get("source_id")): item for item in mappings if isinstance(item, dict)}
    existing_source_ids = set(mapping_by_source)
    source_count = 4 if is_osee_deck else 8
    for number in range(1, source_count + 1):
        source_id = f"Input {number}"
        if source_id not in existing_source_ids:
            mappings.append({
                "source_id": source_id,
                "display_name": source_id,
                "ptz_camera_id": f"cam{number}",
            })
        elif fill_unmapped_inputs and mapping_by_source[source_id].get("ptz_camera_id") is None:
            mapping_by_source[source_id]["ptz_camera_id"] = f"cam{number}"
    if is_osee:
        extra_sources = ("MP1", "MP2", "M/SRC")
    elif is_osee_deck:
        extra_sources = ("AUX", "STILL1", "STILL2", "S/SRC")
    else:
        extra_sources = ("Black", "MP1", "MP2", "SuperSource")
    for source_id in extra_sources:
        if source_id not in existing_source_ids:
            mappings.append({"source_id": source_id, "display_name": source_id, "ptz_camera_id": None})
    sources["mappings"] = mappings
    migrated["sources"] = sources
    return migrated

def parse_config(data: dict[str, Any]) -> ControllerConfig:
    try:
        return ControllerConfig.model_validate(_apply_osee_duet_defaults(data))
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def _merge_sequence_by_key(base: list[Any], override: list[Any]) -> list[Any]:
    """Merge lists of mappings by stable keys used in config files.

    Lists such as ``ptz.cameras`` and ``sources.mappings`` are edited locally by
    id rather than repeated in full. Unknown override items are appended. Lists
    without a known key fall back to full replacement.
    """
    key_candidates = ("id", "source_id", "name")
    selected_key: str | None = None
    for key in key_candidates:
        if any(isinstance(item, dict) and key in item for item in base + override):
            selected_key = key
            break

    if selected_key is None:
        return override

    merged: list[Any] = []
    index: dict[Any, int] = {}
    for item in base:
        merged.append(item)
        if isinstance(item, dict) and selected_key in item:
            index[item[selected_key]] = len(merged) - 1

    for item in override:
        if isinstance(item, dict) and selected_key in item and item[selected_key] in index:
            existing = merged[index[item[selected_key]]]
            if isinstance(existing, dict):
                merged[index[item[selected_key]]] = deep_merge_config(existing, item)
            else:
                merged[index[item[selected_key]]] = item
        else:
            merged.append(item)
    return merged


def deep_merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return ``base`` recursively overridden by ``override``.

    This is intended for ``config.local.yaml`` machine-specific overrides. It
    keeps the generic example complete while allowing small local patches such as
    camera hosts, disabled cameras or vMix host/port changes.
    """
    merged: dict[str, Any] = dict(base)
    for key, override_value in override.items():
        if key in merged:
            base_value = merged[key]
            if isinstance(base_value, dict) and isinstance(override_value, dict):
                merged[key] = deep_merge_config(base_value, override_value)
            elif isinstance(base_value, list) and isinstance(override_value, list):
                merged[key] = _merge_sequence_by_key(base_value, override_value)
            else:
                merged[key] = override_value
        else:
            merged[key] = override_value
    return merged


def default_local_config_path(path: str | Path) -> Path:
    config_path = Path(path)
    if config_path.name == "config.example.yaml":
        return config_path.with_name("config.local.yaml")
    return config_path.with_suffix(".local.yaml")


def load_config(path: str | Path, *, local_path: str | Path | None = None, use_local: bool = True) -> ControllerConfig:
    base_data = load_yaml_file(path)
    local_data: dict[str, Any] = {}
    if use_local:
        resolved_local_path = Path(local_path) if local_path is not None else default_local_config_path(path)
        if resolved_local_path.exists():
            local_data = load_yaml_file(resolved_local_path)

    # Determine the effective backend before merging. For Osee, augment the
    # generic base configuration with Input 1-8 defaults first; explicit local
    # mappings (including ptz_camera_id: null) are then allowed to override them.
    effective_probe = deep_merge_config(base_data, local_data) if local_data else base_data
    effective_switcher = effective_probe.get("switcher") if isinstance(effective_probe, dict) else None
    effective_type = str(effective_switcher.get("type", "")).lower() if isinstance(effective_switcher, dict) else ""
    if effective_type in {"osee", "osee_gostream_duet", "osee_gostream_deck"}:
        base_for_osee = deep_merge_config(base_data, {"switcher": {"type": effective_type}})
        base_data = _apply_osee_duet_defaults(base_for_osee, fill_unmapped_inputs=True)

    if local_data:
        base_data = deep_merge_config(base_data, local_data)
    return parse_config(base_data)


def dump_config(config: ControllerConfig) -> dict[str, Any]:
    return config.model_dump(mode="json", exclude_none=False)
