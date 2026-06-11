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
        for button_name, button in self.joystick.buttons.items():
            if button.action == ButtonAction.PREVIEW_SOURCE and button.source_id not in source_ids:
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


def parse_config(data: dict[str, Any]) -> ControllerConfig:
    try:
        return ControllerConfig.model_validate(data)
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
    if use_local:
        resolved_local_path = Path(local_path) if local_path is not None else default_local_config_path(path)
        if resolved_local_path.exists():
            base_data = deep_merge_config(base_data, load_yaml_file(resolved_local_path))
    return parse_config(base_data)


def dump_config(config: ControllerConfig) -> dict[str, Any]:
    return config.model_dump(mode="json", exclude_none=False)
