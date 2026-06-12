from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..models.ptz import PtzCamera
from .builder import ViscaCommandBuilder
from .commands import ViscaCommand
from .packet import ViscaPacketEncoder
from .transport import PtzTransport


@dataclass(frozen=True)
class PtzPresetRecall:
    """Recall-only PTZ preset request."""

    camera_id: str
    preset_number: int


class PtzPresetTransport(Protocol):
    def recall_preset(self, camera: PtzCamera, preset_number: int) -> bytes: ...


@dataclass
class ViscaPresetRecallTransport:
    """Preset recall transport using an existing VISCA PTZ transport."""

    transport: PtzTransport
    encoder: ViscaPacketEncoder = field(default_factory=ViscaPacketEncoder)

    def recall_preset(self, camera: PtzCamera, preset_number: int) -> bytes:
        command = ViscaCommandBuilder(visca_id=camera.visca_id).preset_recall(preset_number)
        packet = self.encoder.encode(command.payload)
        self.transport.send(packet)
        return packet


@dataclass
class FakePresetTransport:
    """Fake recall-only transport for offline tests."""

    recalls: list[PtzPresetRecall] = field(default_factory=list)
    packets: list[bytes] = field(default_factory=list)

    def recall_preset(self, camera: PtzCamera, preset_number: int) -> bytes:
        command = ViscaCommandBuilder(visca_id=camera.visca_id).preset_recall(preset_number)
        packet = ViscaPacketEncoder().encode(command.payload)
        self.recalls.append(PtzPresetRecall(camera_id=camera.id, preset_number=preset_number))
        self.packets.append(packet)
        return packet
