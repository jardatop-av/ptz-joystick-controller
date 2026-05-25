from __future__ import annotations

import logging
from dataclasses import dataclass

from ..app_state import AppState
from ..event_bus import EventBus
from ..models.commands import Command, CommandError, CommandType, EventType
from ..models.sources import UnsupportedSourceError
from ..state_machine.preview_program import PreviewProgramStateMachine
from ..state_machine.ptz_control import PtzControlStateMachine
from ..switchers.base import AbstractSwitcher

LOGGER = logging.getLogger(__name__)


@dataclass
class SwitcherCommandExecutor:
    """Execute internal commands against a switcher backend and mirror state locally.

    This class intentionally depends only on the switcher abstraction. It can run
    with a real vMix backend, a FakeSwitcher, or any future backend implementing
    AbstractSwitcher.
    """

    switcher: AbstractSwitcher
    state: AppState
    event_bus: EventBus
    preview_program: PreviewProgramStateMachine
    ptz_control: PtzControlStateMachine
    dry_run: bool = False

    def sync_from_switcher(self) -> None:
        """Refresh local program/preview state from the current switcher object.

        Network failures are handled by the backend. This method never raises for
        a disconnected switcher; it only mirrors whatever state is available.
        """

        try:
            if hasattr(self.switcher, "poll"):
                getattr(self.switcher, "poll")()
        except Exception as exc:  # keep bridge safe when vMix is unavailable
            self.state.switcher_connected = False
            self.state.last_error = str(exc)
            LOGGER.info("Switcher sync failed safely: %s", exc)
            self.event_bus.publish("switcher.sync_failed", {"error": str(exc)})
            return

        self.state.switcher_connected = self.switcher.is_connected()
        program = self.switcher.get_program_source()
        preview = self.switcher.get_preview_source()
        if program != self.state.program_source_id:
            try:
                self.preview_program.set_program(program)
            except UnsupportedSourceError as exc:
                self.state.last_error = str(exc)
                LOGGER.warning("Switcher reported unsupported program source %s; keeping previous state", exc.source_id)
        if preview != self.state.preview_source_id:
            try:
                self.preview_program.set_preview(preview)
            except UnsupportedSourceError as exc:
                self.state.last_error = str(exc)
                LOGGER.warning("Switcher reported unsupported preview source %s; keeping previous state", exc.source_id)
        self.event_bus.publish(
            "switcher.synced",
            {
                "program_source_id": self.state.program_source_id,
                "preview_source_id": self.state.preview_source_id,
                "active_ptz_camera_id": self.state.active_ptz_camera_id,
                "connected": self.state.switcher_connected,
            },
        )

    def ensure_connected(self) -> None:
        if self.switcher.is_connected():
            self.state.switcher_connected = True
            return
        try:
            LOGGER.info("Switcher reconnect attempt")
            self.switcher.reconnect()
            self.state.switcher_connected = self.switcher.is_connected()
            self.event_bus.publish("switcher.reconnect", {"connected": self.state.switcher_connected})
        except Exception as exc:
            self.state.switcher_connected = False
            self.state.last_error = str(exc)
            LOGGER.info("Switcher reconnect failed safely: %s", exc)
            self.event_bus.publish("switcher.reconnect_failed", {"error": str(exc)})

    def execute(self, command: Command) -> bool:
        LOGGER.info("Action execute: %s source=%s origin=%s", command.type.value, command.source_id, command.origin)
        self.event_bus.publish(EventType.COMMAND_DISPATCHED, {"command": command})

        if command.type == CommandType.NOOP:
            return True

        if self.dry_run:
            LOGGER.info("Dry-run action skipped switcher command: %s", command.type.value)
            self.event_bus.publish("command.dry_run", {"command": command})
            return True

        self.ensure_connected()
        try:
            if command.type == CommandType.SET_PREVIEW_SOURCE:
                if not command.source_id:
                    raise CommandError("SET_PREVIEW_SOURCE requires source_id")
                self.switcher.set_preview_source(command.source_id)
                try:
                    self.preview_program.set_preview(self.switcher.get_preview_source() or command.source_id)
                except UnsupportedSourceError as exc:
                    self.state.last_error = str(exc)
                    LOGGER.warning("Preview source command completed but source is unsupported locally: %s", exc.source_id)
                return True

            if command.type == CommandType.COPY_PROGRAM_TO_PREVIEW:
                self.switcher.copy_program_to_preview()
                self.sync_from_switcher()
                return True

            if command.type == CommandType.CUT:
                if self.state.config.ptz.stop_on_switch:
                    self.ptz_control.request_stop("before_cut")
                self.switcher.cut()
                self.sync_from_switcher()
                return True

            if command.type == CommandType.AUTO:
                if self.state.config.ptz.stop_on_switch:
                    self.ptz_control.request_stop("before_auto")
                self.switcher.auto()
                self.sync_from_switcher()
                return True

            if command.type == CommandType.PTZ_STOP:
                self.ptz_control.request_stop(command.reason or "command")
                return True

            raise CommandError(f"Unsupported command type: {command.type}")
        except Exception as exc:
            self.state.last_error = str(exc)
            LOGGER.info("Action failed safely: %s", exc)
            self.event_bus.publish("command.failed", {"command": command, "error": str(exc)})
            return False
