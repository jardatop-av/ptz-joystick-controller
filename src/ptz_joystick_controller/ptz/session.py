from __future__ import annotations

from dataclasses import dataclass, field

from ..models.ptz import PtzCamera
from .builder import ViscaCommandBuilder
from .commands import PtzCommandLogEntry, ViscaCommand
from .packet import ViscaPacketEncoder
from .speed import PtzSpeedMapper
from .transport import PtzTransport


@dataclass
class PtzState:
    camera_id: str
    moving: bool = False
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
        self.state.moving = pan != 0.0 or tilt != 0.0 or self.state.zoom != 0.0
        return packet

    def zoom_from_axis(self, zoom: float) -> bytes:
        command = self.builder.zoom_from_axis(zoom, self.speed_mapper)
        packet = self.send_command(command)
        self.state.zoom = zoom
        self.state.moving = self.state.pan != 0.0 or self.state.tilt != 0.0 or zoom != 0.0
        return packet

    def stop(self, reason: str = "manual_stop") -> bytes:
        packet = self.send_command(self.builder.stop())
        self.state.moving = False
        self.state.pan = 0.0
        self.state.tilt = 0.0
        self.state.zoom = 0.0
        self.state.last_command = f"stop:{reason}"
        return packet
