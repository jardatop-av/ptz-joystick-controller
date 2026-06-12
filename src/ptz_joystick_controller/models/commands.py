from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


class CommandType(StrEnum):
    NOOP = "noop"
    SET_PREVIEW_SOURCE = "set_preview_source"
    CUT = "cut"
    AUTO = "auto"
    COPY_PROGRAM_TO_PREVIEW = "copy_program_to_preview"
    PTZ_STOP = "ptz_stop"
    PTZ_PRESET_RECALL = "ptz_preset_recall"


class EventType(StrEnum):
    COMMAND_DISPATCHED = "command.dispatched"
    PREVIEW_CHANGED = "preview.changed"
    PROGRAM_CHANGED = "program.changed"
    TRANSITION_STARTED = "transition.started"
    TRANSITION_COMPLETED = "transition.completed"
    PTZ_STOP_REQUESTED = "ptz.stop_requested"
    ACTIVE_PTZ_CHANGED = "ptz.active_changed"
    UNSUPPORTED_SOURCE = "source.unsupported"


@dataclass(frozen=True)
class Command:
    type: CommandType
    source_id: str | None = None
    reason: str | None = None
    preset_number: int | None = None
    origin: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CommandError(ValueError):
    """Raised when an internal command cannot be executed."""
