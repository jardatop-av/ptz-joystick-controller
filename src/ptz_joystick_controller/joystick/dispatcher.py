from __future__ import annotations

from dataclasses import dataclass

from ..config import ControllerConfig
from ..event_bus import EventBus
from ..models.commands import Command, CommandError, CommandType, EventType
from ..models.joystick import ButtonAction


@dataclass
class JoystickActionDispatcher:
    config: ControllerConfig
    event_bus: EventBus

    def command_for_button(self, button_name: str) -> Command:
        mapping = self.config.joystick.buttons.get(button_name)
        if mapping is None or mapping.action == ButtonAction.NONE:
            return Command(type=CommandType.NOOP, origin=f"joystick:{button_name}")
        if mapping.action == ButtonAction.CUT:
            return Command(type=CommandType.CUT, origin=f"joystick:{button_name}")
        if mapping.action == ButtonAction.AUTO:
            return Command(type=CommandType.AUTO, origin=f"joystick:{button_name}")
        if mapping.action == ButtonAction.COPY_PROGRAM_TO_PREVIEW:
            return Command(type=CommandType.COPY_PROGRAM_TO_PREVIEW, origin=f"joystick:{button_name}")
        if mapping.action == ButtonAction.PREVIEW_SOURCE:
            if mapping.source_id is None:
                raise CommandError(f"Button {button_name} preview_source action has no source_id")
            if not self.config.sources.has_source(mapping.source_id):
                raise CommandError(f"Button {button_name} references unsupported source_id: {mapping.source_id}")
            return Command(
                type=CommandType.SET_PREVIEW_SOURCE,
                source_id=mapping.source_id,
                origin=f"joystick:{button_name}",
            )
        raise CommandError(f"Unsupported button action: {mapping.action}")

    def dispatch_button(self, button_name: str) -> Command:
        command = self.command_for_button(button_name)
        self.event_bus.publish(EventType.COMMAND_DISPATCHED, {"command": command})
        return command
