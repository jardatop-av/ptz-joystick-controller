from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, ConfigDict


class TallyState(StrEnum):
    OFF = "off"
    PREVIEW = "preview"
    PROGRAM = "program"


class SourceTally(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    state: TallyState = TallyState.OFF
