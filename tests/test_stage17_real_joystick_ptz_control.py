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


def make_config():
    return parse_config(
        {
            "switcher": {"type": "vmix", "host": None, "port": 8088},
            "sources": {
                "mappings": [
                    {"source_id": "Input 1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
                    {"source_id": "Input 2", "display_name": "Camera 2", "ptz_camera_id": "cam2"},
                    {"source_id": "Input 3", "display_name": "Unmapped 3", "ptz_camera_id": None},
                    {"source_id": "Input 4", "display_name": "Unmapped 4", "ptz_camera_id": None},
                ]
            },
            "ptz": {
                "stop_on_switch": True,
                "cameras": [
                    {"id": "cam1", "name": "PTZ 1", "host": "192.0.2.11", "visca_id": 1},
                    {"id": "cam2", "name": "PTZ 2", "host": "192.0.2.12", "visca_id": 2},
                ],
            },
            "joystick": {
                "buttons": {
                    "trigger": {"action": "cut"},
                    "thumb": {"action": "copy_program_to_preview"},
                    "button_3": {"action": "preview_source", "source_id": "Input 1"},
                    "button_4": {"action": "preview_source", "source_id": "Input 2"},
                }
            },
        }
    )


def make_bridge(*, program: str = "Input 3", preview: str = "Input 1"):
    config = make_config()
    bus = EventBus()
    provider = FakeJoystickInputProvider()
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=StaticFakeJoystickDiscovery(),
        provider_factory=lambda _device: provider,
    )
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id=program, preview_source_id=preview)
    bridge = JoystickToSwitcherBridge(config, monitor, switcher, bus, dry_run=False)
    bridge.start()
    return bridge, provider, switcher


def set_axes(provider: FakeJoystickInputProvider, *, pan: int = 0, tilt: int = 0, zoom: int = 0, throttle: int = 32767) -> None:
    provider.set_axes(RawAxisState(pan=pan, tilt=tilt, zoom=zoom, throttle=throttle))


def test_preview_source_ptz_selection_and_state_tracking() -> None:
    bridge, _provider, switcher = make_bridge(preview="Input 1")
    assert bridge.state.active_ptz_camera_id == "cam1"
    assert bridge.status().active_ptz_camera_id == "cam1"
    assert bridge.status().active_ptz_diagnostics.active_camera_id == "cam1"

    switcher.set_preview_source("Input 2")
    bridge.switcher_executor.sync_from_switcher()
    assert bridge.state.active_ptz_camera_id == "cam2"
    assert bridge.ptz_router.diagnostics().active_camera_id == "cam2"


def test_preview_input_3_and_4_have_no_ptz_assigned() -> None:
    bridge, provider, switcher = make_bridge(preview="Input 3")
    assert bridge.state.preview_source_id == "Input 3"
    assert bridge.state.active_ptz_camera_id is None

    set_axes(provider, pan=32767, tilt=32767, zoom=32767)
    bridge.poll_once()
    assert bridge.ptz_router.camera_command_count("cam1") == 0
    assert bridge.ptz_router.camera_command_count("cam2") == 0

    switcher.set_preview_source("Input 4")
    bridge.switcher_executor.sync_from_switcher()
    assert bridge.state.active_ptz_camera_id is None
    bridge.poll_once()
    assert bridge.ptz_router.camera_command_count("cam1") == 0
    assert bridge.ptz_router.camera_command_count("cam2") == 0


def test_joystick_axes_control_only_current_preview_ptz_camera() -> None:
    bridge, provider, switcher = make_bridge(preview="Input 1")
    set_axes(provider, pan=32767, tilt=32767, zoom=32767, throttle=32767)
    bridge.poll_once()
    assert bridge.ptz_router.camera_command_count("cam1") == 2
    assert bridge.ptz_router.camera_command_count("cam2") == 0
    assert bridge.ptz_router.diagnostics().active_camera_moving is True

    switcher.set_preview_source("Input 2")
    bridge.switcher_executor.sync_from_switcher()
    set_axes(provider, pan=-32768, tilt=-32768, zoom=-32768, throttle=32767)
    bridge.poll_once()
    assert bridge.ptz_router.camera_command_count("cam2") == 2


def test_joystick_center_sends_stop_command() -> None:
    bridge, provider, _switcher = make_bridge(preview="Input 1")
    set_axes(provider, pan=32767, throttle=32767)
    bridge.poll_once()
    assert bridge.ptz_router.diagnostics().active_camera_moving is True

    set_axes(provider, pan=0, tilt=0, zoom=0, throttle=32767)
    bridge.poll_once()
    assert any("cam1:stop reason=joystick_centered" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.ptz_router.diagnostics().active_camera_moving is False


def test_source_change_sends_stop_command_before_new_camera_becomes_active() -> None:
    bridge, provider, switcher = make_bridge(preview="Input 1")
    set_axes(provider, pan=32767, throttle=32767)
    bridge.poll_once()

    switcher.set_preview_source("Input 2")
    bridge.switcher_executor.sync_from_switcher()

    assert any("cam1:stop reason=active_source_changed" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.state.active_ptz_camera_id == "cam2"


def test_cut_sends_stop_before_transition_and_tracks_new_active_camera() -> None:
    bridge, provider, _switcher = make_bridge(program="Input 2", preview="Input 1")
    set_axes(provider, pan=32767, throttle=32767)
    bridge.poll_once()

    provider.press("trigger")
    bridge.poll_once()

    assert any("cam1:stop reason=before_cut" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.state.program_source_id == "Input 1"
    assert bridge.state.preview_source_id == "Input 2"
    assert bridge.state.active_ptz_camera_id == "cam2"


def test_copy_program_to_preview_makes_program_ptz_camera_controllable() -> None:
    bridge, provider, _switcher = make_bridge(program="Input 2", preview="Input 3")
    assert bridge.state.active_ptz_camera_id is None

    provider.press("thumb")
    bridge.poll_once()

    assert bridge.state.preview_source_id == "Input 2"
    assert bridge.state.active_ptz_camera_id == "cam2"
    set_axes(provider, pan=32767, throttle=32767)
    bridge.poll_once()
    assert bridge.ptz_router.camera_command_count("cam2") == 2


def test_hat_switch_routes_fine_pan_tilt_to_active_ptz() -> None:
    bridge, provider, _switcher = make_bridge(preview="Input 1")
    provider.set_hat(HatState(x=1, y=-1))
    bridge.poll_once()
    assert any("cam1:hat" in entry for entry in bridge.ptz_router.command_log)
