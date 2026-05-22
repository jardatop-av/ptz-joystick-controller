from __future__ import annotations

from dataclasses import dataclass

from ..models.commands import Command, CommandError, CommandType
from .preview_program import PreviewProgramStateMachine
from .ptz_control import PtzControlStateMachine


@dataclass
class TransitionCoordinator:
    preview_program: PreviewProgramStateMachine
    ptz_control: PtzControlStateMachine

    def before_cut_or_auto(self, transition_type: str) -> None:
        self.ptz_control.request_stop(f"before_{transition_type}")

    def after_preview_program_refresh(self) -> None:
        self.ptz_control.recompute_active_ptz()


@dataclass
class CommandDispatcher:
    preview_program: PreviewProgramStateMachine
    ptz_control: PtzControlStateMachine

    def dispatch(self, command: Command) -> None:
        if command.type == CommandType.NOOP:
            return
        if command.type == CommandType.SET_PREVIEW_SOURCE:
            if not command.source_id:
                raise CommandError("SET_PREVIEW_SOURCE requires source_id")
            self.preview_program.set_preview(command.source_id)
            return
        if command.type == CommandType.COPY_PROGRAM_TO_PREVIEW:
            self.preview_program.copy_program_to_preview()
            return
        if command.type == CommandType.CUT:
            self.preview_program.cut()
            return
        if command.type == CommandType.AUTO:
            self.preview_program.auto()
            return
        if command.type == CommandType.PTZ_STOP:
            self.ptz_control.request_stop(command.reason or "command")
            return
        raise CommandError(f"Unsupported command type: {command.type}")
