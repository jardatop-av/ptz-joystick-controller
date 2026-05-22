from __future__ import annotations

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.calibration import AxisCalibration, JoystickCalibration
from ptz_joystick_controller.joystick.deadzone import DeadzoneProcessor, apply_deadzone
from ptz_joystick_controller.joystick.device import FakeJoystickInputProvider
from ptz_joystick_controller.joystick.dispatcher import JoystickActionDispatcher
from ptz_joystick_controller.joystick.hat import HatProcessor
from ptz_joystick_controller.joystick.ptz_speed import PtzSpeedScaler
from ptz_joystick_controller.joystick.throttle import ThrottleScaler
from ptz_joystick_controller.models.commands import CommandType
from ptz_joystick_controller.models.joystick import DeadzoneConfig, HatConfig, ThrottleConfig, AxisInvertConfig
from ptz_joystick_controller.models.joystick_input import HatState, NormalizedAxisState, RawAxisState


def test_deadzone_filtering_zeroes_small_values_and_rescales() -> None:
    assert apply_deadzone(0.05, 0.10) == 0.0
    assert apply_deadzone(-0.05, 0.10) == 0.0
    assert round(apply_deadzone(0.55, 0.10), 3) == 0.5

    processor = DeadzoneProcessor(DeadzoneConfig(pan=0.1, tilt=0.2, zoom=0.3))
    processed = processor.process(NormalizedAxisState(pan=0.05, tilt=0.5, zoom=-0.2, throttle=0.7))
    assert processed.pan == 0.0
    assert round(processed.tilt, 3) == 0.375
    assert processed.zoom == 0.0
    assert processed.throttle == 0.7


def test_axis_normalization() -> None:
    calibration = JoystickCalibration(
        pan=AxisCalibration(minimum=0, center=50, maximum=100),
        tilt=AxisCalibration(minimum=-100, center=0, maximum=100),
        zoom=AxisCalibration(minimum=0, center=0, maximum=255),
        throttle=AxisCalibration(minimum=0, center=127, maximum=255),
    )
    axes = calibration.normalize_axes(RawAxisState(pan=100, tilt=-50, zoom=255, throttle=0))
    assert axes.pan == 1.0
    assert axes.tilt == -0.5
    assert axes.zoom == 1.0
    assert axes.throttle == -1.0


def test_throttle_scaling() -> None:
    scaler = ThrottleScaler(ThrottleConfig(min_multiplier=0.2, max_multiplier=1.0))
    assert scaler.scale(-1.0) == 0.2
    assert scaler.scale(1.0) == 1.0
    assert round(scaler.scale(0.0), 6) == 0.6


def test_hat_switch_conversion() -> None:
    processor = HatProcessor(HatConfig(fine_pan_speed=2, fine_tilt_speed=3))
    assert processor.to_ptz_step(HatState(x=1, y=0)).pan_speed == 2
    assert processor.to_ptz_step(HatState(x=-1, y=0)).pan_speed == -2
    assert processor.to_ptz_step(HatState(x=0, y=-1)).tilt_speed == 3
    assert processor.to_ptz_step(HatState(x=0, y=1)).tilt_speed == -3
    diagonal = processor.to_ptz_step(HatState(x=1, y=-1))
    assert diagonal.pan_speed == 2
    assert diagonal.tilt_speed == 3


def test_ptz_speed_scaling_applies_invert_and_throttle() -> None:
    scaler = PtzSpeedScaler(
        invert=AxisInvertConfig(pan=False, tilt=True, zoom=False),
        throttle=ThrottleScaler(ThrottleConfig(min_multiplier=0.5, max_multiplier=1.0)),
    )
    velocity = scaler.velocity_from_axes(NormalizedAxisState(pan=0.5, tilt=0.5, zoom=-1.0, throttle=1.0))
    assert velocity.speed_multiplier == 1.0
    assert velocity.pan == 0.5
    assert velocity.tilt == -0.5
    assert velocity.zoom == -1.0


def test_fake_joystick_provider_emits_simulated_button_events() -> None:
    provider = FakeJoystickInputProvider()
    provider.press("trigger")
    provider.release("trigger")
    events = list(provider.button_events())
    assert [(event.button_name, event.pressed) for event in events] == [("trigger", True), ("trigger", False)]
    assert list(provider.button_events()) == []


def test_button_mapping_from_simulated_events() -> None:
    config = parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "sources": {"mappings": [{"source_id": "CH1", "ptz_camera_id": None}]},
            "joystick": {
                "buttons": {
                    "trigger": {"action": "auto"},
                    "thumb": {"action": "copy_program_to_preview"},
                    "button_3": {"action": "preview_source", "source_id": "CH1"},
                }
            },
        }
    )
    provider = FakeJoystickInputProvider()
    dispatcher = JoystickActionDispatcher(config=config, event_bus=EventBus())

    provider.press("trigger")
    command = dispatcher.dispatch_button_event(next(iter(provider.button_events())))
    assert command is not None
    assert command.type == CommandType.AUTO

    provider.press("thumb")
    command = dispatcher.dispatch_button_event(next(iter(provider.button_events())))
    assert command is not None
    assert command.type == CommandType.COPY_PROGRAM_TO_PREVIEW

    provider.press("button_3")
    command = dispatcher.dispatch_button_event(next(iter(provider.button_events())))
    assert command is not None
    assert command.type == CommandType.SET_PREVIEW_SOURCE
    assert command.source_id == "CH1"


def test_released_button_event_is_ignored() -> None:
    config = parse_config({"switcher": {"type": "vmix", "host": None}, "joystick": {"buttons": {"trigger": {"action": "cut"}}}})
    provider = FakeJoystickInputProvider()
    dispatcher = JoystickActionDispatcher(config=config, event_bus=EventBus())
    provider.release("trigger")
    assert dispatcher.dispatch_button_event(next(iter(provider.button_events()))) is None
