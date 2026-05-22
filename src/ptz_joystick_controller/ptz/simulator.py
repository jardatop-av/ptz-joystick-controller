from __future__ import annotations

from dataclasses import dataclass

from ..models.ptz import PtzCamera
from .session import CameraSession
from .transport import FakeViscaTransport, ReconnectSafeTransport


@dataclass
class OfflinePtzSimulation:
    session: CameraSession
    fake_transport: FakeViscaTransport

    @classmethod
    def for_camera(cls, camera: PtzCamera) -> "OfflinePtzSimulation":
        fake = FakeViscaTransport()
        session = CameraSession(camera=camera, transport=ReconnectSafeTransport(fake))
        return cls(session=session, fake_transport=fake)
