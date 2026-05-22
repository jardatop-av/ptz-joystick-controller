from __future__ import annotations

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.dispatcher import JoystickActionDispatcher
from ptz_joystick_controller.models.commands import CommandType


def test_joystick_action_dispatcher_maps_button_to_internal_command() -> None:
    config = parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "sources": {"mappings": [{"source_id": "CH1", "ptz_camera_id": None}]},
            "joystick": {
                "buttons": {
                    "trigger": {"action": "cut"},
                    "button_3": {"action": "preview_source", "source_id": "CH1"},
                    "button_4": {"action": "none"},
                }
            },
        }
    )
    dispatcher = JoystickActionDispatcher(config=config, event_bus=EventBus())

    assert dispatcher.command_for_button("trigger").type == CommandType.CUT
    preview_command = dispatcher.command_for_button("button_3")
    assert preview_command.type == CommandType.SET_PREVIEW_SOURCE
    assert preview_command.source_id == "CH1"
    assert dispatcher.command_for_button("button_4").type == CommandType.NOOP
    assert dispatcher.command_for_button("missing").type == CommandType.NOOP
