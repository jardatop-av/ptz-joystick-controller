from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.models.joystick_input import PtzVelocity
from ptz_joystick_controller.models.ptz import PtzCamera
from ptz_joystick_controller.ptz.transport import (
    FakeViscaTransport,
    ReconnectSafeTransport,
    UdpViscaTransport,
    build_real_udp_transport,
)
from ptz_joystick_controller.runtime.application import RuntimeApplication
from ptz_joystick_controller.webui import create_web_app


@dataclass
class RecordingTransport:
    connected: bool = False
    sent_packets: list[bytes] = field(default_factory=list)

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def send(self, packet: bytes) -> None:
        self.connected = True
        self.sent_packets.append(packet)


def runtime_config(*, second_camera: bool = True, first_enabled: bool = True, first_host: str | None = "192.0.2.51"):
    cameras = [
        {
            "id": "cam1",
            "name": "Camera 1",
            "host": first_host,
            "port": 52381,
            "visca_id": 1,
            "enabled": first_enabled,
        }
    ]
    mappings = [{"source_id": "Input 1", "display_name": "Input 1", "ptz_camera_id": "cam1"}]
    if second_camera:
        cameras.append(
            {
                "id": "cam2",
                "name": "Camera 2",
                "host": "192.0.2.52",
                "port": 52382,
                "visca_id": 2,
                "enabled": True,
            }
        )
        mappings.append({"source_id": "Input 2", "display_name": "Input 2", "ptz_camera_id": "cam2"})
    return parse_config(
        {
            "switcher": {"type": "vmix", "host": "127.0.0.1", "port": 8088},
            "sources": {"mappings": mappings},
            "ptz": {"cameras": cameras},
            "webui": {"enabled": False},
        }
    )


def select_camera(app: RuntimeApplication, source_id: str = "Input 1") -> None:
    app.bridge.state.preview_source_id = source_id
    app.bridge.state.recompute_active_ptz()


def test_normal_runtime_uses_real_udp_transport_by_default() -> None:
    app = RuntimeApplication(runtime_config())

    assert app.ptz_transport_factory is build_real_udp_transport
    routed = app.bridge.ptz_router.sessions["cam1"]
    assert isinstance(routed.session.transport, ReconnectSafeTransport)
    assert isinstance(routed.session.transport.inner, UdpViscaTransport)
    assert routed.session.transport.inner.host == "192.0.2.51"
    assert routed.session.transport.inner.port == 52381


def test_dry_run_retains_fake_visca_transport() -> None:
    app = RuntimeApplication(runtime_config(), dry_run=True)

    assert app.ptz_transport_factory is None
    routed = app.bridge.ptz_router.sessions["cam1"]
    assert isinstance(routed.session.transport, ReconnectSafeTransport)
    assert isinstance(routed.session.transport.inner, FakeViscaTransport)


def test_injected_factory_overrides_production_default() -> None:
    created: dict[str, RecordingTransport] = {}

    def factory(camera: PtzCamera) -> RecordingTransport:
        transport = RecordingTransport()
        created[camera.id] = transport
        return transport

    app = RuntimeApplication(runtime_config(), ptz_transport_factory=factory)

    assert app.ptz_transport_factory is factory
    assert app.bridge.ptz_router.sessions["cam1"].session.transport is created["cam1"]


def test_pan_tilt_zoom_and_center_stop_reach_injected_transport() -> None:
    created: dict[str, RecordingTransport] = {}

    def factory(camera: PtzCamera) -> RecordingTransport:
        transport = RecordingTransport()
        created[camera.id] = transport
        return transport

    app = RuntimeApplication(runtime_config(), ptz_transport_factory=factory)
    select_camera(app)

    app.bridge.ptz_router.route_controls(PtzVelocity(pan=0.8, tilt=-0.5, zoom=0.7, speed_multiplier=1.0))
    packets_after_move = list(created["cam1"].sent_packets)
    assert len(packets_after_move) == 2  # combined pan/tilt and zoom

    app.bridge.ptz_router.route_controls(PtzVelocity())
    packets_after_center = created["cam1"].sent_packets
    assert len(packets_after_center) == 4  # pan/tilt stop and zoom stop
    assert packets_after_center[-2:] != packets_after_move[-2:]


def test_preview_change_stops_previous_camera_with_injected_transport() -> None:
    created: dict[str, RecordingTransport] = {}

    def factory(camera: PtzCamera) -> RecordingTransport:
        transport = RecordingTransport()
        created[camera.id] = transport
        return transport

    app = RuntimeApplication(runtime_config(), ptz_transport_factory=factory)
    select_camera(app, "Input 1")
    app.bridge.ptz_router.route_controls(PtzVelocity(pan=0.8, zoom=0.7))
    before = len(created["cam1"].sent_packets)

    app.bridge.preview_program.set_preview("Input 2")

    assert len(created["cam1"].sent_packets) == before + 2
    assert app.bridge.state.active_ptz_camera_id == "cam2"
    assert app.bridge.ptz_router.pan_tilt_active is False
    assert app.bridge.ptz_router.zoom_active is False


def test_enabled_camera_without_host_fails_clearly() -> None:
    with pytest.raises(ValueError, match="host is not configured: cam1"):
        RuntimeApplication(runtime_config(second_camera=False, first_host=None))


def test_disabled_camera_does_not_create_real_transport() -> None:
    calls: list[str] = []

    def factory(camera: PtzCamera) -> RecordingTransport:
        calls.append(camera.id)
        return RecordingTransport()

    app = RuntimeApplication(
        runtime_config(second_camera=False, first_enabled=False, first_host=None),
        ptz_transport_factory=factory,
    )

    assert calls == []
    assert app.bridge.ptz_router.sessions == {}


def test_diagnostics_visca_section_is_wide_and_target_does_not_wrap() -> None:
    app = RuntimeApplication(runtime_config(), dry_run=True)
    client = TestClient(create_web_app(app.status_provider))

    response = client.get("/diagnostics")

    assert response.status_code == 200
    assert 'class="visca-section"' in response.text
    assert 'class="visca-table"' in response.text
    assert 'class="visca-target"' in response.text
    assert "white-space: nowrap" in response.text
    assert 'class="visca-target mono"' in response.text
