from __future__ import annotations

import logging

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.axis_metadata import AxisInversionMetadataRegistry
from ptz_joystick_controller.joystick.hat import HatProcessor
from ptz_joystick_controller.joystick.ptz_speed import PtzSpeedScaler
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.joystick.throttle import ThrottleScaler
from ptz_joystick_controller.models.joystick import AxisInvertConfig, HatConfig, ThrottleConfig
from ptz_joystick_controller.models.joystick_input import HatState, NormalizedAxisState
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo


class EmptyDiscovery:
    def discover(self):
        return []


def test_axis_inversion_metadata_labels_exist() -> None:
    registry = AxisInversionMetadataRegistry(AxisInvertConfig(pan=True, tilt=False, zoom=True))
    metadata = registry.all_metadata()
    assert metadata["pan"].label == "Reverse pan"
    assert metadata["tilt"].label == "Reverse tilt"
    assert metadata["zoom"].label == "Reverse zoom"
    assert metadata["pan"].inverted is True
    assert metadata["tilt"].inverted is False
    assert metadata["zoom"].inverted is True


def test_axis_inversion_unknown_label_falls_back_to_axis_id() -> None:
    registry = AxisInversionMetadataRegistry(AxisInvertConfig())
    assert registry.label_for("iris") == "iris"


def test_main_tilt_inversion_false_keeps_axis_direction() -> None:
    scaler = PtzSpeedScaler(
        invert=AxisInvertConfig(tilt=False),
        throttle=ThrottleScaler(ThrottleConfig(min_multiplier=1.0, max_multiplier=1.0, invert=False)),
    )
    velocity = scaler.velocity_from_axes(NormalizedAxisState(tilt=0.75, throttle=1.0))
    assert velocity.tilt == 0.75


def test_main_tilt_inversion_true_reverses_axis_direction() -> None:
    scaler = PtzSpeedScaler(
        invert=AxisInvertConfig(tilt=True),
        throttle=ThrottleScaler(ThrottleConfig(min_multiplier=1.0, max_multiplier=1.0, invert=False)),
    )
    velocity = scaler.velocity_from_axes(NormalizedAxisState(tilt=0.75, throttle=1.0))
    assert velocity.tilt == -0.75


def test_hat_tilt_inversion_false_keeps_legacy_hat_direction() -> None:
    processor = HatProcessor(HatConfig(fine_tilt_speed=3), invert=AxisInvertConfig(tilt=False))
    assert processor.to_ptz_step(HatState(x=0, y=-1)).tilt_speed == 3
    assert processor.to_ptz_step(HatState(x=0, y=1)).tilt_speed == -3


def test_hat_tilt_inversion_true_reverses_hat_tilt_direction() -> None:
    processor = HatProcessor(HatConfig(fine_tilt_speed=3), invert=AxisInvertConfig(tilt=True))
    assert processor.to_ptz_step(HatState(x=0, y=-1)).tilt_speed == -3
    assert processor.to_ptz_step(HatState(x=0, y=1)).tilt_speed == 3


def test_config_supports_pan_tilt_zoom_inversion_and_keeps_example_preference() -> None:
    config = parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "joystick": {"invert": {"pan": False, "tilt": True, "zoom": False}},
        }
    )
    assert config.joystick.invert.pan is False
    assert config.joystick.invert.tilt is True
    assert config.joystick.invert.zoom is False


def test_startup_logs_effective_inversion_settings(caplog) -> None:
    config = parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "joystick": {"invert": {"pan": False, "tilt": True, "zoom": False}},
        }
    )
    monitor = JoystickRuntimeMonitor(config=config, event_bus=EventBus(), discovery=EmptyDiscovery())
    with caplog.at_level(logging.INFO):
        monitor.start()
    assert "Joystick axis inversion: pan=False tilt=True zoom=False" in caplog.text

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.joystick.device import FakeJoystickInputProvider
from ptz_joystick_controller.joystick.discovery import StaticJoystickDiscovery
from ptz_joystick_controller.models.joystick_input import JoystickSnapshot, RawAxisState
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.runtime.joystick_switcher_bridge import JoystickToSwitcherBridge
from ptz_joystick_controller.runtime.ptz_router import PtzRouter
from ptz_joystick_controller.switchers.fake import FakeSwitcher


def _config_with_tilt_invert(enabled: bool):
    return parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "sources": {
                "mappings": [
                    {"source_id": "Input 1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
                ]
            },
            "ptz": {"cameras": [{"id": "cam1", "name": "PTZ 1", "host": "192.0.2.10", "port": 52381}]},
            "joystick": {
                "deadzone": {"pan": 0.0, "tilt": 0.0, "zoom": 0.0},
                "invert": {"pan": False, "tilt": enabled, "zoom": False},
                "throttle": {"min_multiplier": 1.0, "max_multiplier": 1.0, "invert": False},
            },
        }
    )


def test_runtime_main_tilt_invert_false_maps_physical_up_to_positive_tilt() -> None:
    config = _config_with_tilt_invert(False)
    monitor = JoystickRuntimeMonitor(config=config, event_bus=EventBus(), discovery=EmptyDiscovery())

    velocity = monitor.ptz_velocity(JoystickSnapshot(axes=RawAxisState(tilt=-32768, throttle=32767)))

    assert velocity.tilt == 1.0


def test_runtime_main_tilt_invert_true_maps_physical_up_to_negative_tilt() -> None:
    config = _config_with_tilt_invert(True)
    monitor = JoystickRuntimeMonitor(config=config, event_bus=EventBus(), discovery=EmptyDiscovery())

    velocity = monitor.ptz_velocity(JoystickSnapshot(axes=RawAxisState(tilt=-32768, throttle=32767)))

    assert velocity.tilt == -1.0


def test_ptz_router_main_path_uses_inverted_runtime_tilt_velocity_end_to_end() -> None:
    config = _config_with_tilt_invert(True)
    state = AppState(config=config)
    state.preview_source_id = "Input 1"
    state.recompute_active_ptz()
    router = PtzRouter(state=state, event_bus=EventBus())
    monitor = JoystickRuntimeMonitor(config=config, event_bus=EventBus(), discovery=EmptyDiscovery())

    velocity = monitor.ptz_velocity(JoystickSnapshot(axes=RawAxisState(tilt=-32768, throttle=32767)))
    router.route_velocity(velocity)

    routed = router.sessions["cam1"].session
    assert routed.state.tilt == -1.0
    assert router.command_log[-1].startswith("cam1:pan_tilt source=main")


def test_bridge_end_to_end_ptz_command_uses_inverted_main_tilt() -> None:
    config = _config_with_tilt_invert(True)
    device = JoystickDeviceInfo(name="Fake Logitech", path="fake0", backend="fake")
    provider = FakeJoystickInputProvider(JoystickSnapshot(axes=RawAxisState(tilt=-32768, throttle=32767)))
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=EventBus(),
        discovery=StaticJoystickDiscovery((device,)),
        provider_factory=lambda _device: provider,
    )
    switcher = FakeSwitcher(SwitcherType.VMIX, connected=True, program_source_id="Input 2", preview_source_id="Input 1")
    bridge = JoystickToSwitcherBridge(config=config, joystick_monitor=monitor, switcher=switcher, dry_run=True)

    bridge.start()
    bridge.poll_once()

    session = bridge.ptz_router.sessions["cam1"].session
    assert session.state.tilt == -1.0
