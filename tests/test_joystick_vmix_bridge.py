from __future__ import annotations

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.device import FakeJoystickInputProvider
from ptz_joystick_controller.joystick.discovery import StaticJoystickDiscovery
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.runtime.joystick_switcher_bridge import JoystickToSwitcherBridge
from ptz_joystick_controller.switchers.fake import FakeSwitcher


def make_vmix_config():
    return parse_config(
        {
            "switcher": {"type": "vmix", "host": None},
            "sources": {
                "mappings": [
                    {"source_id": "Input 1", "display_name": "Camera 1", "ptz_camera_id": "cam1"},
                    {"source_id": "Input 2", "display_name": "Camera 2", "ptz_camera_id": "cam2"},
                    {"source_id": "Input 5", "display_name": "Camera 5", "ptz_camera_id": None},
                ]
            },
            "ptz": {
                "stop_on_switch": True,
                "cameras": [
                    {"id": "cam1", "name": "Camera 1"},
                    {"id": "cam2", "name": "Camera 2"},
                ],
            },
            "joystick": {
                "buttons": {
                    "trigger": {"action": "cut"},
                    "button_2": {"action": "auto"},
                    "thumb": {"action": "copy_program_to_preview"},
                    "button_7": {"action": "preview_source", "source_id": "Input 5"},
                }
            },
        }
    )


def make_bridge(provider: FakeJoystickInputProvider, *, switcher: FakeSwitcher | None = None) -> JoystickToSwitcherBridge:
    config = make_vmix_config()
    device = JoystickDeviceInfo(name="Fake Logitech", path="fake", backend="fake")
    discovery = StaticJoystickDiscovery((device,))
    monitor = JoystickRuntimeMonitor(
        config=config,
        event_bus=EventBus(),
        discovery=discovery,
        provider_factory=lambda _device: provider,
    )
    return JoystickToSwitcherBridge(
        config=config,
        joystick_monitor=monitor,
        switcher=switcher or FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 1", preview_source_id="Input 2"),
    )


def test_button_preview_source_executes_switcher_preview_input() -> None:
    provider = FakeJoystickInputProvider()
    bridge = make_bridge(provider)
    bridge.start()

    provider.press("button_7")
    status = bridge.poll_once()

    assert status.preview_source_id == "Input 5"
    assert bridge.switcher.get_preview_source() == "Input 5"
    assert status.active_ptz_camera_id is None


def test_trigger_cut_executes_cut_and_requests_stop() -> None:
    provider = FakeJoystickInputProvider()
    bridge = make_bridge(provider)
    bridge.start()

    provider.press("trigger")
    status = bridge.poll_once()

    assert bridge.switcher.transition_log == ["cut"]
    assert bridge.state.stop_requests == ["before_cut"]
    assert status.program_source_id == "Input 2"
    assert status.preview_source_id == "Input 1"
    assert status.active_ptz_camera_id == "cam1"


def test_auto_button_executes_fade_auto_path() -> None:
    provider = FakeJoystickInputProvider()
    bridge = make_bridge(provider)
    bridge.start()

    provider.press("button_2")
    status = bridge.poll_once()

    assert bridge.switcher.transition_log == ["auto"]
    assert bridge.state.stop_requests == ["before_auto"]
    assert status.program_source_id == "Input 2"
    assert status.preview_source_id == "Input 1"


def test_thumb_copies_program_to_preview() -> None:
    provider = FakeJoystickInputProvider()
    bridge = make_bridge(provider)
    bridge.start()

    provider.press("thumb")
    status = bridge.poll_once()

    assert status.program_source_id == "Input 1"
    assert status.preview_source_id == "Input 1"
    assert status.active_ptz_camera_id == "cam1"


def test_bridge_starts_without_joystick_connected() -> None:
    config = make_vmix_config()
    monitor = JoystickRuntimeMonitor(config=config, event_bus=EventBus(), discovery=StaticJoystickDiscovery(()), provider_factory=None)
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 1", preview_source_id="Input 2")
    bridge = JoystickToSwitcherBridge(config=config, joystick_monitor=monitor, switcher=switcher)

    bridge.start()
    status = bridge.poll_once()

    assert status.joystick_connected is False
    assert status.switcher_connected is True
    assert status.preview_source_id == "Input 2"
    assert status.active_ptz_camera_id == "cam2"


def test_vmix_reported_input_3_is_valid_without_ptz_mapping() -> None:
    provider = FakeJoystickInputProvider()
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 3", preview_source_id="Input 3")
    bridge = make_bridge(provider, switcher=switcher)

    bridge.start()
    status = bridge.poll_once()

    assert status.program_source_id == "Input 3"
    assert status.preview_source_id == "Input 3"
    assert status.active_ptz_camera_id is None
    assert status.last_error is None


def test_vmix_source_ids_input_1_3_100_are_valid_for_bridge_state() -> None:
    provider = FakeJoystickInputProvider()
    for source_id in ("Input 1", "Input 3", "Input 100"):
        switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 1", preview_source_id=source_id)
        bridge = make_bridge(provider, switcher=switcher)

        bridge.start()
        status = bridge.poll_once()

        assert status.preview_source_id == source_id
        expected_ptz = "cam1" if source_id == "Input 1" else None
        assert status.active_ptz_camera_id == expected_ptz


def test_unsupported_switcher_source_logs_warning_without_crashing_bridge(caplog) -> None:
    provider = FakeJoystickInputProvider()
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="Input 1", preview_source_id="Input 101")
    bridge = make_bridge(provider, switcher=switcher)

    bridge.start()
    status = bridge.poll_once()

    assert status.program_source_id == "Input 1"
    assert status.preview_source_id is None
    assert status.active_ptz_camera_id is None
    assert "Unsupported or unmapped source_id: Input 101" in (status.last_error or "")
    assert any("unsupported preview source Input 101" in record.message for record in caplog.records)


def test_sync_from_switcher_accepts_vmix_inputs_without_warnings(caplog) -> None:
    provider = FakeJoystickInputProvider()
    caplog.set_level("WARNING")

    for source_id in ("Input 1", "Input 3", "Input 100"):
        switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id=source_id, preview_source_id=source_id)
        bridge = make_bridge(provider, switcher=switcher)

        bridge.start()
        status = bridge.poll_once()

        assert status.program_source_id == source_id
        assert status.preview_source_id == source_id
        assert status.active_ptz_camera_id == ("cam1" if source_id == "Input 1" else None)
        assert status.last_error is None

    assert not [record for record in caplog.records if "unsupported" in record.message.lower()]


def test_sync_from_switcher_normalizes_vmix_input_number_without_warning(caplog) -> None:
    provider = FakeJoystickInputProvider()
    caplog.set_level("WARNING")
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id="3", preview_source_id="input 100")
    bridge = make_bridge(provider, switcher=switcher)

    bridge.start()
    status = bridge.poll_once()

    assert status.program_source_id == "Input 3"
    assert status.preview_source_id == "Input 100"
    assert status.active_ptz_camera_id is None
    assert status.last_error is None
    assert not [record for record in caplog.records if "unsupported" in record.message.lower()]
