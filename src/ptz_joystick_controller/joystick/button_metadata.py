from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..models.commands import Command, CommandType
from ..models.joystick import ButtonAction, ButtonMapping

CANONICAL_BUTTON_IDS: tuple[str, ...] = (
    "trigger",
    "thumb",
    "button_3",
    "button_4",
    "button_5",
    "button_6",
    "button_7",
    "button_8",
    "button_9",
    "button_10",
    "button_11",
    "button_12",
)

DEFAULT_BUTTON_LABELS: dict[str, str] = {
    "trigger": "Trigger / CUT",
    "thumb": "Thumb / Program to Preview",
    "button_3": "Top left lower",
    "button_4": "Top right lower",
    "button_5": "Top left upper",
    "button_6": "Top right upper",
    "button_7": "Base 7",
    "button_8": "Base 8",
    "button_9": "Base 9",
    "button_10": "Base 10",
    "button_11": "Base 11",
    "button_12": "Base 12",
}

CONFIGURABLE_BUTTON_ACTIONS: tuple[ButtonAction, ...] = (
    ButtonAction.PREVIEW_SOURCE,
    ButtonAction.PRESET_RECALL,
    ButtonAction.NONE,
)

FIXED_DEFAULT_BUTTON_ACTIONS: dict[str, ButtonAction] = {
    "trigger": ButtonAction.CUT,
    "thumb": ButtonAction.COPY_PROGRAM_TO_PREVIEW,
}


@dataclass(frozen=True)
class ButtonMetadata:
    button_id: str
    label: str
    default_action: ButtonAction = ButtonAction.NONE
    configurable_actions: tuple[ButtonAction, ...] = CONFIGURABLE_BUTTON_ACTIONS

    @property
    def is_canonical(self) -> bool:
        return self.button_id in CANONICAL_BUTTON_IDS


class ButtonMetadataRegistry:
    """Human-readable joystick button metadata for debug tools and future GUI.

    The registry is intentionally separate from button mappings. A button can be
    known and labelled even when its current action is disabled/no-op.
    """

    def __init__(self, labels: Mapping[str, str] | None = None) -> None:
        merged = dict(DEFAULT_BUTTON_LABELS)
        if labels:
            merged.update({str(key): str(value) for key, value in labels.items()})
        self._labels = merged

    def label_for(self, button_id: str) -> str:
        return self._labels.get(button_id, button_id)

    def metadata_for(self, button_id: str) -> ButtonMetadata:
        return ButtonMetadata(
            button_id=button_id,
            label=self.label_for(button_id),
            default_action=FIXED_DEFAULT_BUTTON_ACTIONS.get(button_id, ButtonAction.NONE),
            configurable_actions=CONFIGURABLE_BUTTON_ACTIONS,
        )

    def all_metadata(self) -> dict[str, ButtonMetadata]:
        return {button_id: self.metadata_for(button_id) for button_id in CANONICAL_BUTTON_IDS}

    def describe_mapping(self, button_id: str, mapping: ButtonMapping | None) -> str:
        label = self.label_for(button_id)
        if mapping is None or mapping.action == ButtonAction.NONE:
            return f"{label} ({button_id}) -> disabled"
        if mapping.action == ButtonAction.PREVIEW_SOURCE:
            return f"{label} ({button_id}) -> Preview {mapping.source_id}"
        if mapping.action == ButtonAction.PRESET_RECALL:
            return f"{label} ({button_id}) -> Preset {mapping.preset_number}"
        if mapping.action == ButtonAction.CUT:
            return f"{label} ({button_id}) -> CUT"
        if mapping.action == ButtonAction.AUTO:
            return f"{label} ({button_id}) -> AUTO"
        if mapping.action == ButtonAction.COPY_PROGRAM_TO_PREVIEW:
            return f"{label} ({button_id}) -> Copy Program To Preview"
        return f"{label} ({button_id}) -> {mapping.action.value}"

    def describe_command(self, button_id: str, command: Command) -> str:
        label = self.label_for(button_id)
        if command.type == CommandType.NOOP:
            return f"{label} ({button_id}) -> disabled"
        if command.type == CommandType.SET_PREVIEW_SOURCE:
            return f"{label} ({button_id}) -> Preview {command.source_id}"
        if command.type == CommandType.PTZ_PRESET_RECALL:
            return f"{label} ({button_id}) -> Preset {command.preset_number}"
        if command.type == CommandType.CUT:
            return f"{label} ({button_id}) -> CUT"
        if command.type == CommandType.AUTO:
            return f"{label} ({button_id}) -> AUTO"
        if command.type == CommandType.COPY_PROGRAM_TO_PREVIEW:
            return f"{label} ({button_id}) -> Copy Program To Preview"
        return f"{label} ({button_id}) -> {command.type.value}"


def default_button_metadata_registry(labels: Mapping[str, str] | None = None) -> ButtonMetadataRegistry:
    return ButtonMetadataRegistry(labels)
