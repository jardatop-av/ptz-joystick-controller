from __future__ import annotations

from pathlib import Path

from .app_state import AppState
from .config import ControllerConfig, load_config
from .event_bus import EventBus
from .state_machine.preview_program import PreviewProgramStateMachine
from .state_machine.ptz_control import PtzControlStateMachine
from .state_machine.transitions import TransitionCoordinator


class Application:
    def __init__(self, config: ControllerConfig) -> None:
        self.event_bus = EventBus()
        self.state = AppState(config=config)
        self.ptz_control = PtzControlStateMachine(self.state, self.event_bus)
        self.preview_program = PreviewProgramStateMachine(self.state, self.event_bus, self.ptz_control)
        self.transitions = TransitionCoordinator(self.preview_program, self.ptz_control)

    def start(self) -> None:
        self.event_bus.publish("app.started", {"device_name": self.state.config.app.device_name})


def create_application(config_path: str | Path) -> Application:
    return Application(load_config(config_path))


def main() -> int:
    # Hardware-free startup path for stage-1 validation.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
