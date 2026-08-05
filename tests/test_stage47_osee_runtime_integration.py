from __future__ import annotations

import time
from collections import deque

from ptz_joystick_controller.app_state import AppState
from ptz_joystick_controller.config import parse_config
from ptz_joystick_controller.event_bus import EventBus
from ptz_joystick_controller.joystick.dispatcher import JoystickActionDispatcher
from ptz_joystick_controller.models.commands import CommandType
from ptz_joystick_controller.models.joystick_input import ButtonEvent
from ptz_joystick_controller.runtime.switcher_executor import SwitcherCommandExecutor
from ptz_joystick_controller.state_machine.preview_program import PreviewProgramStateMachine
from ptz_joystick_controller.state_machine.ptz_control import PtzControlStateMachine
from ptz_joystick_controller.switchers.factory import create_switcher
from ptz_joystick_controller.switchers.osee_gostream_duet import OseeGoStreamDuetSwitcher
from ptz_joystick_controller.switchers.osee_gsp import GspCommand, GspTransportError
from ptz_joystick_controller.webui.status import RuntimeStatusProvider


class FakeGspTransport:
    def __init__(self) -> None:
        self.connected = False
        self.sent: list[GspCommand] = []
        self.incoming: deque[tuple[GspCommand, ...]] = deque()
        self.fail_receive = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.disconnect(); self.connect()

    def send_command(self, command: GspCommand) -> bytes:
        if not self.connected:
            raise GspTransportError("not connected")
        self.sent.append(command)
        return b"packet"

    def send_get(self, command_id: str) -> bytes:
        return self.send_command(GspCommand(id=command_id, type="get"))

    def receive(self) -> tuple[GspCommand, ...]:
        if self.fail_receive:
            self.connected = False
            raise GspTransportError("cable unplugged")
        return self.incoming.popleft() if self.incoming else ()


def config():
    return parse_config({
        "switcher": {"type": "osee", "host": "192.0.2.10", "port": 19010},
        "sources": {"mappings": [
            {"source_id": "Input 1", "ptz_camera_id": "cam1"},
            {"source_id": "Input 2", "ptz_camera_id": "cam2"},
            {"source_id": "Input 3", "ptz_camera_id": None},
        ]},
        "ptz": {"cameras": [
            {"id": "cam1", "name": "Cam 1", "host": "192.0.2.101", "enabled": True},
            {"id": "cam2", "name": "Cam 2", "host": "192.0.2.102", "enabled": True},
        ]},
        "joystick": {"buttons": {
            "trigger": {"action": "cut"},
            "thumb": {"action": "copy_program_to_preview"},
            "button_3": {"action": "preview_source", "source_id": "Input 1"},
            "button_4": {"action": "preview_source", "source_id": "Input 2"},
        }},
    })


def runtime(transport: FakeGspTransport):
    cfg = config()
    switcher = OseeGoStreamDuetSwitcher(cfg.switcher.host or "", cfg.switcher.port or 19010, transport_factory=lambda h,p: transport)
    switcher.connect()
    bus = EventBus()
    state = AppState(cfg)
    ptz = PtzControlStateMachine(state, bus)
    preview = PreviewProgramStateMachine(state, bus, ptz)
    executor = SwitcherCommandExecutor(switcher, state, bus, preview, ptz)
    return cfg, switcher, bus, state, executor


def test_factory_selects_real_gsp_backend_and_alias() -> None:
    cfg = config()
    transport = FakeGspTransport()
    switcher = create_switcher(cfg.switcher, offline=False, osee_transport_factory=lambda h,p: transport)
    assert isinstance(switcher, OseeGoStreamDuetSwitcher)
    assert cfg.switcher.type == "osee_gostream_duet"
    assert switcher.port == 19010


def test_preview_button_routes_generic_source_to_osee_command() -> None:
    transport = FakeGspTransport(); cfg, _, bus, _, executor = runtime(transport)
    command = JoystickActionDispatcher(cfg, bus).dispatch_button_event(ButtonEvent("button_4", True))
    assert command and executor.execute(command)
    assert transport.sent[-1] == GspCommand(id="pvwIndex", type="set", value=(2,))


def test_trigger_routes_to_cut() -> None:
    transport = FakeGspTransport(); cfg, _, bus, _, executor = runtime(transport)
    command = JoystickActionDispatcher(cfg, bus).dispatch_button_event(ButtonEvent("trigger", True))
    assert command and command.type == CommandType.CUT and executor.execute(command)
    assert GspCommand(id="cutTransition", type="set") in transport.sent


def test_thumb_copies_program_to_preview() -> None:
    transport = FakeGspTransport(); cfg, switcher, bus, _, executor = runtime(transport)
    transport.incoming.append((GspCommand(id="pgmIndex", type="pus", value=(4001,)),))
    executor.sync_from_switcher()
    command = JoystickActionDispatcher(cfg, bus).dispatch_button_event(ButtonEvent("thumb", True))
    assert command and executor.execute(command)
    assert transport.sent[-1] == GspCommand(id="pvwIndex", type="set", value=(4001,))
    assert switcher.get_preview_source() == "Input 5"


def test_push_updates_runtime_ptz_and_dashboard() -> None:
    transport = FakeGspTransport(); _, switcher, bus, state, executor = runtime(transport)
    transport.incoming.append((
        GspCommand(id="pgmIndex", type="pus", value=(2,)),
        GspCommand(id="pvwIndex", type="pus", value=(1,)),
        GspCommand(id="transitionStatus", type="pus", value=(1,)),
    ))
    executor.sync_from_switcher()
    assert state.program_source_id == "Input 2"
    assert state.preview_source_id == "Input 1"
    assert state.active_ptz_camera_id == "cam1"
    assert state.transition_state == (1,)
    status = RuntimeStatusProvider(state=state, event_bus=bus, switcher=switcher).status()
    assert status["program"] == "Input 2"
    assert status["preview"] == "Input 1"
    assert status["transition"] == (1,)


def test_reconnect_rebuilds_transport_and_restores_operation() -> None:
    first = FakeGspTransport(); second = FakeGspTransport(); transports = iter((first, second))
    cfg = config()
    switcher = OseeGoStreamDuetSwitcher(cfg.switcher.host or "", 19010, transport_factory=lambda h,p: next(transports))
    switcher.connect()
    first.fail_receive = True
    switcher.poll()
    assert not switcher.is_connected()
    switcher.reconnect()
    deadline = time.time() + 1
    while not switcher.is_connected() and time.time() < deadline:
        time.sleep(0.01)
    assert switcher.is_connected()
    switcher.set_preview_source("Input 8")
    assert second.sent[-1] == GspCommand(id="pvwIndex", type="set", value=(4004,))

def test_preview_input_8_button_does_not_require_ptz_mapping() -> None:
    cfg = parse_config({
        "switcher": {"type": "osee", "host": "192.0.2.10"},
        "sources": {"mappings": [{"source_id": "Input 1", "ptz_camera_id": None}]},
        "joystick": {"buttons": {"button_5": {"action": "preview_source", "source_id": "Input 8"}}},
    })
    command = JoystickActionDispatcher(cfg, EventBus()).dispatch_button_event(ButtonEvent("button_5", True))
    assert command is not None
    assert command.source_id == "Input 8"
