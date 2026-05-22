from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import ControllerConfig, parse_config
from ..storage.preset_storage import PresetStorage
from .last_preset import LastPresetStore


class PresetManager:
    def __init__(
        self,
        preset_storage: PresetStorage | str | Path,
        last_preset_store: LastPresetStore | str | Path,
    ) -> None:
        self.preset_storage = preset_storage if isinstance(preset_storage, PresetStorage) else PresetStorage(preset_storage)
        self.last_preset_store = (
            last_preset_store if isinstance(last_preset_store, LastPresetStore) else LastPresetStore(last_preset_store)
        )

    @classmethod
    def from_paths(cls, preset_dir: str | Path, last_preset_path: str | Path) -> "PresetManager":
        return cls(PresetStorage(preset_dir), LastPresetStore(last_preset_path))

    def save(self, name: str, config: ControllerConfig, *, mark_as_last: bool = True) -> Path:
        path = self.preset_storage.save(name, config)
        if mark_as_last:
            self.last_preset_store.set(Path(path).stem)
        return path

    def save_preset(self, name: str, config: ControllerConfig | dict[str, Any], *, mark_last: bool = True) -> Path:
        parsed = parse_config(config) if isinstance(config, dict) else config
        return self.save(name, parsed, mark_as_last=mark_last)

    def load(self, name: str, *, mark_as_last: bool = True) -> ControllerConfig:
        config = self.preset_storage.load(name)
        if mark_as_last:
            self.last_preset_store.set(name.removesuffix(".yaml"))
        return config

    def load_last(self) -> ControllerConfig | None:
        name = self.last_preset_store.get()
        if not name:
            return None
        return self.preset_storage.load(name)
