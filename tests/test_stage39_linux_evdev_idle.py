from __future__ import annotations

import errno
from types import SimpleNamespace

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.discovery import StaticJoystickDiscovery
from ptz_joystick_controller.joystick.linux_evdev import LinuxEvdevJoystickProvider
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.models.joystick_input import HatState, RawAxisState
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo


class FakeEcodes:
    EV_KEY = 1
    EV_ABS = 3
    bytype = {
        EV_ABS: {
            0: "ABS_X",
            1: "ABS_Y",
            5: "ABS_RZ",
            6: "ABS_THROTTLE",
            16: "ABS_HAT0X",
            17: "ABS_HAT0Y",
        },
        EV_KEY: {
            288: "BTN_TRIGGER",
        },
    }


class FakeEvdevDevice:
    def __init__(self) -> None:
        self.events: list[SimpleNamespace] = []
        self.raise_blocking = False
        self.raise_oserror = False

    def read(self):
        if self.raise_blocking:
            raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
        if self.raise_oserror:
            raise OSError(errno.ENODEV, "No such device")
        events = list(self.events)
        self.events.clear()
        return events


def make_provider(device: FakeEvdevDevice) -> LinuxEvdevJoystickProvider:
    provider = LinuxEvdevJoystickProvider.__new__(LinuxEvdevJoystickProvider)
    provider._evdev_categorize = lambda event: event
    provider._ecodes = FakeEcodes()
    provider._device = device
    provider._axes = RawAxisState()
    provider._hat = HatState()
    provider._pressed_buttons = set()
    provider._events = []
    provider._no_event_debug_logged = False
    return provider


def test_linux_evdev_poll_blockingioerror_returns_normally() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    device.raise_blocking = True

    provider.poll()

    snapshot = provider.snapshot()
    assert snapshot.axes == RawAxisState()
    assert snapshot.hat == HatState()


def test_linux_evdev_snapshot_after_no_events_returns_last_known_state() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=0, value=1234))
    assert provider.snapshot().axes.pan == 1234

    device.raise_blocking = True
    idle_snapshot = provider.snapshot()

    assert idle_snapshot.axes.pan == 1234


def test_linux_evdev_blockingioerror_does_not_emit_joystick_error() -> None:
    config = parse_config({"switcher": {"type": "vmix"}})
    bus = EventBus()
    events: list[str] = []
    bus.subscribe_all(lambda event: events.append(event.type))
    device_info = JoystickDeviceInfo(name="Logitech Extreme 3D Pro", path="/dev/input/event2", backend="evdev")
    evdev_device = FakeEvdevDevice()
    provider = make_provider(evdev_device)
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=StaticJoystickDiscovery((device_info,)),
        provider_factory=lambda _device: provider,
    )

    monitor.start()
    evdev_device.raise_blocking = True
    snapshot = monitor.poll()

    assert snapshot is not None
    assert monitor.health.connected is True
    assert "joystick.error" not in events
    assert "joystick.disconnected" not in events
    assert "joystick.snapshot" in events


def test_linux_evdev_real_oserror_triggers_runtime_disconnect() -> None:
    config = parse_config({"switcher": {"type": "vmix"}})
    bus = EventBus()
    events: list[str] = []
    bus.subscribe_all(lambda event: events.append(event.type))
    device_info = JoystickDeviceInfo(name="Logitech Extreme 3D Pro", path="/dev/input/event2", backend="evdev")
    evdev_device = FakeEvdevDevice()
    provider = make_provider(evdev_device)
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=StaticJoystickDiscovery((device_info,)),
        provider_factory=lambda _device: provider,
    )

    monitor.start()
    evdev_device.raise_oserror = True
    snapshot = monitor.poll()

    assert snapshot is None
    assert monitor.health.connected is False
    assert monitor.provider is None
    assert "joystick.disconnected" in events


def test_linux_evdev_idle_manual_poll_stays_connected() -> None:
    config = parse_config({"switcher": {"type": "vmix"}})
    bus = EventBus()
    device_info = JoystickDeviceInfo(name="Logitech Extreme 3D Pro", path="/dev/input/event2", backend="evdev")
    evdev_device = FakeEvdevDevice()
    provider = make_provider(evdev_device)
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=StaticJoystickDiscovery((device_info,)),
        provider_factory=lambda _device: provider,
    )

    monitor.start()
    evdev_device.raise_blocking = True
    for _ in range(3):
        assert monitor.poll() is not None
        assert monitor.health.connected is True
