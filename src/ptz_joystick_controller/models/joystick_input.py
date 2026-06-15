from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class JoystickAxis(StrEnum):
    PAN = "pan"
    TILT = "tilt"
    ZOOM = "zoom"
    THROTTLE = "throttle"


class HatDirection(StrEnum):
    CENTER = "center"
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    UP_LEFT = "up_left"
    UP_RIGHT = "up_right"
    DOWN_LEFT = "down_left"
    DOWN_RIGHT = "down_right"


@dataclass(frozen=True)
class RawAxisState:
    pan: int = 0
    tilt: int = 0
    zoom: int = 0
    throttle: int = 0


@dataclass(frozen=True)
class NormalizedAxisState:
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0
    throttle: float = 0.0


@dataclass(frozen=True)
class PtzVelocity:
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0
    speed_multiplier: float = 1.0


@dataclass(frozen=True)
class HatState:
    x: int = 0
    y: int = 0

    @property
    def direction(self) -> HatDirection:
        if self.x == 0 and self.y == 0:
            return HatDirection.CENTER
        if self.x == 0 and self.y < 0:
            return HatDirection.UP
        if self.x == 0 and self.y > 0:
            return HatDirection.DOWN
        if self.x < 0 and self.y == 0:
            return HatDirection.LEFT
        if self.x > 0 and self.y == 0:
            return HatDirection.RIGHT
        if self.x < 0 and self.y < 0:
            return HatDirection.UP_LEFT
        if self.x > 0 and self.y < 0:
            return HatDirection.UP_RIGHT
        if self.x < 0 and self.y > 0:
            return HatDirection.DOWN_LEFT
        return HatDirection.DOWN_RIGHT


@dataclass(frozen=True)
class HatPtzStep:
    pan_speed: int = 0
    tilt_speed: int = 0
    x: int = 0
    y: int = 0

    @property
    def moving(self) -> bool:
        return self.pan_speed != 0 or self.tilt_speed != 0


@dataclass(frozen=True)
class ButtonEvent:
    button_name: str
    pressed: bool


@dataclass(frozen=True)
class JoystickSnapshot:
    axes: RawAxisState = RawAxisState()
    hat: HatState = HatState()
    pressed_buttons: frozenset[str] = frozenset()
