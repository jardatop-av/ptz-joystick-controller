from __future__ import annotations

from dataclasses import dataclass

from ..models.joystick import AxisInvertConfig
from ..models.joystick_input import NormalizedAxisState, PtzVelocity
from .throttle import ThrottleScaler


@dataclass(frozen=True)
class PtzSpeedScaler:
    invert: AxisInvertConfig
    throttle: ThrottleScaler

    def velocity_from_axes(self, axes: NormalizedAxisState) -> PtzVelocity:
        pan = -axes.pan if self.invert.pan else axes.pan
        tilt = -axes.tilt if self.invert.tilt else axes.tilt
        zoom = -axes.zoom if self.invert.zoom else axes.zoom
        multiplier = self.throttle.scale(axes.throttle)
        return PtzVelocity(
            pan=pan * multiplier,
            tilt=tilt * multiplier,
            zoom=zoom * multiplier,
            speed_multiplier=multiplier,
        )
