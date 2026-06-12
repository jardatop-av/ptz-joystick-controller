from __future__ import annotations

from dataclasses import dataclass

from .commands import PanDirection, PanTiltCommand, TiltDirection, ViscaCommand, ZoomCommand, ZoomDirection
from .speed import PtzSpeedMapper


def _camera_address(visca_id: int) -> int:
    if not 1 <= visca_id <= 7:
        raise ValueError("VISCA camera id must be in range 1..7")
    return 0x80 + visca_id


def _direction_byte(direction: PanDirection | TiltDirection) -> int:
    match direction:
        case PanDirection.LEFT:
            return 0x01
        case PanDirection.RIGHT:
            return 0x02
        case PanDirection.STOP:
            return 0x03
        case TiltDirection.UP:
            return 0x01
        case TiltDirection.DOWN:
            return 0x02
        case TiltDirection.STOP:
            return 0x03
    raise ValueError(f"Unsupported direction: {direction}")


def _zoom_byte(command: ZoomCommand) -> int:
    if command.direction == ZoomDirection.STOP or command.speed == 0:
        return 0x00
    if not 0 <= command.speed <= 7:
        raise ValueError("VISCA zoom speed must be in range 0..7")
    if command.direction == ZoomDirection.TELE:
        return 0x20 + command.speed
    if command.direction == ZoomDirection.WIDE:
        return 0x30 + command.speed
    raise ValueError(f"Unsupported zoom direction: {command.direction}")


@dataclass(frozen=True)
class ViscaCommandBuilder:
    visca_id: int = 1

    def pan_tilt(self, command: PanTiltCommand) -> ViscaCommand:
        if not 0 <= command.pan_speed <= 24:
            raise ValueError("VISCA pan speed must be in range 0..24")
        if not 0 <= command.tilt_speed <= 20:
            raise ValueError("VISCA tilt speed must be in range 0..20")
        payload = bytes(
            [
                _camera_address(self.visca_id),
                0x01,
                0x06,
                0x01,
                command.pan_speed,
                command.tilt_speed,
                _direction_byte(command.pan_direction),
                _direction_byte(command.tilt_direction),
                0xFF,
            ]
        )
        return ViscaCommand(payload=payload, description="pan_tilt")

    def zoom(self, command: ZoomCommand) -> ViscaCommand:
        payload = bytes([_camera_address(self.visca_id), 0x01, 0x04, 0x07, _zoom_byte(command), 0xFF])
        return ViscaCommand(payload=payload, description="zoom")

    def stop(self) -> ViscaCommand:
        return self.pan_tilt(
            PanTiltCommand(
                pan_speed=0,
                tilt_speed=0,
                pan_direction=PanDirection.STOP,
                tilt_direction=TiltDirection.STOP,
            )
        )

    def preset_recall(self, preset_number: int) -> ViscaCommand:
        if not 0 <= preset_number <= 255:
            raise ValueError("VISCA preset number must be in range 0..255")
        payload = bytes([_camera_address(self.visca_id), 0x01, 0x04, 0x3F, 0x02, preset_number, 0xFF])
        return ViscaCommand(payload=payload, description=f"preset_recall:{preset_number}")

    def pan_tilt_from_axes(self, pan: float, tilt: float, speed: PtzSpeedMapper) -> ViscaCommand:
        pan_speed = speed.pan_speed(pan)
        tilt_speed = speed.tilt_speed(tilt)
        pan_direction = PanDirection.STOP if pan_speed == 0 else PanDirection.RIGHT if pan > 0 else PanDirection.LEFT
        tilt_direction = TiltDirection.STOP if tilt_speed == 0 else TiltDirection.UP if tilt > 0 else TiltDirection.DOWN
        return self.pan_tilt(PanTiltCommand(pan_speed, tilt_speed, pan_direction, tilt_direction))

    def zoom_from_axis(self, zoom: float, speed: PtzSpeedMapper) -> ViscaCommand:
        zoom_speed = speed.zoom_speed(zoom)
        direction = ZoomDirection.STOP if zoom_speed == 0 else ZoomDirection.TELE if zoom > 0 else ZoomDirection.WIDE
        return self.zoom(ZoomCommand(speed=zoom_speed, direction=direction))
