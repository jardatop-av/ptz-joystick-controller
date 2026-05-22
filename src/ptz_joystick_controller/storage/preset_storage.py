from __future__ import annotations

from pathlib import Path

import yaml

from ..config import ControllerConfig, dump_config, load_config, parse_config
from .atomic_write import atomic_write_text
from .backup_rotation import BackupRotation


class PresetStorage:
    def __init__(self, preset_dir: str | Path, *, backup_dir: str | Path | None = None, keep_backups: int = 5) -> None:
        self.preset_dir = Path(preset_dir)
        self.backup_rotation = BackupRotation(backup_dir, keep=keep_backups) if backup_dir else None

    def preset_path(self, name: str) -> Path:
        safe_name = name.strip()
        if not safe_name:
            raise ValueError("Preset name must not be empty")
        if "/" in safe_name or "\\" in safe_name:
            raise ValueError("Preset name must not contain path separators")
        if not safe_name.endswith(".yaml"):
            safe_name = f"{safe_name}.yaml"
        return self.preset_dir / safe_name

    def save(self, name: str, config: ControllerConfig) -> Path:
        path = self.preset_path(name)
        if self.backup_rotation:
            self.backup_rotation.backup_file(path)
        serialized = yaml.safe_dump(dump_config(config), sort_keys=False, allow_unicode=True)
        atomic_write_text(path, serialized)
        return path

    def load(self, name: str) -> ControllerConfig:
        return load_config(self.preset_path(name))

    def load_path(self, path: str | Path) -> ControllerConfig:
        return load_config(path)

    def list_presets(self) -> list[str]:
        if not self.preset_dir.exists():
            return []
        return sorted(path.stem for path in self.preset_dir.glob("*.yaml"))

    def export_dict(self, name: str) -> dict[str, object]:
        data = yaml.safe_load(self.preset_path(name).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Preset file does not contain a mapping")
        parse_config(data)
        return data
