from __future__ import annotations

from collections import deque

from fastapi.testclient import TestClient

from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.dispatcher import JoystickActionDispatcher
from ptz_joystick_controller.models.joystick_input import ButtonEvent
from ptz_joystick_controller.runtime.application import RuntimeApplication
from ptz_joystick_controller.runtime.switcher_executor import SwitcherCommandExecutor
from ptz_joystick_controller.state_machine.preview_program import PreviewProgramStateMachine
from ptz_joystick_controller.state_machine.ptz_control import PtzControlStateMachine
from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.switchers.factory import create_switcher
from ptz_joystick_controller.switchers.osee_gostream_duet import OseeGoStreamDuetSwitcher
from ptz_joystick_controller.switchers.osee_gsp import GspCommand
from ptz_joystick_controller.switchers.vmix import VmixSwitcher
from ptz_joystick_controller.webui import RuntimeStatusProvider, create_web_app


class FakeTransport:
    def __init__(self):
        self.connected = False
        self.sent: list[GspCommand] = []
        self.incoming: deque[tuple[GspCommand, ...]] = deque()
    def connect(self): self.connected = True
    def disconnect(self): self.connected = False
    def reconnect(self): self.connected = True
    def send_command(self, command): self.sent.append(command); return b"x"
    def send_get(self, command_id): return self.send_command(GspCommand(id=command_id, type="get"))
    def receive(self): return self.incoming.popleft() if self.incoming else ()


def osee_config():
    return parse_config({
        "switcher": {"type": "osee", "host": "192.0.2.20"},
        "sources": {"mappings": [{"source_id": "Input 1", "ptz_camera_id": "cam1"}]},
        "ptz": {"cameras": [{"id": "cam1", "name": "Cam 1", "host": "192.0.2.30", "enabled": True}]},
        "joystick": {"buttons": {
            "button_3": {"action": "preview_source", "source_id": "Input 5"},
            "trigger": {"action": "cut"},
            "thumb": {"action": "copy_program_to_preview"},
        }},
    })


def build_executor(transport: FakeTransport):
    cfg = osee_config()
    sw = create_switcher(cfg.switcher, offline=False, osee_transport_factory=lambda h, p: transport)
    sw.connect()
    bus = EventBus(); state = AppState(cfg); ptz = PtzControlStateMachine(state, bus)
    preview = PreviewProgramStateMachine(state, bus, ptz)
    return cfg, sw, bus, state, SwitcherCommandExecutor(sw, state, bus, preview, ptz)


def test_osee_alias_instantiates_verified_gsp_runtime_backend():
    transport = FakeTransport()
    app = RuntimeApplication(osee_config(), osee_transport_factory=lambda h, p: transport)
    assert isinstance(app.bridge.switcher, OseeGoStreamDuetSwitcher)
    assert app.bridge.switcher.port == 19010


def test_vmix_still_instantiates_vmix_backend():
    cfg = parse_config({"switcher": {"type": "vmix", "host": "127.0.0.1", "port": 8088}})
    assert isinstance(create_switcher(cfg.switcher, offline=False), VmixSwitcher)


def test_generic_preview_cut_and_copy_program_actions_use_osee_backend():
    transport = FakeTransport(); cfg, sw, bus, _, executor = build_executor(transport)
    dispatcher = JoystickActionDispatcher(cfg, bus)
    assert executor.execute(dispatcher.dispatch_button_event(ButtonEvent("button_3", True)))
    assert transport.sent[-1] == GspCommand(id="pvwIndex", type="set", value=(4001,))
    assert executor.execute(dispatcher.dispatch_button_event(ButtonEvent("trigger", True)))
    assert transport.sent[-1] == GspCommand(id="cutTransition", type="set")
    transport.incoming.append((GspCommand(id="pgmIndex", type="pus", value=(3010,)),))
    executor.sync_from_switcher()
    assert executor.execute(dispatcher.dispatch_button_event(ButtonEvent("thumb", True)))
    assert transport.sent[-1] == GspCommand(id="pvwIndex", type="set", value=(3010,))


def test_push_updates_generic_state_ptz_and_dashboard():
    transport = FakeTransport(); _, sw, bus, state, executor = build_executor(transport)
    transport.incoming.append((
        GspCommand(id="pgmIndex", type="pus", value=(2,)),
        GspCommand(id="pvwIndex", type="pus", value=(1,)),
        GspCommand(id="transitionStatus", type="pus", value=(1,)),
    ))
    executor.sync_from_switcher()
    assert state.program_source_id == "Input 2"
    assert state.preview_source_id == "Input 1"
    assert state.active_ptz_camera_id == "cam1"
    status = RuntimeStatusProvider(state, bus, switcher=sw).status()
    assert status["program"] == "Input 2"
    assert status["preview"] == "Input 1"
    assert status["transition"] == (1,)


def test_config_page_has_switcher_selector_and_osee_logical_sources(tmp_path):
    cfg = osee_config(); state = AppState(cfg); provider = RuntimeStatusProvider(state)
    app = create_web_app(provider, config_example_path=tmp_path/"config.example.yaml", config_local_path=tmp_path/"config.local.yaml")
    html = TestClient(app).get("/config").text
    assert "Osee GoStream Duet 8 ISO" in html
    for source in [*(f"Input {i}" for i in range(1, 9)), "MP1", "MP2", "M/SRC"]:
        assert f'value="{source}"' in html
    assert "4001" not in html
