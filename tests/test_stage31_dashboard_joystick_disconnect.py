from __future__ import annotations

from fastapi.testclient import TestClient

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.device import FakeJoystickInputProvider
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.models.joystick_input import HatState, JoystickSnapshot, RawAxisState
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.runtime.joystick_switcher_bridge import JoystickToSwitcherBridge
from ptz_joystick_controller.switchers.fake import FakeSwitcher
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


class ToggleDiscovery:
    def __init__(self) -> None:
        self.device = JoystickDeviceInfo(name="Fake Runtime Joystick", path="fake0", backend="fake")
        self.available = True

    def discover(self):
        return [self.device] if self.available else []


class DisconnectableFakeJoystick(FakeJoystickInputProvider):
    def __init__(self) -> None:
        super().__init__()
        self.available = True

    def snapshot(self) -> JoystickSnapshot:  # type: ignore[override]
        if not self.available:
            raise OSError("device unplugged")
        return super().snapshot()


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


def make_dashboard():
    config = make_config()
    bus = EventBus()
    joystick = DisconnectableFakeJoystick()
    discovery = ToggleDiscovery()
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=discovery,
        provider_factory=lambda _device: joystick,
    )
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 3", preview_source_id="Input 1")
    bridge = JoystickToSwitcherBridge(config, monitor, switcher, bus, dry_run=False)
    bridge.start()
    client = TestClient(create_web_app(RuntimeStatusProvider.from_bridge(bridge)))
    return bridge, joystick, discovery, client


def test_disconnect_updates_live_dashboard_status_and_clears_controls() -> None:
    bridge, joystick, discovery, client = make_dashboard()
    joystick.set_axes(RawAxisState(pan=32767, tilt=-32768, zoom=12345, throttle=32767))
    joystick.set_hat(HatState(x=-1, y=-1))
    joystick.press("button_5")
    bridge.poll_once()

    joystick.available = False
    discovery.available = False
    bridge.poll_once()

    data = client.get("/api/status").json()
    assert data["joystick"]["connected"] is False
    assert data["joystick"]["device_name"] is None
    assert data["joystick"]["pressed_buttons"] == []
    assert data["joystick"]["hat"]["direction"] == "center"
    assert data["joystick"]["normalized_axes"]["pan"] == 0.0
    assert data["joystick"]["normalized_axes"]["tilt"] == 0.0
    assert data["joystick"]["normalized_axes"]["zoom"] == 0.0
    assert "joystick.disconnected" in [event["type"] for event in data["recent_activity"]]


def test_disconnect_sends_ptz_safe_stop_if_moving() -> None:
    bridge, joystick, discovery, _client = make_dashboard()
    joystick.set_axes(RawAxisState(pan=32767, zoom=32767, throttle=32767))
    bridge.poll_once()
    assert bridge.ptz_router.pan_tilt_active is True
    assert bridge.ptz_router.zoom_active is True

    joystick.available = False
    discovery.available = False
    bridge.poll_once()

    assert bridge.ptz_router.pan_tilt_active is False
    assert bridge.ptz_router.zoom_active is False
    assert any("pan_tilt_stop reason=joystick_disconnected" in item for item in bridge.ptz_router.command_log)
    assert any("zoom_stop reason=joystick_disconnected" in item for item in bridge.ptz_router.command_log)


def test_disconnect_stops_publishing_stale_snapshots() -> None:
    bridge, joystick, discovery, client = make_dashboard()
    bridge.poll_once()
    before = client.get("/api/status").json()
    before_snapshot_count = sum(1 for event in before["recent_activity"] if event["type"] == "joystick.snapshot")

    joystick.available = False
    discovery.available = False
    bridge.poll_once()
    bridge.poll_once()

    after = client.get("/api/status").json()
    after_snapshot_count = sum(1 for event in after["recent_activity"] if event["type"] == "joystick.snapshot")
    disconnect_count = sum(1 for event in after["recent_activity"] if event["type"] == "joystick.disconnected")
    assert after_snapshot_count == before_snapshot_count
    assert disconnect_count == 1


def test_reconnect_updates_dashboard_status_again() -> None:
    bridge, joystick, discovery, client = make_dashboard()
    joystick.available = False
    discovery.available = False
    bridge.poll_once()
    assert client.get("/api/status").json()["joystick"]["connected"] is False

    joystick.available = True
    discovery.available = True
    joystick.set_axes(RawAxisState(pan=32767))
    bridge.poll_once()

    data = client.get("/api/status").json()
    assert data["joystick"]["connected"] is True
    assert data["joystick"]["device_name"] == "Fake Runtime Joystick"
    assert data["joystick"]["normalized_axes"]["pan"] != 0.0
    assert "joystick.connected" in [event["type"] for event in data["recent_activity"]]
