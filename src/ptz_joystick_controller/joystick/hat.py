from __future__ import annotations

from dataclasses import dataclass

from ..models.joystick import HatConfig
from ..models.joystick_input import HatPtzStep, HatState


@dataclass(frozen=True)
class HatProcessor:
    config: HatConfig

    def to_ptz_step(self, hat: HatState, *, throttle_multiplier: float = 1.0) -> HatPtzStep:
        x = max(-1, min(1, hat.x))
        y = max(-1, min(1, hat.y))
        multiplier = max(0.0, throttle_multiplier) if self.config.apply_throttle else 1.0
        return HatPtzStep(
            pan_speed=self._scaled_signed_speed(x, self.config.fine_pan_speed, multiplier),
            tilt_speed=self._scaled_signed_speed(-y, self.config.fine_tilt_speed, multiplier),
        )

    @staticmethod
    def _scaled_signed_speed(direction: int, configured_speed: int, multiplier: float) -> int:
        if direction == 0 or configured_speed <= 0:
            return 0
        speed = int(round(configured_speed * multiplier))
        return direction * max(1, speed)
