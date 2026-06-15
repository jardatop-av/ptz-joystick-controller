from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.joystick.windows_pygame import WindowsPygameJoystickProvider
from ptz_joystick_controller.models.joystick_input import JoystickSnapshot, RawAxisState
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.runtime.joystick_switcher_bridge import JoystickToSwitcherBridge
from ptz_joystick_controller.switchers.fake import FakeSwitcher
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


class FakePygameJoystick:
    def __init__(self, instance_id: int = 42) -> None:
        self.instance_id = instance_id
        self.axes = [0.5, 0.0, 0.0, 0.0]
        self.hat = (0, 0)

    def init(self) -> None:
        pass

    def get_instance_id(self) -> int:
        return self.instance_id

    def get_name(self) -> str:
        return "Logitech Extreme 3D Pro"

    def get_numaxes(self) -> int:
        return len(self.axes)

    def get_axis(self, index: int) -> float:
        return self.axes[index]

    def get_numhats(self) -> int:
        return 1

    def get_hat(self, index: int):
        return self.hat


class FakePygameEventModule:
    def __init__(self) -> None:
        self.events = []
        self.pump_count = 0

    def pump(self) -> None:
        self.pump_count += 1

    def get(self):
        events = list(self.events)
        self.events.clear()
        return events


class FakePygameJoystickModule:
    def __init__(self, joystick: FakePygameJoystick) -> None:
        self.joystick = joystick
        self.count = 1

    def init(self) -> None:
        pass

    def get_count(self) -> int:
        return self.count

    def Joystick(self, index: int) -> FakePygameJoystick:  # noqa: N802 - mirrors pygame API
        if index >= self.count:
            raise RuntimeError("index not available")
        return self.joystick


class FakePygame:
    JOYBUTTONDOWN = 10
    JOYBUTTONUP = 11
    JOYDEVICEREMOVED = 12

    def __init__(self) -> None:
        self._joystick = FakePygameJoystick()
        self.event = FakePygameEventModule()
        self.joystick = FakePygameJoystickModule(self._joystick)

    def init(self) -> None:
        pass


def test_windows_pygame_provider_raises_on_matching_device_removed_event() -> None:
    pygame = FakePygame()
    provider = WindowsPygameJoystickProvider(pygame_module=pygame)

    assert provider.snapshot().axes.pan > 0
    pygame.event.events.append(SimpleNamespace(type=pygame.JOYDEVICEREMOVED, instance_id=42))

    try:
        provider.snapshot()
    except RuntimeError as exc:
        assert "Joystick disconnected" in str(exc)
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("provider did not raise on JOYDEVICEREMOVED")
    assert pygame.event.pump_count >= 2


def test_windows_pygame_provider_raises_when_device_count_drops_to_zero() -> None:
    pygame = FakePygame()
    provider = WindowsPygameJoystickProvider(pygame_module=pygame)

    pygame.joystick.count = 0

    try:
        provider.button_events()
    except RuntimeError as exc:
        assert "Joystick disconnected" in str(exc)
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("provider did not raise when pygame joystick count became zero")


class ToggleDiscovery:
    def __init__(self) -> None:
        self.device = JoystickDeviceInfo(name="Windows Logitech", path="pygame0", backend="pygame")
        self.available = True

    def discover(self):
        return [self.device] if self.available else []


class DisconnectAfterFirstSnapshotProvider:
    def __init__(self) -> None:
        self.connected = True
        self.snapshot_calls = 0

    def snapshot(self) -> JoystickSnapshot:
        self.snapshot_calls += 1
        if not self.connected:
            raise RuntimeError("Joystick disconnected")
        return JoystickSnapshot(axes=RawAxisState(pan=32767, zoom=32767))

    def button_events(self):
        if not self.connected:
            raise RuntimeError("Joystick disconnected")
        return ()


def _bridge_with_provider(provider: DisconnectAfterFirstSnapshotProvider):
    config = parse_config(
        {
            "switcher": {"type": "vmix", "host": "127.0.0.1", "port": 8088},
            "sources": {
                "mappings": [
                    {"source_id": "Input 1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
                    {"source_id": "Input 2", "display_name": "Camera 2", "ptz_camera_id": None},
                ]
            },
            "ptz": {"cameras": [{"id": "cam1", "name": "PTZ 1", "host": "192.0.2.11", "visca_id": 1}]},
        }
    )
    bus = EventBus()
    discovery = ToggleDiscovery()
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=discovery,
        provider_factory=lambda _device: provider,
    )
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 2", preview_source_id="Input 1")
    bridge = JoystickToSwitcherBridge(config, monitor, switcher, bus, dry_run=False)
    bridge.start()
    client = TestClient(create_web_app(RuntimeStatusProvider.from_bridge(bridge)))
    return bridge, provider, discovery, client


def test_runtime_converts_windows_provider_exception_to_disconnect_and_status_clear() -> None:
    provider = DisconnectAfterFirstSnapshotProvider()
    bridge, provider, discovery, client = _bridge_with_provider(provider)
    bridge.poll_once()
    before = client.get("/api/status").json()
    assert before["joystick"]["connected"] is True
    assert any(event["type"] == "joystick.snapshot" for event in before["recent_activity"])

    provider.connected = False
    discovery.available = False
    bridge.poll_once()
    bridge.poll_once()

    after = client.get("/api/status").json()
    assert after["joystick"]["connected"] is False
    assert after["joystick"]["device_name"] is None
    assert after["joystick"]["pressed_buttons"] == []
    assert after["joystick"]["hat"]["direction"] == "center"
    assert after["joystick"]["normalized_axes"]["pan"] == 0.0
    assert after["joystick"]["normalized_axes"]["zoom"] == 0.0
    event_types = [event["type"] for event in after["recent_activity"]]
    assert "joystick.disconnected" in event_types
    assert event_types.count("joystick.disconnected") == 1


def test_runtime_reconnects_after_windows_provider_disconnect() -> None:
    first = DisconnectAfterFirstSnapshotProvider()
    second = DisconnectAfterFirstSnapshotProvider()
    config = parse_config({"switcher": {"type": "vmix"}})
    bus = EventBus()
    discovery = ToggleDiscovery()
    providers = [first, second]
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=discovery,
        provider_factory=lambda _device: providers.pop(0),
    )

    monitor.start()
    assert monitor.health.connected is True
    first.connected = False
    assert monitor.poll() is None
    assert monitor.health.connected is False

    snapshot = monitor.poll()
    assert snapshot is not None
    assert monitor.health.connected is True
    assert monitor.provider is second
