from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import load_config, parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.device import FakeJoystickInputProvider
from ptz_joystick_controller.joystick.runtime import JoystickRuntimeMonitor
from ptz_joystick_controller.models.commands import CommandType
from ptz_joystick_controller.models.joystick import ButtonMapping
from ptz_joystick_controller.models.joystick_runtime import JoystickDeviceInfo
from ptz_joystick_controller.models.switcher import SwitcherType
from ptz_joystick_controller.runtime.joystick_switcher_bridge import JoystickToSwitcherBridge
from ptz_joystick_controller.switchers.fake import FakeSwitcher
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


class StaticDiscovery:
    def discover(self):
        return [JoystickDeviceInfo(name='Fake Joystick', path='fake0', backend='fake')]


def _config(button='trigger'):
    return parse_config({
        'switcher': {'type': 'vmix', 'host': None},
        'sources': {'mappings': [
            {'source_id': 'Input 1', 'display_name': 'Camera 1', 'ptz_camera_id': 'cam1'},
            {'source_id': 'Input 2', 'display_name': 'Camera 2', 'ptz_camera_id': 'cam2'},
        ]},
        'ptz': {'stop_on_switch': True, 'cameras': [
            {'id': 'cam1', 'name': 'Camera 1'}, {'id': 'cam2', 'name': 'Camera 2'}
        ]},
        'joystick': {'buttons': {button: {'action': 'auto'}, 'thumb': {'action': 'copy_program_to_preview'}}},
    })


def _bridge(button='trigger'):
    config = _config(button)
    bus = EventBus()
    provider = FakeJoystickInputProvider()
    monitor = JoystickRuntimeMonitor(config=config, event_bus=bus, discovery=StaticDiscovery(), provider_factory=lambda _d: provider)
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id='Input 1', preview_source_id='Input 2')
    bridge = JoystickToSwitcherBridge(config, monitor, switcher, bus)
    bridge.start()
    return bridge, switcher


def test_config_accepts_auto_action() -> None:
    config = _config('button_10')
    assert config.joystick.buttons['button_10'].action.value == 'auto'


@pytest.mark.parametrize('payload, expected', [
    ({'action': 'auto', 'preset_number': 1}, 'preset_number is allowed only'),
    ({'action': 'auto', 'source_id': 'Input 1'}, 'source_id is allowed only'),
])
def test_auto_rejects_irrelevant_payload(payload, expected) -> None:
    with pytest.raises(ValidationError, match=expected):
        ButtonMapping.model_validate(payload)


def test_pressing_auto_calls_switcher_auto_and_stops_ptz_first() -> None:
    bridge, switcher = _bridge('button_10')
    bridge.state.preview_source_id = 'Input 2'
    bridge.state.recompute_active_ptz()
    bridge.ptz_router.pan_tilt_active = True
    command = bridge.joystick_dispatcher.command_for_button('button_10')
    assert command.type == CommandType.AUTO
    assert bridge.switcher_executor.execute(command) is True
    assert switcher.transition_log[-1] == 'auto'
    assert bridge.state.stop_requests[-1] == 'before_auto'


def test_cut_behavior_remains_unchanged() -> None:
    config = _config()
    config_data = config.model_dump(mode='json')
    config_data['joystick']['buttons']['trigger'] = {'action': 'cut'}
    cut_config = parse_config(config_data)
    bus = EventBus()
    provider = FakeJoystickInputProvider()
    monitor = JoystickRuntimeMonitor(config=cut_config, event_bus=bus, discovery=StaticDiscovery(), provider_factory=lambda _d: provider)
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id='Input 1', preview_source_id='Input 2')
    bridge = JoystickToSwitcherBridge(cut_config, monitor, switcher, bus)
    bridge.start()
    command = bridge.joystick_dispatcher.command_for_button('trigger')
    assert command.type == CommandType.CUT
    bridge.switcher_executor.execute(command)
    assert switcher.transition_log[-1] == 'cut'
    assert bridge.state.stop_requests[-1] == 'before_cut'


def test_web_form_exposes_auto_for_trigger_and_other_buttons(tmp_path: Path) -> None:
    example = tmp_path / 'config.example.yaml'
    example.write_text(Path('config.example.yaml').read_text(encoding='utf-8'), encoding='utf-8')
    local = tmp_path / 'config.local.yaml'
    config = load_config(example, local_path=local)
    provider = RuntimeStatusProvider(state=AppState(config=config), event_bus=EventBus())
    client = TestClient(create_web_app(provider, config_example_path=example, config_local_path=local))
    html = client.get('/config').text
    assert "name='button_trigger_action'" in html
    assert "name='button_button_10_action'" in html
    assert html.count('value="auto"') >= 12


def test_save_and_apply_auto_mapping_succeeds(tmp_path: Path) -> None:
    example = tmp_path / 'config.example.yaml'
    example.write_text(Path('config.example.yaml').read_text(encoding='utf-8'), encoding='utf-8')
    local = tmp_path / 'config.local.yaml'
    config = load_config(example, local_path=local)
    bus = EventBus()
    provider = FakeJoystickInputProvider()
    monitor = JoystickRuntimeMonitor(config=config, event_bus=bus, discovery=StaticDiscovery(), provider_factory=lambda _d: provider)
    switcher = FakeSwitcher(SwitcherType.VMIX, program_source_id='Input 1', preview_source_id='Input 2')
    bridge = JoystickToSwitcherBridge(config, monitor, switcher, bus, dry_run=True)
    bridge.start()
    status = RuntimeStatusProvider.from_bridge(bridge)
    client = TestClient(create_web_app(status, config_example_path=example, config_local_path=local))
    editable = client.get('/api/config').json()['editable_config']
    editable['joystick']['buttons']['trigger'] = {'action': 'auto'}
    response = client.post('/config/basic', json=editable)
    assert response.status_code == 200
    apply_response = client.post('/api/config/apply')
    assert apply_response.status_code == 200
    assert bridge.joystick_dispatcher.command_for_button('trigger').type == CommandType.AUTO
