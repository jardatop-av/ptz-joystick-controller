from __future__ import annotations

import logging

from ptz_joystick_controller.config import load_config, parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.button_metadata import (
    CANONICAL_BUTTON_IDS,
    DEFAULT_BUTTON_LABELS,
    ButtonMetadataRegistry,
)
from ptz_joystick_controller.joystick.device import FakeJoystickInputProvider
from ptz_joystick_controller.joystick.discovery import StaticJoystickDiscovery
from ptz_joystick_controller.joystick.dispatcher import JoystickActionDispatcher
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.models.commands import CommandType
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.runtime.joystick_switcher_bridge import JoystickToSwitcherBridge
from ptz_joystick_controller.switchers.fake import FakeSwitcher


def make_config():
    return parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "sources": {
                "mappings": [
                    {"source_id": "Input 1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
                    {"source_id": "Input 2", "display_name": "Camera 2", "ptz_camera_id": None},
                ]
            },
            "ptz": {"cameras": [{"id": "cam1", "name": "PTZ 1", "visca_id": 1}]},
            "joystick": {
                "buttons": {
                    "trigger": {"action": "cut"},
                    "thumb": {"action": "copy_program_to_preview"},
                    "button_5": {"action": "preview_source", "source_id": "Input 1"},
                    "button_7": {"action": "preset_recall", "preset_number": 1},
                    "button_11": {"action": "none"},
                }
            },
        }
    )


def make_bridge(provider: FakeJoystickInputProvider) -> JoystickToSwitcherBridge:
    config = make_config()
    device = JoystickDeviceInfo(name="Fake joystick", path="fake", backend="fake")
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=EventBus(),
        discovery=StaticJoystickDiscovery((device,)),
        provider_factory=lambda _device: provider,
    )
    return JoystickToSwitcherBridge(
        config=config,
        joystick_monitor=monitor,
        switcher=FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 2", preview_source_id="Input 2"),
    )


def test_button_metadata_loading() -> None:
    registry = ButtonMetadataRegistry()
    metadata = registry.all_metadata()

    assert set(metadata) == set(CANONICAL_BUTTON_IDS)
    assert metadata["button_5"].label == "Top left upper"
    assert metadata["trigger"].default_action.value == "cut"


def test_default_labels_exist() -> None:
    assert DEFAULT_BUTTON_LABELS["trigger"] == "Trigger / CUT"
    assert DEFAULT_BUTTON_LABELS["thumb"] == "Thumb / Program to Preview"
    assert DEFAULT_BUTTON_LABELS["button_12"] == "Base 12"


def test_unknown_label_fallback_to_button_id() -> None:
    assert ButtonMetadataRegistry().label_for("button_99") == "button_99"


def test_action_none_is_ignored_safely(caplog) -> None:
    provider = FakeJoystickInputProvider()
    bridge = make_bridge(provider)
    bridge.start()

    with caplog.at_level(logging.INFO):
        provider.press("button_11")
        status = bridge.poll_once()

    assert status.preview_source_id == "Input 2"
    assert bridge.switcher.transition_log == []
    assert bridge.ptz_router.command_log == []
    assert any("Joystick button disabled: Base 11 (button_11) -> disabled" in record.message for record in caplog.records)


def test_preview_source_mapping_still_works() -> None:
    dispatcher = JoystickActionDispatcher(make_config(), EventBus())

    command = dispatcher.command_for_button("button_5")

    assert command.type == CommandType.SET_PREVIEW_SOURCE
    assert command.source_id == "Input 1"
    assert dispatcher.describe_button_command("button_5", command) == "Top left upper (button_5) -> Preview Input 1"


def test_preset_recall_mapping_still_works() -> None:
    dispatcher = JoystickActionDispatcher(make_config(), EventBus())

    command = dispatcher.command_for_button("button_7")

    assert command.type == CommandType.PTZ_PRESET_RECALL
    assert command.preset_number == 1
    assert dispatcher.describe_button_command("button_7", command) == "Base 7 (button_7) -> Preset 1"


def test_disabled_buttons_do_not_execute_commands() -> None:
    provider = FakeJoystickInputProvider()
    bridge = make_bridge(provider)
    bridge.start()

    provider.press("button_11")
    bridge.poll_once()

    assert bridge.switcher.transition_log == []
    assert bridge.ptz_router.camera_command_count("cam1") == 0


def test_trigger_thumb_behavior_remains_unchanged() -> None:
    dispatcher = JoystickActionDispatcher(make_config(), EventBus())

    assert dispatcher.command_for_button("trigger").type == CommandType.CUT
    assert dispatcher.command_for_button("thumb").type == CommandType.COPY_PROGRAM_TO_PREVIEW


def test_config_example_stage25_button_mapping() -> None:
    config = load_config("config.example.yaml", use_local=False)

    assert config.joystick.buttons["button_3"].source_id == "Input 1"
    assert config.joystick.buttons["button_4"].source_id == "Input 2"
    assert config.joystick.buttons["button_5"].source_id == "Input 3"
    assert config.joystick.buttons["button_6"].source_id == "Input 4"
    assert config.joystick.buttons["button_7"].preset_number == 1
    assert config.joystick.buttons["button_8"].preset_number == 2
    assert config.joystick.buttons["button_9"].preset_number == 3
    assert config.joystick.buttons["button_10"].action.value == "none"
    assert config.joystick.buttons["button_11"].action.value == "none"
    assert config.joystick.buttons["button_12"].action.value == "none"
