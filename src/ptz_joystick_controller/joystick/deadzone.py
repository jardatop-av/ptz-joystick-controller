from __future__ import annotations

from dataclasses import dataclass

from ..models.joystick import DeadzoneConfig
from ..models.joystick_input import NormalizedAxisState


def apply_deadzone(value: float, deadzone: float) -> float:
    if abs(value) <= deadzone:
        return 0.0
    if deadzone >= 1.0:
        return 0.0
    sign = 1.0 if value >= 0 else -1.0
    scaled = (abs(value) - deadzone) / (1.0 - deadzone)
    return sign * min(1.0, max(0.0, scaled))


@dataclass(frozen=True)
class DeadzoneProcessor:
    config: DeadzoneConfig

    def process(self, axes: NormalizedAxisState) -> NormalizedAxisState:
        return NormalizedAxisState(
            pan=apply_deadzone(axes.pan, self.config.pan),
            tilt=apply_deadzone(axes.tilt, self.config.tilt),
            zoom=apply_deadzone(axes.zoom, self.config.zoom),
            throttle=axes.throttle,
        )
