from __future__ import annotations

from fastapi.testclient import TestClient

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.device import FakeJoystickInputProvider
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.models.joystick_input import HatState, RawAxisState
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
            "joystick": {
                "buttons": {
                    "trigger": {"action": "cut"},
                    "thumb": {"action": "copy_program_to_preview"},
                    "button_3": {"action": "preview_source", "source_id": "Input 1"},
                    "button_4": {"action": "preview_source", "source_id": "Input 2"},
                    "button_11": {"action": "none"},
                }
            },
        }
    )


def make_live_dashboard():
    config = make_config()
    bus = EventBus()
    provider = FakeJoystickInputProvider()
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=StaticFakeJoystickDiscovery(),
        provider_factory=lambda _device: provider,
    )
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 3", preview_source_id="Input 1")
    bridge = JoystickToSwitcherBridge(config, monitor, switcher, bus, dry_run=False)
    bridge.start()
    status_provider = RuntimeStatusProvider.from_bridge(bridge)
    client = TestClient(create_web_app(status_provider))
    return bridge, provider, switcher, client


def test_runtime_dashboard_reads_live_joystick_state_without_restart() -> None:
    bridge, provider, _switcher, client = make_live_dashboard()

    provider.set_axes(RawAxisState(pan=32767, tilt=-32768, zoom=12345, throttle=32767))
    provider.set_hat(HatState(x=-1, y=-1))
    provider.press("button_5")
    bridge.poll_once()

    data = client.get("/api/status").json()
    assert data["joystick"]["connected"] is True
    assert data["joystick"]["device_name"] == "Fake Runtime Joystick"
    assert "button_5" in data["joystick"]["pressed_buttons"]
    assert data["joystick"]["hat"]["direction"] == "up_left"
    assert abs(data["joystick"]["normalized_axes"]["pan"]) > 0.9
    assert abs(data["joystick"]["normalized_axes"]["tilt"]) > 0.9
    assert abs(data["joystick"]["normalized_axes"]["zoom"]) > 0.1


def test_runtime_dashboard_reads_live_switcher_preview_program_without_restart() -> None:
    bridge, _provider, switcher, client = make_live_dashboard()
    initial = client.get("/api/status").json()
    assert initial["switcher"]["connected"] is True
    assert initial["program"] == "Input 3"
    assert initial["preview"] == "Input 1"

    switcher.set_preview_source("Input 2")
    switcher.program_source_id = "Input 1"
    bridge.poll_once()

    updated = client.get("/api/status").json()
    assert updated["program"] == "Input 1"
    assert updated["preview"] == "Input 2"
    assert updated["active_ptz_camera"] == "cam2"


def test_runtime_dashboard_reads_live_ptz_moving_state_and_last_action() -> None:
    bridge, provider, _switcher, client = make_live_dashboard()
    provider.set_axes(RawAxisState(pan=32767, tilt=0, zoom=32767, throttle=32767))
    bridge.poll_once()

    data = client.get("/api/status").json()
    assert data["ptz"]["active_camera"] == "cam1"
    assert data["ptz"]["moving"] is True
    assert data["ptz"]["pan_tilt_active"] is True
    assert data["ptz"]["zoom_active"] is True
    assert data["ptz"]["hat_active"] is False
    assert data["ptz"]["last_action"] is not None


def test_runtime_dashboard_recent_activity_uses_runtime_events() -> None:
    bridge, provider, _switcher, client = make_live_dashboard()
    provider.set_axes(RawAxisState(pan=32767, throttle=32767))
    bridge.poll_once()

    data = client.get("/api/status").json()
    event_types = [event["type"] for event in data["recent_activity"]]
    assert "joystick.snapshot" in event_types
    assert "ptz.pan_tilt_move" in event_types


def test_runtime_dashboard_serializes_dataclass_event_payloads() -> None:
    bridge, _provider, _switcher, client = make_live_dashboard()
    bridge.poll_once()

    response = client.get("/api/status")
    assert response.status_code == 200
    payloads = [event["payload"] for event in response.json()["recent_activity"]]
    assert all(isinstance(payload, dict) for payload in payloads)
