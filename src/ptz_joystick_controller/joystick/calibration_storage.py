from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from .calibration import AxisCalibration, JoystickCalibration
from ..storage.atomic_write import atomic_write_text


class JoystickCalibrationStorage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> JoystickCalibration:
        if not self.path.exists():
            return JoystickCalibration()
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return JoystickCalibration(
            pan=self._axis(raw.get("pan")),
            tilt=self._axis(raw.get("tilt")),
            zoom=self._axis(raw.get("zoom")),
            throttle=self._axis(raw.get("throttle")),
        )

    def save(self, calibration: JoystickCalibration) -> None:
        atomic_write_text(self.path, yaml.safe_dump(asdict(calibration), sort_keys=True))

    def _axis(self, raw: Any) -> AxisCalibration:
        if not isinstance(raw, dict):
            return AxisCalibration()
        return AxisCalibration(
            minimum=int(raw.get("minimum", -32768)),
            center=int(raw.get("center", 0)),
            maximum=int(raw.get("maximum", 32767)),
        )
