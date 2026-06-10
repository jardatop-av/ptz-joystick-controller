from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from types import TracebackType

from ..models.ptz import PtzCamera
from .builder import ViscaCommandBuilder
from .commands import PtzCommandLogEntry, ViscaCommand, ZoomCommand, ZoomDirection
from .packet import ViscaPacketEncoder
from .speed import PtzSpeedMapper
from .transport import PtzTransport


@dataclass
class PtzState:
    camera_id: str
    moving: bool = False
    pan_tilt_active: bool = False
    zoom_active: bool = False
    last_command: str | None = None
    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 0.0


@dataclass
class CameraSession:
    camera: PtzCamera
    transport: PtzTransport
    encoder: ViscaPacketEncoder = field(default_factory=ViscaPacketEncoder)
    state: PtzState = field(init=False)
    command_log: list[PtzCommandLogEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.state = PtzState(camera_id=self.camera.id)

    @property
    def builder(self) -> ViscaCommandBuilder:
        return ViscaCommandBuilder(visca_id=self.camera.visca_id)

    @property
    def speed_mapper(self) -> PtzSpeedMapper:
        return PtzSpeedMapper(self.camera.speed)

    def _refresh_moving(self) -> None:
        self.state.moving = self.state.pan_tilt_active or self.state.zoom_active

    def send_command(self, command: ViscaCommand) -> bytes:
        packet = self.encoder.encode(command.payload)
        self.transport.send(packet)
        self.command_log.append(PtzCommandLogEntry(camera_id=self.camera.id, command=command, packet=packet))
        self.state.last_command = command.description
        return packet

    def pan_tilt_from_axes(self, pan: float, tilt: float) -> bytes:
        command = self.builder.pan_tilt_from_axes(pan, tilt, self.speed_mapper)
        packet = self.send_command(command)
        self.state.pan = pan
        self.state.tilt = tilt
        self.state.pan_tilt_active = pan != 0.0 or tilt != 0.0
        self._refresh_moving()
        return packet

    def zoom_from_axis(self, zoom: float) -> bytes:
        command = self.builder.zoom_from_axis(zoom, self.speed_mapper)
        packet = self.send_command(command)
        self.state.zoom = zoom
        self.state.zoom_active = zoom != 0.0
        self._refresh_moving()
        return packet

    def stop_pan_tilt(self, reason: str = "pan_tilt_stop") -> bytes:
        packet = self.send_command(self.builder.stop())
        self.state.pan = 0.0
        self.state.tilt = 0.0
        self.state.pan_tilt_active = False
        self._refresh_moving()
        self.state.last_command = f"pan_tilt_stop:{reason}"
        return packet

    def stop_zoom(self, reason: str = "zoom_stop") -> bytes:
        packet = self.send_command(self.builder.zoom(ZoomCommand(speed=0, direction=ZoomDirection.STOP)))
        self.state.zoom = 0.0
        self.state.zoom_active = False
        self._refresh_moving()
        self.state.last_command = f"zoom_stop:{reason}"
        return packet

    def stop_all(self, reason: str = "manual_stop") -> list[bytes]:
        packets = [self.stop_pan_tilt(reason=reason), self.stop_zoom(reason=reason)]
        self.state.last_command = f"stop:{reason}"
        return packets

    def stop(self, reason: str = "manual_stop") -> bytes:
        """Stop all tracked PTZ movement and return the pan/tilt stop packet.

        The return value preserves the old single-packet API while the method
        now also sends an independent zoom stop so pan/tilt and zoom state do
        not depend on each other.
        """
        packets = self.stop_all(reason=reason)
        return packets[0]

    def disconnect(self) -> None:
        self.transport.disconnect()


@dataclass
class SafeStopCameraSession(AbstractContextManager[CameraSession]):
    session: CameraSession
    reason: str = "script_exit"
    suppress_stop_errors: bool = True

    def __enter__(self) -> CameraSession:
        return self.session

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        try:
            self.session.stop_all(reason=self.reason)
        except Exception:
            if not self.suppress_stop_errors:
                raise
        finally:
            self.session.disconnect()
        return None
