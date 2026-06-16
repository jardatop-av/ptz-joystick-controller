from __future__ import annotations

from fastapi.testclient import TestClient

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.device import FakeJoystickInputProvider
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.models.joystick_input import RawAxisState
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.runtime.joystick_switcher_bridge import JoystickToSwitcherBridge
from ptz_joystick_controller.switchers.fake import FakeSwitcher
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


class StaticFakeJoystickDiscovery:
    def discover(self):
        return [JoystickDeviceInfo(name="Fake Runtime Joystick", path="fake0", backend="fake")]


def make_config():
    return parse_config(
        {
            "switcher": {"type": "vmix", "host": "127.0.0.1", "port": 8088},
            "sources": {
                "mappings": [
                    {"source_id": "Input 1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
                    {"source_id": "Input 2", "display_name": "Camera 2", "ptz_camera_id": "cam2"},
                    {"source_id": "Input 3", "display_name": "No PTZ", "ptz_camera_id": None},
                    {"source_id": "Input 4", "display_name": "No PTZ", "ptz_camera_id": None},
                ]
            },
            "ptz": {
                "cameras": [
                    {"id": "cam1", "name": "PTZ 1", "host": "192.0.2.11", "visca_id": 1},
                    {"id": "cam2", "name": "PTZ 2", "host": "192.0.2.12", "visca_id": 2},
                ]
            },
        }
    )


def make_client():
    config = make_config()
    bus = EventBus()
    fake_provider = FakeJoystickInputProvider()
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=StaticFakeJoystickDiscovery(),
        provider_factory=lambda _device: fake_provider,
    )
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 3", preview_source_id="Input 1")
    bridge = JoystickToSwitcherBridge(config, monitor, switcher, bus, dry_run=False)
    bridge.start()
    status_provider = RuntimeStatusProvider.from_bridge(bridge)
    client = TestClient(create_web_app(status_provider))
    return bridge, fake_provider, bus, client


def test_diagnostics_route_returns_200() -> None:
    _bridge, _provider, _bus, client = make_client()
    response = client.get("/diagnostics")
    assert response.status_code == 200
    assert "Runtime Diagnostics" in response.text
    assert "/api/diagnostics" in response.text


def test_api_diagnostics_returns_expected_structure() -> None:
    _bridge, _provider, _bus, client = make_client()
    response = client.get("/api/diagnostics")
    assert response.status_code == 200
    data = response.json()
    assert {"runtime_events", "ptz_actions", "visca_packets", "joystick", "switcher"}.issubset(data)
    assert data["joystick"]["connected"] is True
    assert data["switcher"]["preview_source"] == "Input 1"


def test_ptz_action_and_visca_packet_are_recorded() -> None:
    bridge, provider, _bus, client = make_client()
    provider.set_axes(RawAxisState(pan=32767, throttle=32767))
    bridge.poll_once()

    data = client.get("/api/diagnostics").json()
    assert any(action["action_type"] == "ptz.pan_tilt_move" for action in data["ptz_actions"])
    assert data["visca_packets"]
    assert data["visca_packets"][0]["direction"] == "send"
    assert data["visca_packets"][0]["hex_payload"]


def test_runtime_event_buffer_is_bounded() -> None:
    _bridge, _provider, bus, client = make_client()
    for i in range(130):
        bus.publish("diagnostic.test", {"value": i})
    data = client.get("/api/diagnostics").json()
    assert len(data["runtime_events"]) == 100
    assert data["runtime_events"][0]["details"]["value"] == 129


def test_empty_diagnostics_render_without_crash() -> None:
    _bridge, _provider, _bus, client = make_client()
    response = client.get("/diagnostics")
    payload = client.get("/api/diagnostics")
    assert response.status_code == 200
    assert payload.status_code == 200
