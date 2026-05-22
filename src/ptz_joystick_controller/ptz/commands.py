from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PanDirection(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    STOP = "stop"


class TiltDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    STOP = "stop"


class ZoomDirection(StrEnum):
    TELE = "tele"
    WIDE = "wide"
    STOP = "stop"


@dataclass(frozen=True)
class ViscaCommand:
    payload: bytes
    description: str


@dataclass(frozen=True)
class PanTiltCommand:
    pan_speed: int
    tilt_speed: int
    pan_direction: PanDirection
    tilt_direction: TiltDirection


@dataclass(frozen=True)
class ZoomCommand:
    speed: int
    direction: ZoomDirection


@dataclass(frozen=True)
class PtzCommandLogEntry:
    camera_id: str
    command: ViscaCommand
    packet: bytes
