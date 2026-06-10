from __future__ import annotations

import logging

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
                ]
            },
            "ptz": {
                "stop_on_switch": True,
                "cameras": [
                    {"id": "cam1", "name": "PTZ 1", "visca_id": 1},
                    {"id": "cam2", "name": "PTZ 2", "visca_id": 2},
                ],
            },
            "joystick": {"hat": {"fine_pan_speed": 3, "fine_tilt_speed": 4, "apply_throttle": False}},
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


def test_main_active_suppresses_hat_movement() -> None:
    bridge, provider, _ = make_bridge()
    set_axes(provider, pan=32767)
    provider.set_hat(HatState(x=-1, y=-1))
    bridge.poll_once()
    assert bridge.ptz_router.effective_pan_tilt_source == "main"
    assert any("source=main" in entry for entry in bridge.ptz_router.command_log)
    assert not any("source=hat" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.ptz_router.hat_active is False


def test_hat_active_only_when_main_centered() -> None:
    bridge, provider, _ = make_bridge()
    provider.set_hat(HatState(x=-1, y=-1))
    bridge.poll_once()
    assert bridge.ptz_router.effective_pan_tilt_source == "hat"
    assert bridge.ptz_router.hat_active is True
    assert any("source=hat x=-1 y=1 pan=-3 tilt=4" in entry for entry in bridge.ptz_router.command_log)


def test_hat_diagonal_sends_combined_pan_tilt_command() -> None:
    bridge, provider, _ = make_bridge()
    provider.set_hat(HatState(x=1, y=1))
    bridge.poll_once()
    assert any("source=hat x=1 y=-1 pan=3 tilt=-4" in entry for entry in bridge.ptz_router.command_log)


def test_moving_main_while_hat_held_switches_source_from_hat_to_main() -> None:
    bridge, provider, _ = make_bridge()
    provider.set_hat(HatState(x=-1, y=0))
    bridge.poll_once()
    assert bridge.ptz_router.effective_pan_tilt_source == "hat"
    set_axes(provider, pan=32767)
    bridge.poll_once()
    assert bridge.ptz_router.effective_pan_tilt_source == "main"
    assert bridge.ptz_router.hat_active is False


def test_releasing_main_while_hat_held_switches_source_from_main_to_hat() -> None:
    bridge, provider, _ = make_bridge()
    set_axes(provider, pan=32767)
    provider.set_hat(HatState(x=-1, y=-1))
    bridge.poll_once()
    assert bridge.ptz_router.effective_pan_tilt_source == "main"
    set_axes(provider, pan=0)
    bridge.poll_once()
    assert bridge.ptz_router.effective_pan_tilt_source == "hat"
    assert bridge.ptz_router.hat_active is True


def test_releasing_all_controls_sends_pan_tilt_stop_once() -> None:
    bridge, provider, _ = make_bridge()
    set_axes(provider, pan=32767)
    bridge.poll_once()
    set_axes(provider, pan=0)
    provider.set_hat(HatState())
    bridge.poll_once()
    bridge.poll_once()
    stops = [entry for entry in bridge.ptz_router.command_log if "pan_tilt_stop" in entry and "axis_center" in entry]
    assert len(stops) == 1


def test_zoom_movement_and_zoom_stop_are_independent() -> None:
    bridge, provider, _ = make_bridge()
    set_axes(provider, zoom=32767)
    provider.set_hat(HatState(x=-1, y=0))
    bridge.poll_once()
    provider.set_hat(HatState())
    bridge.poll_once()
    assert bridge.ptz_router.zoom_active is True
    set_axes(provider, zoom=0)
    bridge.poll_once()
    assert any("zoom_stop reason=zoom_center" in entry for entry in bridge.ptz_router.command_log)


def test_preview_source_change_stops_pan_tilt_and_zoom_on_old_camera() -> None:
    bridge, provider, switcher = make_bridge(preview="Input 1")
    set_axes(provider, pan=32767, zoom=32767)
    bridge.poll_once()
    switcher.set_preview_source("Input 2")
    bridge.switcher_executor.sync_from_switcher()
    assert any("cam1:pan_tilt_stop reason=preview_source_changed" in entry for entry in bridge.ptz_router.command_log)
    assert any("cam1:zoom_stop reason=preview_source_changed" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.state.active_ptz_camera_id == "cam2"
    assert bridge.ptz_router.pan_tilt_active is False
    assert bridge.ptz_router.zoom_active is False
    assert bridge.ptz_router.hat_active is False


def test_no_sequence_of_main_hat_zoom_releases_leaves_pan_tilt_running() -> None:
    bridge, provider, _ = make_bridge()
    set_axes(provider, pan=32767, zoom=32767)
    provider.set_hat(HatState(x=1, y=-1))
    bridge.poll_once()
    set_axes(provider, pan=0, zoom=32767)
    bridge.poll_once()
    provider.set_hat(HatState())
    bridge.poll_once()
    set_axes(provider, zoom=0)
    bridge.poll_once()
    assert bridge.ptz_router.pan_tilt_active is False
    assert bridge.ptz_router.zoom_active is False
    assert bridge.ptz_router.hat_active is False


def test_logs_no_longer_show_separate_hat_move_command_in_same_cycle(caplog) -> None:
    bridge, provider, _ = make_bridge()
    set_axes(provider, pan=32767, zoom=32767)
    provider.set_hat(HatState(x=-1, y=-1))
    with caplog.at_level(logging.INFO):
        bridge.poll_once()
    messages = [record.getMessage() for record in caplog.records]
    assert any("PTZ PAN/TILT MOVE" in message and "source=main" in message for message in messages)
    assert any("PTZ ZOOM MOVE" in message for message in messages)
    assert not any("PTZ HAT MOVE" in message for message in messages)
