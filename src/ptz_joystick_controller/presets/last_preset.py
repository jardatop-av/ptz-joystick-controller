from __future__ import annotations

from pathlib import Path

from ..storage.atomic_write import atomic_write_text


class LastPresetStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def get(self) -> str | None:
        if not self.path.exists():
            return None
        value = self.path.read_text(encoding="utf-8").strip()
        return value or None

    def set(self, name: str) -> None:
        if not name.strip():
            raise ValueError("Last preset name must not be empty")
        atomic_write_text(self.path, f"{name.strip()}\n")

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
