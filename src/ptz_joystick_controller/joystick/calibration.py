from __future__ import annotations

from dataclasses import dataclass

from ..models.joystick_input import NormalizedAxisState, RawAxisState


@dataclass(frozen=True)
class AxisCalibration:
    minimum: int = -32768
    center: int = 0
    maximum: int = 32767

    def normalize(self, value: int) -> float:
        if value == self.center:
            return 0.0
        if value < self.center:
            span = max(1, self.center - self.minimum)
            return max(-1.0, min(0.0, (value - self.center) / span))
        span = max(1, self.maximum - self.center)
        return min(1.0, max(0.0, (value - self.center) / span))


@dataclass(frozen=True)
class JoystickCalibration:
    pan: AxisCalibration = AxisCalibration()
    tilt: AxisCalibration = AxisCalibration()
    zoom: AxisCalibration = AxisCalibration()
    throttle: AxisCalibration = AxisCalibration()

    def normalize_axes(self, axes: RawAxisState) -> NormalizedAxisState:
        return NormalizedAxisState(
            pan=self.pan.normalize(axes.pan),
            tilt=self.tilt.normalize(axes.tilt),
            zoom=self.zoom.normalize(axes.zoom),
            throttle=self.throttle.normalize(axes.throttle),
        )
