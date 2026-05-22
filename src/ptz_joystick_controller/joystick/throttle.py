from __future__ import annotations

from dataclasses import dataclass

from ..models.joystick import ThrottleConfig


@dataclass(frozen=True)
class ThrottleScaler:
    config: ThrottleConfig

    def scale(self, normalized_value: float) -> float:
        clamped = max(-1.0, min(1.0, normalized_value))
        unit = (clamped + 1.0) / 2.0
        return self.config.min_multiplier + unit * (self.config.max_multiplier - self.config.min_multiplier)
