from __future__ import annotations

from dataclasses import dataclass

from ..models.joystick import HatConfig
from ..models.joystick_input import HatPtzStep, HatState


@dataclass(frozen=True)
class HatProcessor:
    config: HatConfig

    def to_ptz_step(self, hat: HatState) -> HatPtzStep:
        x = max(-1, min(1, hat.x))
        y = max(-1, min(1, hat.y))
        return HatPtzStep(
            pan_speed=x * self.config.fine_pan_speed,
            tilt_speed=-y * self.config.fine_tilt_speed,
        )
