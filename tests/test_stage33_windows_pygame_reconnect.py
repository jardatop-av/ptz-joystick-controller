from __future__ import annotations

from fastapi.testclient import TestClient

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.discovery import WindowsPygameJoystickDiscovery
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.models.joystick_input import JoystickSnapshot, RawAxisState
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


class FakePygameJoystick:
    def __init__(self, name: str = "Logitech Extreme 3D Pro") -> None:
        self.name = name
        self.init_calls = 0

    def init(self) -> None:
        self.init_calls += 1

    def get_name(self) -> str:
        return self.name


class FakePygameEvent:
    def __init__(self) -> None:
        self.pump_calls = 0
        self.clear_calls = 0

    def pump(self) -> None:
        self.pump_calls += 1

    def clear(self) -> None:
        self.clear_calls += 1


class FakePygameJoystickModule:
    def __init__(self) -> None:
        self.devices: list[FakePygameJoystick] = []
        self.quit_calls = 0
        self.init_calls = 0
        self.created: list[FakePygameJoystick] = []

    def quit(self) -> None:
        self.quit_calls += 1

    def init(self) -> None:
        self.init_calls += 1

    def get_count(self) -> int:
        return len(self.devices)

    def Joystick(self, index: int) -> FakePygameJoystick:  # noqa: N802 - pygame API compatibility
        joystick = self.devices[index]
        self.created.append(joystick)
        return joystick


class FakePygame:
    def __init__(self) -> None:
        self.event = FakePygameEvent()
        self.joystick = FakePygameJoystickModule()
        self.init_calls = 0

    def init(self) -> None:
        self.init_calls += 1


class ToggleDiscovery:
    def __init__(self) -> None:
        self.available = True
        self.device = JoystickDeviceInfo(name="Logitech Extreme 3D Pro", path="0", backend="pygame")

    def discover(self):
        return [self.device] if self.available else []


class DisconnectableProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.connected = True

    def snapshot(self) -> JoystickSnapshot:
        if not self.connected:
            raise RuntimeError("Joystick disconnected")
        return JoystickSnapshot(axes=RawAxisState(pan=1234))

    def button_events(self):
        if not self.connected:
            raise RuntimeError("Joystick disconnected")
        return ()


def test_windows_discovery_reinitializes_pygame_joystick_each_scan() -> None:
    pygame = FakePygame()
    discovery = WindowsPygameJoystickDiscovery(pygame_module=pygame)

    pygame.joystick.devices = []
    assert discovery.discover() == []
    assert pygame.joystick.quit_calls == 1
    assert pygame.joystick.init_calls == 1
    assert pygame.event.pump_calls == 1
    assert pygame.event.clear_calls == 1

    pygame.joystick.devices = [FakePygameJoystick()]
    devices = discovery.discover()
    assert len(devices) == 1
    assert devices[0].name == "Logitech Extreme 3D Pro"
    assert devices[0].path == "0"
    assert pygame.joystick.quit_calls == 2
    assert pygame.joystick.init_calls == 2
    assert pygame.event.pump_calls == 2
    assert pygame.event.clear_calls == 2


def test_runtime_disconnect_clears_provider_and_reconnect_uses_new_provider() -> None:
    config = parse_config({"switcher": {"type": "vmix"}})
    bus = EventBus()
    discovery = ToggleDiscovery()
    first = DisconnectableProvider("old")
    second = DisconnectableProvider("new")
    providers = [first, second]
    events: list[str] = []
    bus.subscribe_all(lambda event: events.append(event.type))
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=discovery,
        provider_factory=lambda _device: providers.pop(0),
    )

    monitor.start()
    assert monitor.provider is first
    assert monitor.health.connected is True

    first.connected = False
    assert monitor.poll() is None
    assert monitor.provider is None
    assert monitor.health.connected is False
    assert "joystick.disconnected" in events

    snapshot = monitor.poll()
    assert snapshot is not None
    assert monitor.provider is second
    assert monitor.health.connected is True
    assert events.count("joystick.connected") == 2


def test_dashboard_changes_disconnected_to_connected_after_reconnect() -> None:
    config = parse_config({"switcher": {"type": "vmix"}})
    bus = EventBus()
    discovery = ToggleDiscovery()
    first = DisconnectableProvider("old")
    second = DisconnectableProvider("new")
    providers = [first, second]
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=discovery,
        provider_factory=lambda _device: providers.pop(0),
    )
    status = RuntimeStatusProvider(state=AppState(config), event_bus=bus, joystick_health=monitor.health, joystick_monitor=monitor)
    client = TestClient(create_web_app(status))

    monitor.start()
    assert client.get("/api/status").json()["joystick"]["connected"] is True

    first.connected = False
    monitor.poll()
    disconnected = client.get("/api/status").json()
    assert disconnected["joystick"]["connected"] is False
    assert disconnected["joystick"]["device_name"] is None

    monitor.poll()
    reconnected = client.get("/api/status").json()
    assert reconnected["joystick"]["connected"] is True
    assert reconnected["joystick"]["device_name"] == "Logitech Extreme 3D Pro"


def test_stale_old_provider_is_not_reused_after_reconnect() -> None:
    config = parse_config({"switcher": {"type": "vmix"}})
    bus = EventBus()
    discovery = ToggleDiscovery()
    first = DisconnectableProvider("old")
    second = DisconnectableProvider("new")
    created: list[DisconnectableProvider] = []

    def factory(_device: JoystickDeviceInfo) -> DisconnectableProvider:
        provider = second if created else first
        created.append(provider)
        return provider

    monitor = JoystickRuntimeMonitor(config=config, event_bus=bus, discovery=discovery, provider_factory=factory)
    monitor.start()
    first.connected = False
    monitor.poll()
    monitor.poll()

    assert created == [first, second]
    assert monitor.provider is second
    assert monitor.provider is not first
