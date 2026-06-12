from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ControllerConfig
from ..event_bus import EventBus
from ..models.commands import Command, CommandError, CommandType, EventType
from ..models.joystick import ButtonAction
from ..models.joystick_input import ButtonEvent
from .button_metadata import ButtonMetadataRegistry


@dataclass
class JoystickActionDispatcher:
    config: ControllerConfig
    event_bus: EventBus
    metadata: ButtonMetadataRegistry = field(init=False)

    def __post_init__(self) -> None:
        self.metadata = ButtonMetadataRegistry(self.config.joystick.button_labels)

    def label_for_button(self, button_name: str) -> str:
        return self.metadata.label_for(button_name)

    def describe_button_mapping(self, button_name: str) -> str:
        return self.metadata.describe_mapping(button_name, self.config.joystick.buttons.get(button_name))

    def describe_button_command(self, button_name: str, command: Command) -> str:
        return self.metadata.describe_command(button_name, command)

    def command_for_button(self, button_name: str) -> Command:
        mapping = self.config.joystick.buttons.get(button_name)
        button_label = self.label_for_button(button_name)
        payload = {"button_id": button_name, "button_label": button_label}
        if mapping is None or mapping.action == ButtonAction.NONE:
            return Command(type=CommandType.NOOP, origin=f"joystick:{button_name}", payload=payload)
        if mapping.action == ButtonAction.CUT:
            return Command(type=CommandType.CUT, origin=f"joystick:{button_name}", payload=payload)
        if mapping.action == ButtonAction.AUTO:
            return Command(type=CommandType.AUTO, origin=f"joystick:{button_name}", payload=payload)
        if mapping.action == ButtonAction.COPY_PROGRAM_TO_PREVIEW:
            return Command(type=CommandType.COPY_PROGRAM_TO_PREVIEW, origin=f"joystick:{button_name}", payload=payload)
        if mapping.action == ButtonAction.PREVIEW_SOURCE:
            if mapping.source_id is None:
                raise CommandError(f"Button {button_name} preview_source action has no source_id")
            if not self.config.sources.has_source(mapping.source_id):
                raise CommandError(f"Button {button_name} references unsupported source_id: {mapping.source_id}")
            return Command(
                type=CommandType.SET_PREVIEW_SOURCE,
                source_id=mapping.source_id,
                origin=f"joystick:{button_name}",
                payload=payload,
            )
        if mapping.action == ButtonAction.PRESET_RECALL:
            if mapping.preset_number is None:
                raise CommandError(f"Button {button_name} preset_recall action has no preset_number")
            return Command(
                type=CommandType.PTZ_PRESET_RECALL,
                preset_number=mapping.preset_number,
                origin=f"joystick:{button_name}",
                payload=payload,
            )
        raise CommandError(f"Unsupported button action: {mapping.action}")

    def dispatch_button(self, button_name: str) -> Command:
        command = self.command_for_button(button_name)
        self.event_bus.publish(EventType.COMMAND_DISPATCHED, {"command": command})
        return command

    def dispatch_button_event(self, event: ButtonEvent) -> Command | None:
        if not event.pressed:
            return None
        return self.dispatch_button(event.button_name)
