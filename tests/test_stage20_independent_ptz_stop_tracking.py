from __future__ import annotations

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.device import FakeJoystickInputProvider
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.models.joystick_input import HatState, RawAxisState
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.ptz.session import CameraSession, SafeStopCameraSession
from ptz_joystick_controller.ptz.transport import FakeViscaTransport, ReconnectSafeTransport
from ptz_joystick_controller.models.ptz import PtzCamera
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
                    {"source_id": "Input 3", "display_name": "Unmapped", "ptz_camera_id": None},
                ]
            },
            "ptz": {
                "stop_on_switch": True,
                "cameras": [{"id": "cam1", "name": "PTZ 1", "visca_id": 1}],
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
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 3", preview_source_id="Input 1")
    bridge = JoystickToSwitcherBridge(config, monitor, switcher, bus, dry_run=False)
    bridge.start()
    return bridge, provider


def set_axes(provider: FakeJoystickInputProvider, *, pan: int = 0, tilt: int = 0, zoom: int = 0, throttle: int = 32767) -> None:
    provider.set_axes(RawAxisState(pan=pan, tilt=tilt, zoom=zoom, throttle=throttle))


def test_pan_tilt_move_then_center_sends_pan_tilt_stop() -> None:
    bridge, provider = make_bridge()
    set_axes(provider, pan=32767)
    bridge.poll_once()
    set_axes(provider, pan=0)
    bridge.poll_once()
    assert any("cam1:pan_tilt_stop reason=axis_center" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.ptz_router.pan_tilt_active is False


def test_zoom_move_then_center_sends_zoom_stop() -> None:
    bridge, provider = make_bridge()
    set_axes(provider, zoom=32767)
    bridge.poll_once()
    set_axes(provider, zoom=0)
    bridge.poll_once()
    assert any("cam1:zoom_stop reason=zoom_center" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.ptz_router.zoom_active is False


def test_hat_move_then_center_sends_pan_tilt_stop() -> None:
    bridge, provider = make_bridge()
    provider.set_hat(HatState(x=1, y=-1))
    bridge.poll_once()
    provider.set_hat(HatState())
    bridge.poll_once()
    assert any("cam1:hat_stop reason=hat_center" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.ptz_router.hat_active is False


def test_stopping_zoom_does_not_clear_pan_tilt_active() -> None:
    bridge, provider = make_bridge()
    set_axes(provider, pan=32767, zoom=32767)
    bridge.poll_once()
    set_axes(provider, pan=32767, zoom=0)
    bridge.poll_once()
    assert any("cam1:zoom_stop reason=zoom_center" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.ptz_router.pan_tilt_active is True
    assert bridge.ptz_router.zoom_active is False


def test_stopping_pan_tilt_does_not_clear_zoom_active() -> None:
    bridge, provider = make_bridge()
    set_axes(provider, pan=32767, zoom=32767)
    bridge.poll_once()
    set_axes(provider, pan=0, zoom=32767)
    bridge.poll_once()
    assert any("cam1:pan_tilt_stop reason=axis_center" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.ptz_router.pan_tilt_active is False
    assert bridge.ptz_router.zoom_active is True


def test_stopping_hat_does_not_interfere_with_zoom_active() -> None:
    bridge, provider = make_bridge()
    set_axes(provider, zoom=32767)
    provider.set_hat(HatState(x=1, y=0))
    bridge.poll_once()
    provider.set_hat(HatState())
    bridge.poll_once()
    assert any("cam1:hat_stop reason=hat_center" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.ptz_router.zoom_active is True


def test_center_events_without_prior_movement_do_not_spam_stop() -> None:
    bridge, provider = make_bridge()
    set_axes(provider, pan=0, tilt=0, zoom=0)
    provider.set_hat(HatState())
    bridge.poll_once()
    bridge.poll_once()
    assert not any("axis_center" in entry or "zoom_center" in entry or "hat_center" in entry for entry in bridge.ptz_router.command_log)


def test_script_exit_sends_pan_tilt_stop_and_zoom_stop() -> None:
    fake = FakeViscaTransport()
    session = CameraSession(PtzCamera(id="cam1", name="Camera 1"), ReconnectSafeTransport(fake))
    with SafeStopCameraSession(session, reason="script_exit") as active:
        active.pan_tilt_from_axes(1.0, 0.0)
        active.zoom_from_axis(1.0)
    assert session.state.pan_tilt_active is False
    assert session.state.zoom_active is False
    assert session.command_log[-2].command.payload.endswith(b"\x03\x03\xff")
    assert session.command_log[-1].command.payload.endswith(b"\x00\xff")


def test_hat_fine_speed_and_throttle_independence() -> None:
    bridge, provider = make_bridge()
    set_axes(provider, throttle=-32768)
    provider.set_hat(HatState(x=1, y=-1))
    bridge.poll_once()
    assert any("cam1:hat pan=3 tilt=4" in entry for entry in bridge.ptz_router.command_log)
