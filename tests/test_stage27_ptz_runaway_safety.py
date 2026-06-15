from __future__ import annotations

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.models.joystick_input import PtzVelocity
from ptz_joystick_controller.runtime.ptz_router import PtzRouter
from ptz_joystick_controller.app_state import AppState


def make_router() -> PtzRouter:
    config = parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "sources": {
                "mappings": [
                    {"source_id": "Input 1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
                ]
            },
            "ptz": {
                "stop_on_switch": True,
                "stop_watchdog": {"enabled": True, "timeout_ms": 500, "center_confirm_samples": 2},
                "cameras": [{"id": "cam1", "name": "PTZ 1", "visca_id": 1}],
            },
            "joystick": {
                "output_deadzone": {"pan_tilt": 0.05, "zoom": 0.05},
            },
        }
    )
    state = AppState(config=config)
    state.preview_source_id = "Input 1"
    state.recompute_active_ptz()
    return PtzRouter(state=state, event_bus=EventBus())


def test_tiny_pan_value_does_not_send_move() -> None:
    router = make_router()
    assert router.route_velocity(PtzVelocity(pan=-0.006, tilt=0.0)) is False
    assert router.camera_command_count("cam1") == 0
    assert router.pan_tilt_active is False


def test_tiny_pan_while_active_sends_stop() -> None:
    router = make_router()
    router.route_velocity(PtzVelocity(pan=0.5))
    router.route_velocity(PtzVelocity(pan=0.006))
    assert any("cam1:pan_tilt_stop reason=axis_center" in entry for entry in router.command_log)
    assert router.pan_tilt_active is False


def test_combined_pan_zoom_release_cannot_leave_pan_tilt_active() -> None:
    router = make_router()
    router.route_velocity(PtzVelocity(pan=0.7, zoom=0.8))
    assert router.pan_tilt_active is True
    assert router.zoom_active is True
    router.route_velocity(PtzVelocity(pan=0.006, zoom=0.004))
    assert router.pan_tilt_active is False
    assert router.zoom_active is False
    assert any("pan_tilt_stop reason=axis_center" in entry for entry in router.command_log)
    assert any("zoom_stop reason=zoom_center" in entry for entry in router.command_log)


def test_watchdog_logs_center_confirmed_after_repeated_centered_snapshots() -> None:
    router = make_router()
    router.route_velocity(PtzVelocity(pan=0.7))
    # Simulate a stale active flag after one centered snapshot; the next centered
    # snapshot must still produce a watchdog center-confirmed stop.
    router.pan_tilt_center_samples = 1
    router.pan_tilt_active = True
    router.route_velocity(PtzVelocity(pan=0.0, tilt=0.0))
    assert any("watchdog_pan_tilt_stop reason=center_confirmed" in entry for entry in router.command_log)


def test_no_duplicate_stop_spam_for_repeated_tiny_values() -> None:
    router = make_router()
    router.route_velocity(PtzVelocity(pan=0.7))
    router.route_velocity(PtzVelocity(pan=0.006))
    router.route_velocity(PtzVelocity(pan=0.004))
    stops = [entry for entry in router.command_log if "pan_tilt_stop reason=axis_center" in entry]
    assert len(stops) == 1
