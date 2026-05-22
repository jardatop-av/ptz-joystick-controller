from __future__ import annotations

from pathlib import Path

import yaml

from ..config import ControllerConfig, dump_config, load_config, parse_config
from .atomic_write import atomic_write_text
from .backup_rotation import BackupRotation


class ConfigStorage:
    def __init__(self, path: str | Path, *, backup_dir: str | Path | None = None, keep_backups: int = 5) -> None:
        self.path = Path(path)
        self.backup_rotation = BackupRotation(backup_dir, keep=keep_backups) if backup_dir else None

    def load(self) -> ControllerConfig:
        return load_config(self.path)

    def save(self, config: ControllerConfig) -> None:
        if self.backup_rotation:
            self.backup_rotation.backup_file(self.path)
        serialized = yaml.safe_dump(dump_config(config), sort_keys=False, allow_unicode=True)
        atomic_write_text(self.path, serialized)

    def save_raw(self, data: dict[str, object]) -> None:
        self.save(parse_config(data))
