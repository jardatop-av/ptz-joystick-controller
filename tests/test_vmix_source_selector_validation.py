from __future__ import annotations

import logging

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.runtime.switcher_executor import SwitcherCommandExecutor
from ptz_joystick_controller.state_machine.preview_program import PreviewProgramStateMachine
from ptz_joystick_controller.state_machine.ptz_control import PtzControlStateMachine
from ptz_joystick_controller.switchers.fake import FakeSwitcher
from ptz_joystick_controller.models.switcher import SwitcherType


def make_config():
    return parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "sources": {
                "mappings": [
                    {"source_id": "Input 1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
                ]
            },
            "ptz": {"cameras": [{"id": "cam1", "name": "PTZ 1"}]},
        }
    )


def make_state_machine():
    state = AppState(make_config())
    event_bus = EventBus()
    ptz = PtzControlStateMachine(state, event_bus)
    preview_program = PreviewProgramStateMachine(state, event_bus, ptz)
    return state, event_bus, ptz, preview_program


def test_source_selector_accepts_vmix_input_1_3_100_without_ptz_mapping_requirement() -> None:
    state = AppState(make_config())

    for source_id in ("Input 1", "Input 3", "Input 100"):
        mapping = state.source_selector.require_supported_preview_source(source_id)
        assert mapping.source_id == source_id
        assert state.require_supported_source(source_id) == source_id

    assert state.source_selector.active_ptz_for_preview("Input 1") == "cam1"
    assert state.source_selector.active_ptz_for_preview("Input 3") is None
    assert state.source_selector.active_ptz_for_preview("Input 100") is None


def test_app_state_accepts_and_normalizes_vmix_input_numbers() -> None:
    state = AppState(make_config())

    assert state.require_supported_source("1") == "Input 1"
    assert state.require_supported_source("input 3") == "Input 3"
    assert state.require_supported_source("Input 100") == "Input 100"


def test_preview_program_accepts_vmix_input_1_3_100_and_updates_active_ptz() -> None:
    state, _event_bus, _ptz, preview_program = make_state_machine()

    preview_program.set_program("Input 1")
    preview_program.set_preview("Input 3")
    assert state.program_source_id == "Input 1"
    assert state.preview_source_id == "Input 3"
    assert state.active_ptz_camera_id is None

    preview_program.set_preview("Input 100")
    assert state.preview_source_id == "Input 100"
    assert state.active_ptz_camera_id is None

    preview_program.set_preview("Input 1")
    assert state.preview_source_id == "Input 1"
    assert state.active_ptz_camera_id == "cam1"


def test_sync_from_switcher_accepts_vmix_inputs_without_warning_at_state_level(caplog) -> None:
    caplog.set_level(logging.WARNING)
    state, event_bus, ptz, preview_program = make_state_machine()
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 3", preview_source_id="Input 100")
    executor = SwitcherCommandExecutor(
        switcher=switcher,
        state=state,
        event_bus=event_bus,
        preview_program=preview_program,
        ptz_control=ptz,
    )

    executor.sync_from_switcher()

    assert state.program_source_id == "Input 3"
    assert state.preview_source_id == "Input 100"
    assert state.active_ptz_camera_id is None
    assert state.last_error is None
    assert not [record for record in caplog.records if "unsupported" in record.message.lower()]


def test_vmix_invalid_inputs_still_warn_and_keep_previous_state(caplog) -> None:
    caplog.set_level(logging.WARNING)
    state, event_bus, ptz, preview_program = make_state_machine()
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 1", preview_source_id="Input 0")
    executor = SwitcherCommandExecutor(
        switcher=switcher,
        state=state,
        event_bus=event_bus,
        preview_program=preview_program,
        ptz_control=ptz,
    )

    executor.sync_from_switcher()

    assert state.program_source_id == "Input 1"
    assert state.preview_source_id is None
    assert state.active_ptz_camera_id is None
    assert any("unsupported preview source Input 0" in record.message for record in caplog.records)
