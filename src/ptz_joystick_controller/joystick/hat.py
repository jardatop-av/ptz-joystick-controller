from __future__ import annotations

from dataclasses import dataclass, field

from ..models.joystick import AxisInvertConfig, HatConfig
from ..models.joystick_input import HatPtzStep, HatState


@dataclass(frozen=True)
class HatProcessor:
    config: HatConfig
    invert: AxisInvertConfig = field(default_factory=AxisInvertConfig)

    def to_ptz_step(self, hat: HatState, *, throttle_multiplier: float = 1.0) -> HatPtzStep:
        x = max(-1, min(1, hat.x))
        y = max(-1, min(1, hat.y))
        multiplier = max(0.0, throttle_multiplier) if self.config.apply_throttle else 1.0

        pan_direction = -x if self.invert.pan else x
        # Hat Y convention: y=-1 is up/forward. The legacy non-inverted mapping
        # converts that to positive tilt speed. When reverse tilt is enabled,
        # the sign is flipped so joystick up/forward tilts the camera down.
        tilt_direction = y if self.invert.tilt else -y

        return HatPtzStep(
            pan_speed=self._scaled_signed_speed(pan_direction, self.config.fine_pan_speed, multiplier),
            tilt_speed=self._scaled_signed_speed(tilt_direction, self.config.fine_tilt_speed, multiplier),
            x=pan_direction,
            y=tilt_direction,
        )

    @staticmethod
    def _scaled_signed_speed(direction: int, configured_speed: int, multiplier: float) -> int:
        if direction == 0 or configured_speed <= 0:
            return 0
        speed = int(round(configured_speed * multiplier))
        return direction * max(1, speed)
