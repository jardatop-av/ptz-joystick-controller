from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from ..app_state import AppState
from ..config import ControllerConfig
from ..event_bus import EventBus
from ..joystick.dispatcher import JoystickActionDispatcher
from ..joystick.runtime import JoystickRuntimeMonitor
from ..state_machine.preview_program import PreviewProgramStateMachine
from ..state_machine.ptz_control import PtzControlStateMachine
from ..switchers.base import AbstractSwitcher
from .switcher_executor import SwitcherCommandExecutor
from .ptz_router import PtzRouter, PtzRouterDiagnostics
from ..models.commands import CommandType
from ..models.ptz import PtzCamera
from ..ptz.transport import PtzTransport

LOGGER = logging.getLogger(__name__)


@dataclass
class JoystickToSwitcherBridgeStatus:
    joystick_connected: bool
    switcher_connected: bool
    program_source_id: str | None
    preview_source_id: str | None
    active_ptz_camera_id: str | None
    active_ptz_diagnostics: PtzRouterDiagnostics | None = None
    last_error: str | None = None


@dataclass
class JoystickToSwitcherBridge:
    """Runtime bridge from joystick button events to switcher commands.

    This bridge intentionally handles only switcher actions. PTZ socket control,
    GUI rendering and discovery are outside this layer.
    """

    config: ControllerConfig
    joystick_monitor: JoystickRuntimeMonitor
    switcher: AbstractSwitcher
    event_bus: EventBus = field(default_factory=EventBus)
    dry_run: bool = False
    ptz_transport_factory: Callable[[PtzCamera], PtzTransport] | None = None
    state: AppState = field(init=False)
    ptz_control: PtzControlStateMachine = field(init=False)
    preview_program: PreviewProgramStateMachine = field(init=False)
    joystick_dispatcher: JoystickActionDispatcher = field(init=False)
    switcher_executor: SwitcherCommandExecutor = field(init=False)
    ptz_router: PtzRouter = field(init=False)

    def __post_init__(self) -> None:
        self.state = AppState(config=self.config)
        self.ptz_control = PtzControlStateMachine(self.state, self.event_bus)
        self.preview_program = PreviewProgramStateMachine(self.state, self.event_bus, self.ptz_control)
        self.joystick_dispatcher = JoystickActionDispatcher(self.config, self.event_bus)
        self.ptz_router = PtzRouter(self.state, self.event_bus, transport_factory=self.ptz_transport_factory)
        self.switcher_executor = SwitcherCommandExecutor(
            switcher=self.switcher,
            state=self.state,
            event_bus=self.event_bus,
            preview_program=self.preview_program,
            ptz_control=self.ptz_control,
            dry_run=self.dry_run,
        )

    def start(self) -> None:
        LOGGER.info("Joystick-to-switcher bridge starting dry_run=%s", self.dry_run)
        self.joystick_monitor.start()
        self._connect_switcher_safely()
        self.switcher_executor.sync_from_switcher()
        self.log_status()

    def _connect_switcher_safely(self) -> None:
        try:
            self.switcher.connect()
            self.state.switcher_connected = self.switcher.is_connected()
        except Exception as exc:
            self.state.switcher_connected = False
            self.state.last_error = str(exc)
            LOGGER.info("Switcher connect failed safely: %s", exc)
            self.event_bus.publish("switcher.connect_failed", {"error": str(exc)})

    def poll_once(self) -> JoystickToSwitcherBridgeStatus:
        """Poll joystick, execute pending button actions and synchronize switcher state."""

        snapshot = self.joystick_monitor.poll()
        self.state.joystick_connected = self.joystick_monitor.health.connected

        if not self.switcher.is_connected():
            self.switcher_executor.ensure_connected()
        else:
            self.switcher_executor.sync_from_switcher()

        provider = self.joystick_monitor.provider
        if provider is not None:
            for button_event in provider.button_events():
                event_type = "pressed" if button_event.pressed else "released"
                LOGGER.info("Joystick button %s: %s", event_type, button_event.button_name)
                if not button_event.pressed:
                    continue
                command = self.joystick_dispatcher.dispatch_button_event(button_event)
                if command is not None:
                    if command.type == CommandType.PTZ_PRESET_RECALL:
                        if command.preset_number is not None:
                            self.ptz_router.recall_preset(command.preset_number)
                    else:
                        self.switcher_executor.execute(command)

        if snapshot is None:
            LOGGER.debug("Bridge poll: no joystick snapshot available")
        else:
            self.ptz_router.route_controls(
                self.joystick_monitor.ptz_velocity(snapshot),
                self.joystick_monitor.hat_step(snapshot),
            )
        return self.status()

    def status(self) -> JoystickToSwitcherBridgeStatus:
        return JoystickToSwitcherBridgeStatus(
            joystick_connected=self.joystick_monitor.health.connected,
            switcher_connected=self.switcher.is_connected(),
            program_source_id=self.state.program_source_id,
            preview_source_id=self.state.preview_source_id,
            active_ptz_camera_id=self.state.active_ptz_camera_id,
            active_ptz_diagnostics=self.ptz_router.diagnostics(),
            last_error=self.state.last_error,
        )

    def log_status(self) -> None:
        status = self.status()
        LOGGER.info(
            "Bridge status: joystick=%s switcher=%s program=%s preview=%s active_ptz=%s moving=%s last_ptz=%s error=%s",
            status.joystick_connected,
            status.switcher_connected,
            status.program_source_id,
            status.preview_source_id,
            status.active_ptz_camera_id,
            status.active_ptz_diagnostics.active_camera_moving if status.active_ptz_diagnostics else None,
            status.active_ptz_diagnostics.active_camera_last_command if status.active_ptz_diagnostics else None,
            status.last_error,
        )
