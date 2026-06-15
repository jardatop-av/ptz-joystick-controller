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


class StaticFakeJoystickDiscovery:
    def discover(self):
        return [JoystickDeviceInfo(name="Fake joystick", path="fake", backend="fake")]


def make_bridge():
    config = parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "sources": {
                "mappings": [
                    {"source_id": "Input 1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
                ]
            },
            "ptz": {
                "cameras": [
                    {"id": "cam1", "name": "PTZ 1", "visca_id": 1},
                ],
            },
            "joystick": {
                "hat": {"fine_pan_speed": 3, "fine_tilt_speed": 4, "apply_throttle": False},
                "invert": {"pan": False, "tilt": False, "zoom": False},
            },
        }
    )
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
    return bridge, provider


def set_axes_centered(provider: FakeJoystickInputProvider) -> None:
    provider.set_axes(RawAxisState(pan=0, tilt=0, zoom=0, throttle=32767))


def latest_packet(bridge: JoystickToSwitcherBridge) -> bytes:
    packets = bridge.ptz_router.transport_packets("cam1")
    assert packets
    return packets[-1]


def pan_tilt_fields(packet: bytes) -> tuple[int, int, int, int]:
    # VISCA-over-IP test header is 8 bytes, then pan/tilt payload:
    # addr 01 06 01 pan_speed tilt_speed pan_dir tilt_dir ff
    return packet[12], packet[13], packet[14], packet[15]


def latest_hat_log(bridge: JoystickToSwitcherBridge) -> str:
    return [entry for entry in bridge.ptz_router.command_log if "source=hat" in entry][-1]


def assert_hat_direction(hat: HatState, *, pan_dir: int, tilt_dir: int, pan_speed: int = 3, tilt_speed: int = 4) -> None:
    bridge, provider = make_bridge()
    set_axes_centered(provider)
    provider.set_hat(hat)
    bridge.poll_once()
    assert pan_tilt_fields(latest_packet(bridge)) == (pan_speed, tilt_speed, pan_dir, tilt_dir)


def test_hat_left_up_sends_combined_symmetric_command() -> None:
    assert_hat_direction(HatState(x=-1, y=-1), pan_dir=0x01, tilt_dir=0x01)


def test_hat_left_down_sends_combined_symmetric_command() -> None:
    assert_hat_direction(HatState(x=-1, y=1), pan_dir=0x01, tilt_dir=0x02)


def test_hat_right_up_sends_combined_symmetric_command() -> None:
    assert_hat_direction(HatState(x=1, y=-1), pan_dir=0x02, tilt_dir=0x01)


def test_hat_right_down_sends_combined_symmetric_command() -> None:
    assert_hat_direction(HatState(x=1, y=1), pan_dir=0x02, tilt_dir=0x02)


def test_hat_left_to_left_up_transition_updates_immediately_without_center() -> None:
    bridge, provider = make_bridge()
    provider.set_hat(HatState(x=-1, y=0))
    bridge.poll_once()
    provider.set_hat(HatState(x=-1, y=-1))
    bridge.poll_once()
    assert "x=-1 y=1" in latest_hat_log(bridge)
    assert pan_tilt_fields(latest_packet(bridge)) == (3, 4, 0x01, 0x01)
    assert not any("pan_tilt_stop" in entry for entry in bridge.ptz_router.command_log)


def test_hat_left_to_left_down_transition_updates_immediately_without_center() -> None:
    bridge, provider = make_bridge()
    provider.set_hat(HatState(x=-1, y=0))
    bridge.poll_once()
    provider.set_hat(HatState(x=-1, y=1))
    bridge.poll_once()
    assert "x=-1 y=-1" in latest_hat_log(bridge)
    assert pan_tilt_fields(latest_packet(bridge)) == (3, 4, 0x01, 0x02)
    assert not any("pan_tilt_stop" in entry for entry in bridge.ptz_router.command_log)


def test_hat_right_to_right_up_transition_updates_immediately_without_center() -> None:
    bridge, provider = make_bridge()
    provider.set_hat(HatState(x=1, y=0))
    bridge.poll_once()
    provider.set_hat(HatState(x=1, y=-1))
    bridge.poll_once()
    assert "x=1 y=1" in latest_hat_log(bridge)
    assert pan_tilt_fields(latest_packet(bridge)) == (3, 4, 0x02, 0x01)
    assert not any("pan_tilt_stop" in entry for entry in bridge.ptz_router.command_log)


def test_hat_right_to_right_down_transition_updates_immediately_without_center() -> None:
    bridge, provider = make_bridge()
    provider.set_hat(HatState(x=1, y=0))
    bridge.poll_once()
    provider.set_hat(HatState(x=1, y=1))
    bridge.poll_once()
    assert "x=1 y=-1" in latest_hat_log(bridge)
    assert pan_tilt_fields(latest_packet(bridge)) == (3, 4, 0x02, 0x02)
    assert not any("pan_tilt_stop" in entry for entry in bridge.ptz_router.command_log)


def test_left_right_hat_diagonal_generation_is_symmetric() -> None:
    bridge_left, provider_left = make_bridge()
    provider_left.set_hat(HatState(x=-1, y=-1))
    bridge_left.poll_once()
    left = pan_tilt_fields(latest_packet(bridge_left))

    bridge_right, provider_right = make_bridge()
    provider_right.set_hat(HatState(x=1, y=-1))
    bridge_right.poll_once()
    right = pan_tilt_fields(latest_packet(bridge_right))

    assert left[:2] == right[:2] == (3, 4)
    assert left[2] == 0x01
    assert right[2] == 0x02
    assert left[3] == right[3] == 0x01
