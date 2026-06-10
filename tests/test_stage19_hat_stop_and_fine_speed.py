from __future__ import annotations

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.device import FakeJoystickInputProvider
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.models.joystick_input import HatState, RawAxisState
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.runtime.joystick_switcher_bridge import JoystickToSwitcherBridge
from ptz_joystick_controller.switchers.fake import FakeSwitcher


def make_config(*, fine_pan_speed: int = 2, fine_tilt_speed: int = 2, apply_throttle: bool = False):
    return parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "sources": {
                "mappings": [
                    {"source_id": "Input 1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
                    {"source_id": "Input 3", "display_name": "Unmapped", "ptz_camera_id": None},
                ]
            },
            "ptz": {
                "stop_on_switch": True,
                "cameras": [{"id": "cam1", "name": "PTZ 1", "visca_id": 1}],
            },
            "joystick": {
                "hat": {
                    "fine_pan_speed": fine_pan_speed,
                    "fine_tilt_speed": fine_tilt_speed,
                    "apply_throttle": apply_throttle,
                }
            },
        }
    )


def make_bridge(*, fine_pan_speed: int = 2, fine_tilt_speed: int = 2, apply_throttle: bool = False):
    config = make_config(
        fine_pan_speed=fine_pan_speed,
        fine_tilt_speed=fine_tilt_speed,
        apply_throttle=apply_throttle,
    )
    bus = EventBus()
    provider = FakeJoystickInputProvider()
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=type(
            "Discovery",
            (),
            {"discover": lambda self: [JoystickDeviceInfo(name="Fake", path="fake", backend="fake")]},
        )(),
        provider_factory=lambda _device: provider,
    )
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 3", preview_source_id="Input 1")
    bridge = JoystickToSwitcherBridge(config, monitor, switcher, bus, dry_run=False)
    bridge.start()
    return bridge, provider


def test_hat_non_zero_sends_move() -> None:
    bridge, provider = make_bridge()
    provider.set_hat(HatState(x=1, y=0))
    bridge.poll_once()
    assert any("cam1:hat pan=2 tilt=0" in entry for entry in bridge.ptz_router.command_log)


def test_hat_center_after_movement_sends_stop() -> None:
    bridge, provider = make_bridge()
    provider.set_hat(HatState(x=1, y=0))
    bridge.poll_once()
    provider.set_hat(HatState())
    bridge.poll_once()
    assert any("cam1:hat_stop reason=hat_center" in entry for entry in bridge.ptz_router.command_log)


def test_hat_center_without_prior_hat_movement_does_not_spam_stop() -> None:
    bridge, provider = make_bridge()
    provider.set_hat(HatState())
    bridge.poll_once()
    bridge.poll_once()
    assert not any("hat_center" in entry for entry in bridge.ptz_router.command_log)


def test_hat_speed_uses_fine_pan_speed_and_fine_tilt_speed() -> None:
    bridge, provider = make_bridge(fine_pan_speed=1, fine_tilt_speed=1)
    provider.set_hat(HatState(x=1, y=-1))
    bridge.poll_once()
    assert any("cam1:hat pan=1 tilt=1" in entry for entry in bridge.ptz_router.command_log)


def test_hat_movement_independent_of_throttle_when_apply_throttle_false() -> None:
    bridge, provider = make_bridge(fine_pan_speed=3, fine_tilt_speed=4, apply_throttle=False)
    provider.set_axes(RawAxisState(throttle=32767))
    provider.set_hat(HatState(x=1, y=-1))
    bridge.poll_once()
    assert any("cam1:hat pan=3 tilt=4" in entry for entry in bridge.ptz_router.command_log)
