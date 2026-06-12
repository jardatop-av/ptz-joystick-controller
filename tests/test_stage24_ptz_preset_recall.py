from __future__ import annotations

import logging

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.device import FakeJoystickInputProvider
from ptz_joystick_controller.joystick.dispatcher import JoystickActionDispatcher
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.models.commands import CommandType
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.ptz import FakePresetTransport, ViscaCommandBuilder
from ptz_joystick_controller.runtime.joystick_switcher_bridge import JoystickToSwitcherBridge
from ptz_joystick_controller.switchers.fake import FakeSwitcher


class StaticFakeJoystickDiscovery:
    def discover(self):
        return [JoystickDeviceInfo(name="Fake joystick", path="fake", backend="fake")]


def make_config():
    return parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "sources": {
                "mappings": [
                    {"source_id": "Input 1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
                    {"source_id": "Input 2", "display_name": "Camera 2", "ptz_camera_id": "cam2"},
                    {"source_id": "Input 3", "display_name": "No PTZ", "ptz_camera_id": None},
                ]
            },
            "ptz": {
                "cameras": [
                    {"id": "cam1", "name": "PTZ 1", "visca_id": 1},
                    {"id": "cam2", "name": "PTZ 2", "visca_id": 2},
                ]
            },
            "joystick": {
                "buttons": {
                    "button_7": {"action": "preset_recall", "preset_number": 1},
                    "button_8": {"action": "preset_recall", "preset_number": 2},
                    "button_9": {"action": "preset_recall", "preset_number": 3},
                }
            },
        }
    )


def make_bridge(*, preview: str = "Input 1"):
    config = make_config()
    bus = EventBus()
    provider = FakeJoystickInputProvider()
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=bus,
        discovery=StaticFakeJoystickDiscovery(),
        provider_factory=lambda _device: provider,
    )
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 3", preview_source_id=preview)
    bridge = JoystickToSwitcherBridge(config, monitor, switcher, bus, dry_run=False)
    bridge.start()
    return bridge, provider


def test_button_mapping_action_creates_preset_recall_command() -> None:
    config = make_config()
    dispatcher = JoystickActionDispatcher(config, EventBus())
    command = dispatcher.command_for_button("button_7")
    assert command.type == CommandType.PTZ_PRESET_RECALL
    assert command.preset_number == 1


def test_visca_preset_recall_payload() -> None:
    command = ViscaCommandBuilder(visca_id=1).preset_recall(3)
    assert command.description == "preset_recall:3"
    assert command.payload == bytes([0x81, 0x01, 0x04, 0x3F, 0x02, 0x03, 0xFF])


def test_fake_preset_transport_records_recall() -> None:
    config = make_config()
    camera = config.ptz.cameras[0]
    fake = FakePresetTransport()
    packet = fake.recall_preset(camera, 2)
    assert fake.recalls[0].camera_id == "cam1"
    assert fake.recalls[0].preset_number == 2
    assert packet.endswith(bytes([0x81, 0x01, 0x04, 0x3F, 0x02, 0x02, 0xFF]))


def test_preset_recall_routes_to_active_ptz_camera(caplog) -> None:
    bridge, _provider = make_bridge(preview="Input 1")
    with caplog.at_level(logging.INFO):
        assert bridge.ptz_router.recall_preset(1) is True
    assert any("cam1:preset_recall preset=1" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.ptz_router.camera_command_count("cam1") == 1
    assert bridge.ptz_router.camera_command_count("cam2") == 0
    assert any("PTZ PRESET RECALL camera=cam1 preset=1" in record.getMessage() for record in caplog.records)


def test_button_event_preset_recall_uses_active_camera_only() -> None:
    bridge, provider = make_bridge(preview="Input 2")
    provider.press("button_8")
    bridge.poll_once()
    assert any("cam2:preset_recall preset=2" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.ptz_router.camera_command_count("cam1") == 0
    assert bridge.ptz_router.camera_command_count("cam2") == 1


def test_preset_recall_without_active_ptz_is_ignored_safely() -> None:
    bridge, provider = make_bridge(preview="Input 3")
    provider.press("button_7")
    bridge.poll_once()
    assert bridge.state.active_ptz_camera_id is None
    assert any("preset_ignored preset=1 reason=no_active_ptz" in entry for entry in bridge.ptz_router.command_log)
    assert bridge.ptz_router.camera_command_count("cam1") == 0
    assert bridge.ptz_router.camera_command_count("cam2") == 0

from ptz_joystick_controller.models.joystick_input import PtzVelocity
from ptz_joystick_controller.ptz import CameraSession, FakeViscaTransport


def test_camera_session_preset_recall_sends_only_recall_packet_by_default() -> None:
    config = make_config()
    camera = config.ptz.cameras[0]
    transport = FakeViscaTransport()
    session = CameraSession(camera=camera, transport=transport)

    packet = session.recall_preset(1)

    assert transport.sent_packets == [packet]
    assert len(session.command_log) == 1
    assert session.command_log[0].command.description == "preset_recall:1"
    assert packet.endswith(bytes([0x81, 0x01, 0x04, 0x3F, 0x02, 0x01, 0xFF]))


def test_router_preset_recall_does_not_stop_after_recall() -> None:
    bridge, _provider = make_bridge(preview="Input 1")

    assert bridge.ptz_router.recall_preset(1) is True

    session = bridge.ptz_router.sessions["cam1"].session
    descriptions = [entry.command.description for entry in session.command_log]
    assert descriptions == ["preset_recall:1"]


def test_router_preset_recall_stops_active_movement_before_recall_only() -> None:
    bridge, _provider = make_bridge(preview="Input 1")

    bridge.ptz_router.route_velocity(PtzVelocity(pan=0.5))
    assert bridge.ptz_router.pan_tilt_active is True
    assert bridge.ptz_router.recall_preset(2, stop_before_recall=True) is True

    session = bridge.ptz_router.sessions["cam1"].session
    descriptions = [entry.command.description for entry in session.command_log]
    assert descriptions == ["pan_tilt", "pan_tilt", "preset_recall:2"]
    assert bridge.ptz_router.pan_tilt_active is False
    assert bridge.ptz_router.zoom_active is False
    assert session.state.last_command == "preset_recall:2"


def test_router_preset_recall_can_skip_stop_before_recall() -> None:
    bridge, _provider = make_bridge(preview="Input 1")

    bridge.ptz_router.route_velocity(PtzVelocity(pan=0.5))
    assert bridge.ptz_router.recall_preset(3, stop_before_recall=False) is True

    session = bridge.ptz_router.sessions["cam1"].session
    descriptions = [entry.command.description for entry in session.command_log]
    assert descriptions == ["pan_tilt", "preset_recall:3"]

import os
import subprocess
import sys
from pathlib import Path


def test_manual_preset_recall_script_exposes_hold_after_send_option() -> None:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "manual_ptz_preset_recall.py"), "--help"],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    assert "--hold-after-send" in result.stdout
    assert "Default: 2.0" in result.stdout


def test_manual_preset_recall_script_documents_no_stop_after_recall() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "manual_ptz_preset_recall.py").read_text()
    assert "without sending any stop-after-recall command" in script
    assert "Waiting after preset recall" in script
