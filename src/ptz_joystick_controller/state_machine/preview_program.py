from __future__ import annotations

import logging
from dataclasses import dataclass

from ..app_state import AppState
from ..event_bus import EventBus
from ..models.commands import EventType
from ..models.sources import UnsupportedSourceError
from .ptz_control import PtzControlStateMachine

LOGGER = logging.getLogger(__name__)


@dataclass
class PreviewProgramStateMachine:
    state: AppState
    event_bus: EventBus
    ptz_control: PtzControlStateMachine | None = None

    def _ptz(self) -> PtzControlStateMachine:
        if self.ptz_control is None:
            self.ptz_control = PtzControlStateMachine(self.state, self.event_bus)
        return self.ptz_control

    def set_preview(self, source_id: str | None) -> None:
        normalized_source_id = source_id
        if source_id is not None:
            try:
                normalized_source_id = self.state.require_supported_source(source_id)
            except UnsupportedSourceError:
                LOGGER.warning("Unsupported source ignored: %s", source_id)
                self.event_bus.publish(EventType.UNSUPPORTED_SOURCE, {"source_id": source_id})
                raise
        previous = self.state.preview_source_id
        self.state.preview_source_id = normalized_source_id
        active_ptz = self._ptz().recompute_active_ptz()
        self.event_bus.publish(
            EventType.PREVIEW_CHANGED,
            {"old_source_id": previous, "source_id": normalized_source_id, "active_ptz_camera_id": active_ptz},
        )

    def set_program(self, source_id: str | None) -> None:
        normalized_source_id = source_id
        if source_id is not None:
            try:
                normalized_source_id = self.state.require_supported_source(source_id)
            except UnsupportedSourceError:
                LOGGER.warning("Unsupported source ignored: %s", source_id)
                self.event_bus.publish(EventType.UNSUPPORTED_SOURCE, {"source_id": source_id})
                raise
        previous = self.state.program_source_id
        self.state.program_source_id = normalized_source_id
        self.event_bus.publish(EventType.PROGRAM_CHANGED, {"old_source_id": previous, "source_id": normalized_source_id})

    def copy_program_to_preview(self) -> None:
        self.set_preview(self.state.program_source_id)
        self.event_bus.publish("preview.copy_program", {"source_id": self.state.program_source_id})

    def cut(self) -> None:
        self._perform_transition("cut")

    def auto(self) -> None:
        self._perform_transition("auto")

    def _perform_transition(self, transition_type: str) -> None:
        self.event_bus.publish(EventType.TRANSITION_STARTED, {"transition_type": transition_type})
        if self.state.config.ptz.stop_on_switch:
            self._ptz().request_stop(f"before_{transition_type}")

        old_program = self.state.program_source_id
        old_preview = self.state.preview_source_id
        self.state.program_source_id = old_preview
        self.state.preview_source_id = old_program
        active_ptz = self._ptz().recompute_active_ptz()

        self.event_bus.publish(EventType.PROGRAM_CHANGED, {"old_source_id": old_program, "source_id": old_preview})
        self.event_bus.publish(
            EventType.PREVIEW_CHANGED,
            {"old_source_id": old_preview, "source_id": old_program, "active_ptz_camera_id": active_ptz},
        )
        self.event_bus.publish(
            EventType.TRANSITION_COMPLETED,
            {
                "transition_type": transition_type,
                "program_source_id": self.state.program_source_id,
                "preview_source_id": self.state.preview_source_id,
                "active_ptz_camera_id": active_ptz,
            },
        )
