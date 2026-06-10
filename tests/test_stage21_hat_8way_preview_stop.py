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


def make_bridge(*, preview: str = "Input 1"):
    config = parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "sources": {
                "mappings": [
                    {"source_id": "Input 1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
                    {"source_id": "Input 2", "display_name": "Camera 2", "ptz_camera_id": "cam2"},
                    {"source_id": "Input 3", "display_name": "Unmapped", "ptz_camera_id": None},
                    {"source_id": "Input 4", "display_name": "Unmapped", "ptz_camera_id": None},
                ]
            },
            "ptz": {
                "stop_on_switch": True,
                "cameras": [
                    {"id": "cam1", "name": "PTZ 1", "visca_id": 1},
                    {"id": "cam2", "name": "PTZ 2", "visca_id": 2},
                ],
            },
            "joystick": {
                "hat": {"fine_pan_speed": 3, "fine_tilt_speed": 4, "apply_throttle": False},
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
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 3", preview_source_id=preview)
    bridge = JoystickToSwitcherBridge(config, monitor, switcher, bus, dry_run=False)
    bridge.start()
    return bridge, provider, switcher


def set_axes(provider: FakeJoystickInputProvider, *, pan: int = 0, tilt: int = 0, zoom: int = 0, throttle: int = 32767) -> None:
    provider.set_axes(RawAxisState(pan=pan, tilt=tilt, zoom=zoom, throttle=throttle))


def latest_hat_log(bridge: JoystickToSwitcherBridge) -> str:
    return [entry for entry in bridge.ptz_router.command_log if ":hat x=" in entry][-1]


def test_hat_left_sends_pan_only() -> None:
    bridge, provider, _ = make_bridge()
    provider.set_hat(HatState(x=-1, y=0))
    bridge.poll_once()
    assert "cam1:hat x=-1 y=0 pan=-3 tilt=0" in latest_hat_log(bridge)


def test_hat_up_sends_tilt_only() -> None:
    bridge, provider, _ = make_bridge()
    provider.set_hat(HatState(x=0, y=-1))
    bridge.poll_once()
    assert "cam1:hat x=0 y=1 pan=0 tilt=4" in latest_hat_log(bridge)


def test_hat_up_left_sends_combined_pan_tilt() -> None:
    bridge, provider, _ = make_bridge()
    provider.set_hat(HatState(x=-1, y=-1))
    bridge.poll_once()
    assert "cam1:hat x=-1 y=1 pan=-3 tilt=4" in latest_hat_log(bridge)


def test_hat_up_right_sends_combined_pan_tilt() -> None:
    bridge, provider, _ = make_bridge()
    provider.set_hat(HatState(x=1, y=-1))
    bridge.poll_once()
    assert "cam1:hat x=1 y=1 pan=3 tilt=4" in latest_hat_log(bridge)


def test_hat_down_left_sends_combined_pan_tilt() -> None:
    bridge, provider, _ = make_bridge()
    provider.set_hat(HatState(x=-1, y=1))
    bridge.poll_once()
    assert "cam1:hat x=-1 y=-1 pan=-3 tilt=-4" in latest_hat_log(bridge)


def test_hat_down_right_sends_combined_pan_tilt() -> None:
    bridge, provider, _ = make_bridge()
    provider.set_hat(HatState(x=1, y=1))
    bridge.poll_once()
    assert "cam1:hat x=1 y=-1 pan=3 tilt=-4" in latest_hat_log(bridge)


def test_changing_hat_direction_while_active_sends_updated_move() -> None:
    bridge, provider, _ = make_bridge()
    provider.set_hat(HatState(x=-1, y=0))
    bridge.poll_once()
    provider.set_hat(HatState(x=-1, y=-1))
    bridge.poll_once()
    provider.set_hat(HatState(x=0, y=-1))
    bridge.poll_once()
    hat_logs = [entry for entry in bridge.ptz_router.command_log if ":hat x=" in entry]
    assert any("x=-1 y=0" in entry for entry in hat_logs)
    assert any("x=-1 y=1" in entry for entry in hat_logs)
    assert any("x=0 y=1" in entry for entry in hat_logs)


def test_hat_center_sends_stop_once() -> None:
    bridge, provider, _ = make_bridge()
    provider.set_hat(HatState(x=1, y=-1))
    bridge.poll_once()
    provider.set_hat(HatState())
    bridge.poll_once()
    bridge.poll_once()
    stops = [entry for entry in bridge.ptz_router.command_log if "hat_stop reason=hat_center" in entry]
    assert len(stops) == 1


def test_hat_center_without_prior_movement_does_not_spam_stop() -> None:
    bridge, provider, _ = make_bridge()
    provider.set_hat(HatState())
    bridge.poll_once()
    bridge.poll_once()
    assert not any("hat_stop reason=hat_center" in entry for entry in bridge.ptz_router.command_log)


def test_preview_change_during_pan_tilt_sends_stop_to_old_camera() -> None:
    bridge, provider, switcher = make_bridge(preview="Input 1")
    set_axes(provider, pan=32767)
    bridge.poll_once()
    switcher.set_preview_source("Input 2")
    bridge.switcher_executor.sync_from_switcher()
    assert any("cam1:pan_tilt_stop reason=preview_source_changed" in entry for entry in bridge.ptz_router.command_log)
    assert any("cam1:stop_previous reason=preview_source_changed" in entry for entry in bridge.ptz_router.command_log)


def test_preview_change_during_zoom_sends_zoom_stop_to_old_camera() -> None:
    bridge, provider, switcher = make_bridge(preview="Input 1")
    set_axes(provider, zoom=32767)
    bridge.poll_once()
    switcher.set_preview_source("Input 2")
    bridge.switcher_executor.sync_from_switcher()
    assert any("cam1:zoom_stop reason=preview_source_changed" in entry for entry in bridge.ptz_router.command_log)


def test_preview_change_during_hat_sends_pan_tilt_stop_to_old_camera() -> None:
    bridge, provider, switcher = make_bridge(preview="Input 1")
    provider.set_hat(HatState(x=-1, y=-1))
    bridge.poll_once()
    switcher.set_preview_source("Input 2")
    bridge.switcher_executor.sync_from_switcher()
    assert any("cam1:pan_tilt_stop reason=preview_source_changed" in entry for entry in bridge.ptz_router.command_log)
    assert any("cam1:stop_previous reason=preview_source_changed" in entry for entry in bridge.ptz_router.command_log)


def test_preview_change_clears_all_movement_flags() -> None:
    bridge, provider, switcher = make_bridge(preview="Input 1")
    set_axes(provider, pan=32767, zoom=32767)
    provider.set_hat(HatState(x=1, y=-1))
    bridge.poll_once()
    assert bridge.ptz_router.pan_tilt_active is True
    assert bridge.ptz_router.zoom_active is True
    # Main joystick pan/tilt has priority over hat, so hat is suppressed while main is active.
    assert bridge.ptz_router.hat_active is False
    switcher.set_preview_source("Input 2")
    bridge.switcher_executor.sync_from_switcher()
    assert bridge.ptz_router.pan_tilt_active is False
    assert bridge.ptz_router.zoom_active is False
    assert bridge.ptz_router.hat_active is False


def test_new_active_camera_does_not_inherit_old_movement_state() -> None:
    bridge, provider, switcher = make_bridge(preview="Input 1")
    set_axes(provider, pan=32767, zoom=32767)
    bridge.poll_once()
    switcher.set_preview_source("Input 2")
    bridge.switcher_executor.sync_from_switcher()
    cam2_count = bridge.ptz_router.camera_command_count("cam2")
    set_axes(provider, pan=0, zoom=0)
    bridge.poll_once()
    assert bridge.state.active_ptz_camera_id == "cam2"
    assert bridge.ptz_router.camera_command_count("cam2") == cam2_count
