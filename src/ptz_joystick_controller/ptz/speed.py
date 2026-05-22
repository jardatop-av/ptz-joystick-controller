from __future__ import annotations

from dataclasses import dataclass

from ..models.ptz import PtzSpeedConfig


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def scale_axis_to_speed(value: float, minimum: int, maximum: int) -> int:
    magnitude = abs(clamp(value, -1.0, 1.0))
    if magnitude == 0.0 or maximum <= 0:
        return 0
    if maximum < minimum:
        raise ValueError("maximum speed must be greater than or equal to minimum speed")
    return round(minimum + (maximum - minimum) * magnitude)


@dataclass(frozen=True)
class PtzSpeedMapper:
    config: PtzSpeedConfig

    def pan_speed(self, value: float) -> int:
        return scale_axis_to_speed(value, self.config.pan_min, self.config.pan_max)

    def tilt_speed(self, value: float) -> int:
        return scale_axis_to_speed(value, self.config.tilt_min, self.config.tilt_max)

    def zoom_speed(self, value: float) -> int:
        return scale_axis_to_speed(value, self.config.zoom_min, self.config.zoom_max)


def throttle_to_multiplier(throttle: float, minimum: float = 0.2, maximum: float = 1.0) -> float:
    normalized = (clamp(throttle, -1.0, 1.0) + 1.0) / 2.0
    return minimum + (maximum - minimum) * normalized
