from __future__ import annotations

from ptz_joystick_controller.config import load_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.discovery import StaticJoystickDiscovery
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.joystick.runtime_output import JoystickRuntimeOutputFilter
from ptz_joystick_controller.models.joystick_input import HatState, JoystickSnapshot, RawAxisState


def make_monitor() -> JoystickRuntimeMonitor:
    return JoystickRuntimeMonitor(load_config("config.example.yaml"), EventBus(), discovery=StaticJoystickDiscovery())


def test_runtime_output_does_not_repeat_unchanged_snapshots() -> None:
    monitor = make_monitor()
    output = JoystickRuntimeOutputFilter(axis_log_interval_seconds=0.25)
    snapshot = JoystickSnapshot(axes=RawAxisState(pan=1000))

    first = output.snapshot_messages(monitor, snapshot, now=1.0)
    second = output.snapshot_messages(monitor, snapshot, now=2.0)

    assert any(message.message.startswith("Axes raw=") for message in first)
    assert second == []


def test_runtime_output_logs_button_press_and_release_separately() -> None:
    monitor = make_monitor()
    output = JoystickRuntimeOutputFilter()
    output.snapshot_messages(monitor, JoystickSnapshot(), now=0.0)

    pressed = output.snapshot_messages(monitor, JoystickSnapshot(pressed_buttons=frozenset({"trigger"})), now=1.0)
    released = output.snapshot_messages(monitor, JoystickSnapshot(pressed_buttons=frozenset()), now=2.0)

    assert [(message.message, message.args) for message in pressed] == [("Button pressed: %s", ("trigger",))]
    assert [(message.message, message.args) for message in released] == [("Button released: %s", ("trigger",))]


def test_runtime_output_logs_hat_direction_changes_separately() -> None:
    monitor = make_monitor()
    output = JoystickRuntimeOutputFilter()

    output.snapshot_messages(monitor, JoystickSnapshot(hat=HatState()), now=1.0)
    messages = output.snapshot_messages(monitor, JoystickSnapshot(hat=HatState(x=1, y=0)), now=2.0)

    assert len(messages) == 1
    assert messages[0].message == "Hat direction: %s step=%s"
    assert messages[0].args[0] == "right"


def test_runtime_output_throttles_axis_logs() -> None:
    monitor = make_monitor()
    output = JoystickRuntimeOutputFilter(axis_log_interval_seconds=1.0)

    first = output.snapshot_messages(monitor, JoystickSnapshot(axes=RawAxisState(pan=1000)), now=1.0)
    suppressed = output.snapshot_messages(monitor, JoystickSnapshot(axes=RawAxisState(pan=2000)), now=1.5)
    due = output.snapshot_messages(monitor, JoystickSnapshot(axes=RawAxisState(pan=3000)), now=2.1)

    assert any(message.message.startswith("Axes raw=") for message in first)
    assert suppressed == []
    assert any(message.message.startswith("Axes raw=") for message in due)


def test_runtime_output_verbose_mode_adds_debug_snapshot_message() -> None:
    monitor = make_monitor()
    output = JoystickRuntimeOutputFilter()

    messages = output.snapshot_messages(monitor, JoystickSnapshot(), now=1.0, verbose=True)

    assert any(message.message.startswith("Verbose snapshot") for message in messages)
