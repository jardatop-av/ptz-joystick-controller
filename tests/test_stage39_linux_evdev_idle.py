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
        self.absinfo_by_code = {
            0: SimpleNamespace(min=0, max=1023, flat=4),
            1: SimpleNamespace(min=0, max=1023, flat=4),
            5: SimpleNamespace(min=0, max=255, flat=2),
            6: SimpleNamespace(min=0, max=255, flat=0),
        }

    def absinfo(self, code: int):
        return self.absinfo_by_code[code]

    def read(self):
        if self.raise_blocking:
            raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
        if self.raise_oserror:
            raise OSError(errno.ENODEV, "No such device")
        events = list(self.events)
        self.events.clear()
        return events


class LazyBlockingIterator:
    def __iter__(self):
        raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")


class LazyBlockingEvdevDevice(FakeEvdevDevice):
    def read(self):
        return LazyBlockingIterator()


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
    provider._unknown_abs_codes_logged = set()
    provider._axis_specs = provider._load_axis_specs()
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
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=0, value=900))
    first_pan = provider.snapshot().axes.pan
    assert first_pan > 0

    device.raise_blocking = True
    idle_snapshot = provider.snapshot()

    assert idle_snapshot.axes.pan == first_pan


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


def test_linux_evdev_lazy_iterator_blockingioerror_returns_normally() -> None:
    device = LazyBlockingEvdevDevice()
    provider = make_provider(device)

    provider.poll()

    snapshot = provider.snapshot()
    assert snapshot.axes == RawAxisState()
    assert snapshot.hat == HatState()


def test_linux_evdev_lazy_iterator_keeps_last_known_state() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=0, value=900))
    first_pan = provider.snapshot().axes.pan
    assert first_pan > 0

    provider._device = LazyBlockingEvdevDevice()
    idle_snapshot = provider.snapshot()

    assert idle_snapshot.axes.pan == first_pan


def test_linux_evdev_numeric_code_0_updates_pan() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    provider._ecodes.bytype[FakeEcodes.EV_ABS][0] = "UNEXPECTED_ABS_NAME"
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=0, value=900))

    snapshot = provider.snapshot()

    assert snapshot.axes.pan > 0


def test_linux_evdev_numeric_code_1_updates_tilt() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    provider._ecodes.bytype[FakeEcodes.EV_ABS][1] = "UNEXPECTED_ABS_NAME"
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=1, value=100))

    snapshot = provider.snapshot()

    assert snapshot.axes.tilt < 0


def test_linux_evdev_numeric_code_5_updates_zoom_twist() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    provider._ecodes.bytype[FakeEcodes.EV_ABS][5] = "UNEXPECTED_ABS_NAME"
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=5, value=200))

    snapshot = provider.snapshot()

    assert snapshot.axes.zoom > 0


def test_linux_evdev_numeric_code_16_updates_hat_x() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    provider._ecodes.bytype[FakeEcodes.EV_ABS][16] = "UNEXPECTED_ABS_NAME"
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=16, value=-1))

    snapshot = provider.snapshot()

    assert snapshot.hat.x == -1
    assert snapshot.hat.y == 0


def test_linux_evdev_numeric_code_17_updates_hat_y() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    provider._ecodes.bytype[FakeEcodes.EV_ABS][17] = "UNEXPECTED_ABS_NAME"
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=17, value=1))

    snapshot = provider.snapshot()

    assert snapshot.hat.x == 0
    assert snapshot.hat.y == 1


def test_linux_evdev_pan_center_511_becomes_internal_zero() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=0, value=511))

    assert provider.snapshot().axes.pan == 0


def test_linux_evdev_pan_low_high_become_negative_positive() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=0, value=0))
    assert provider.snapshot().axes.pan < 0
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=0, value=1023))
    assert provider.snapshot().axes.pan > 0


def test_linux_evdev_tilt_center_510_becomes_internal_zero() -> None:
    device = FakeEvdevDevice()
    # Simulate minimal fake device without useful absinfo so observed hardware
    # fallback center for ABS 1 is used.
    device.absinfo_by_code.pop(1)
    provider = make_provider(device)
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=1, value=510))

    assert provider.snapshot().axes.tilt == 0


def test_linux_evdev_zoom_center_127_becomes_internal_zero() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=5, value=127))

    assert provider.snapshot().axes.zoom == 0


def test_linux_evdev_hat_values_remain_discrete() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=16, value=-1))
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_ABS, code=17, value=1))

    snapshot = provider.snapshot()

    assert snapshot.hat.x == -1
    assert snapshot.hat.y == 1


def test_linux_evdev_key_code_288_tuple_alias_maps_to_trigger() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    provider._ecodes.bytype[FakeEcodes.EV_KEY][288] = ("BTN_JOYSTICK", "BTN_TRIGGER")

    assert provider._button_for_code(288) == "trigger"


def test_linux_evdev_trigger_down_adds_pressed_button() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    provider._ecodes.bytype[FakeEcodes.EV_KEY][288] = ("BTN_JOYSTICK", "BTN_TRIGGER")
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_KEY, code=288, value=1))

    snapshot = provider.snapshot()

    assert "trigger" in snapshot.pressed_buttons


def test_linux_evdev_trigger_up_removes_pressed_button() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    provider._ecodes.bytype[FakeEcodes.EV_KEY][288] = ("BTN_JOYSTICK", "BTN_TRIGGER")
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_KEY, code=288, value=1))
    provider.snapshot()
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_KEY, code=288, value=0))

    snapshot = provider.snapshot()

    assert "trigger" not in snapshot.pressed_buttons


def test_linux_evdev_trigger_press_and_release_emit_button_events() -> None:
    device = FakeEvdevDevice()
    provider = make_provider(device)
    provider._ecodes.bytype[FakeEcodes.EV_KEY][288] = ("BTN_JOYSTICK", "BTN_TRIGGER")
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_KEY, code=288, value=1))
    events_down = tuple(provider.button_events())
    device.events.append(SimpleNamespace(type=FakeEcodes.EV_KEY, code=288, value=0))
    events_up = tuple(provider.button_events())

    assert [(event.button_name, event.pressed) for event in events_down] == [("trigger", True)]
    assert [(event.button_name, event.pressed) for event in events_up] == [("trigger", False)]
