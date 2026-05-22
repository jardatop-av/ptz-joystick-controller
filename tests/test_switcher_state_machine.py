from __future__ import annotations

import pytest

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.models.commands import Command, CommandType, EventType
from ptz_joystick_controller.models.sources import UnsupportedSourceError
from ptz_joystick_controller.state_machine.preview_program import PreviewProgramStateMachine
from ptz_joystick_controller.state_machine.ptz_control import PtzControlStateMachine
from ptz_joystick_controller.state_machine.transitions import CommandDispatcher


def make_config(stop_on_switch: bool = True):
    return parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "sources": {
                "mappings": [
                    {"source_id": "CH1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
                    {"source_id": "CH2", "display_name": "Camera 2", "ptz_camera_id": "cam2"},
                    {"source_id": "MP1", "display_name": "Media", "ptz_camera_id": None},
                ]
            },
            "ptz": {
                "stop_on_switch": stop_on_switch,
                "cameras": [
                    {"id": "cam1", "name": "Camera 1"},
                    {"id": "cam2", "name": "Camera 2"},
                ],
            },
            "joystick": {
                "buttons": {
                    "trigger": {"action": "cut"},
                    "button_3": {"action": "preview_source", "source_id": "CH1"},
                }
            },
        }
    )


def make_machine(stop_on_switch: bool = True):
    bus = EventBus()
    events = []
    bus.subscribe_all(events.append)
    state = AppState(config=make_config(stop_on_switch=stop_on_switch))
    ptz = PtzControlStateMachine(state, bus)
    machine = PreviewProgramStateMachine(state, bus, ptz)
    dispatcher = CommandDispatcher(machine, ptz)
    return state, machine, dispatcher, events


def test_preview_program_transition_swaps_sources_and_recomputes_active_ptz() -> None:
    state, machine, _dispatcher, _events = make_machine()
    machine.set_program("CH1")
    machine.set_preview("CH2")

    assert state.active_ptz_camera_id == "cam2"

    machine.cut()

    assert state.program_source_id == "CH2"
    assert state.preview_source_id == "CH1"
    assert state.active_ptz_camera_id == "cam1"


def test_active_ptz_selection_returns_none_for_non_camera_source() -> None:
    state, machine, _dispatcher, _events = make_machine()

    machine.set_preview("MP1")

    assert state.preview_source_id == "MP1"
    assert state.active_ptz_camera_id is None


def test_stop_on_switch_publishes_stop_before_transition() -> None:
    state, machine, _dispatcher, events = make_machine(stop_on_switch=True)
    machine.set_program("CH1")
    machine.set_preview("CH2")
    events.clear()

    machine.auto()

    assert state.stop_requests == ["before_auto"]
    assert any(event.type == EventType.PTZ_STOP_REQUESTED for event in events)


def test_stop_on_switch_disabled_does_not_publish_stop() -> None:
    state, machine, _dispatcher, events = make_machine(stop_on_switch=False)
    machine.set_program("CH1")
    machine.set_preview("CH2")
    events.clear()

    machine.cut()

    assert state.stop_requests == []
    assert not any(event.type == EventType.PTZ_STOP_REQUESTED for event in events)


def test_command_dispatcher_selects_preview_source() -> None:
    state, _machine, dispatcher, _events = make_machine()

    dispatcher.dispatch(Command(type=CommandType.SET_PREVIEW_SOURCE, source_id="CH1"))

    assert state.preview_source_id == "CH1"
    assert state.active_ptz_camera_id == "cam1"


def test_unsupported_source_raises_and_keeps_previous_state() -> None:
    state, machine, _dispatcher, events = make_machine()
    machine.set_preview("CH1")

    with pytest.raises(UnsupportedSourceError):
        machine.set_preview("UNKNOWN")

    assert state.preview_source_id == "CH1"
    assert state.active_ptz_camera_id == "cam1"
    assert any(event.type == EventType.UNSUPPORTED_SOURCE for event in events)
