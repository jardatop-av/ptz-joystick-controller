from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil


class BackupRotation:
    def __init__(self, backup_dir: str | Path, *, keep: int = 5) -> None:
        if keep < 1:
            raise ValueError("keep must be >= 1")
        self.backup_dir = Path(backup_dir)
        self.keep = keep

    def backup_file(self, source: str | Path) -> Path | None:
        source_path = Path(source)
        if not source_path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = self.backup_dir / f"{source_path.name}.{stamp}.bak"
        shutil.copy2(source_path, backup_path)
        self.rotate(source_path.name)
        return backup_path

    def rotate(self, basename: str) -> None:
        backups = sorted(
            self.backup_dir.glob(f"{basename}.*.bak"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old in backups[self.keep :]:
            old.unlink(missing_ok=True)
