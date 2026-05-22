from __future__ import annotations
from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.state_machine.preview_program import PreviewProgramStateMachine


def test_source_mapping_returns_camera(valid_config_dict):
    config = parse_config(valid_config_dict)
    assert config.ptz_camera_for_source("CH1") == "cam1"
    assert config.ptz_camera_for_source("MP1") is None


def test_preview_change_recalculates_active_ptz(valid_config_dict):
    state = AppState(parse_config(valid_config_dict))
    sm = PreviewProgramStateMachine(state, EventBus())
    sm.set_preview("CH2")
    assert state.preview_source == "CH2"
    assert state.active_ptz_camera_id == "cam2"
