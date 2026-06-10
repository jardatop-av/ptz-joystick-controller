from __future__ import annotations

import socket

import pytest

from ptz_joystick_controller.models.ptz import PtzCamera
from ptz_joystick_controller.ptz import CameraSession, FakeViscaTransport, ReconnectSafeTransport, SafeStopCameraSession
from ptz_joystick_controller.ptz.transport import UdpViscaTransport, build_real_udp_transport
from ptz_joystick_controller.runtime.ptz_router import PtzRouter
from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.app_state import AppState


class DummySocket:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendto(self, packet: bytes, address: tuple[str, int]) -> int:
        self.sent.append((packet, address))
        return len(packet)

    def close(self) -> None:
        self.closed = True


def test_udp_transport_interface_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummySocket()
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: dummy)

    transport = UdpViscaTransport(host="192.0.2.10", port=52381, timeout_seconds=0.25)
    session = CameraSession(camera=PtzCamera(id="cam1", name="Camera 1", host="192.0.2.10"), transport=transport)

    packet = session.stop(reason="compatibility")

    assert transport.connected is True
    assert transport.sent_packets == [packet]
    assert dummy.timeout == 0.25
    assert dummy.sent == [(packet, ("192.0.2.10", 52381))]


def test_safe_stop_on_exit_sends_stop_and_disconnects() -> None:
    fake = FakeViscaTransport()
    session = CameraSession(
        camera=PtzCamera(id="cam1", name="Camera 1"),
        transport=ReconnectSafeTransport(fake),
    )

    with SafeStopCameraSession(session, reason="test_exit") as active:
        active.pan_tilt_from_axes(1.0, 0.0)
        assert fake.connected is True

    assert session.state.moving is False
    assert session.state.last_command == "stop:test_exit"
    assert fake.disconnect_count == 1
    assert len(fake.sent_packets) == 2


def test_invalid_or_missing_camera_host_rejected() -> None:
    camera = PtzCamera(id="cam1", name="Camera 1", host=None)
    with pytest.raises(ValueError, match="host is not configured"):
        UdpViscaTransport.from_camera(camera)

    with pytest.raises(ValueError, match="host must be configured"):
        UdpViscaTransport(host=" ")


def test_build_real_udp_transport_from_camera_config() -> None:
    camera = PtzCamera(id="cam1", name="Camera 1", host="192.0.2.20", port=1259, visca_id=2)
    transport = build_real_udp_transport(camera, timeout_seconds=0.75)

    assert isinstance(transport.inner, UdpViscaTransport)
    assert transport.inner.host == "192.0.2.20"
    assert transport.inner.port == 1259
    assert transport.inner.timeout_seconds == 0.75


def test_no_command_sent_when_camera_disabled() -> None:
    config = parse_config(
        {
            "switcher": {"type": "vmix", "host": None, "port": 8088},
            "sources": {"mappings": [{"source_id": "Input 1", "display_name": "Cam 1", "ptz_camera_id": "cam1"}]},
            "ptz": {
                "cameras": [
                    {"id": "cam1", "name": "Disabled Cam", "host": "192.0.2.30", "enabled": False}
                ]
            },
        }
    )
    state = AppState(config=config)
    state.preview_source_id = "Input 1"
    assert state.recompute_active_ptz() == "cam1"

    router = PtzRouter(state=state, event_bus=EventBus())

    assert "cam1" not in router.sessions
    assert router.stop(reason="disabled_camera") is False
    assert router.camera_command_count("cam1") == 0
