from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..constants import SUPPORTED_SWITCHERS


class SwitcherType(StrEnum):
    VMIX = "vmix"
    ATEM_MINI_PRO = "atem_mini_pro"
    ATEM_TV_STUDIO_PRO_4K = "atem_tv_studio_pro_4k"
    OSEE_GOSTREAM_DECK = "osee_gostream_deck"
    OSEE_GOSTREAM_DUET = "osee_gostream_duet"


class SwitcherCapability(StrEnum):
    READ_PROGRAM = "read_program"
    READ_PREVIEW = "read_preview"
    SET_PREVIEW = "set_preview"
    CUT = "cut"
    AUTO = "auto"
    COPY_PROGRAM_TO_PREVIEW = "copy_program_to_preview"
    TALLY = "tally"


class SwitcherConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class SwitcherCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    capabilities: frozenset[SwitcherCapability] = frozenset(
        {
            SwitcherCapability.READ_PROGRAM,
            SwitcherCapability.READ_PREVIEW,
            SwitcherCapability.SET_PREVIEW,
            SwitcherCapability.CUT,
            SwitcherCapability.AUTO,
            SwitcherCapability.COPY_PROGRAM_TO_PREVIEW,
            SwitcherCapability.TALLY,
        }
    )

    def supports(self, capability: SwitcherCapability) -> bool:
        return capability in self.capabilities


class SwitcherStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    state: SwitcherConnectionState = SwitcherConnectionState.DISCONNECTED
    message: str | None = None


class ReconnectConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    interval_seconds: int = Field(default=3, ge=1)
    max_backoff_seconds: int = Field(default=30, ge=1)


class SwitcherConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    reconnect: ReconnectConfig = Field(default_factory=ReconnectConfig)

    @field_validator("type")
    @classmethod
    def validate_switcher_type(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in SUPPORTED_SWITCHERS:
            raise ValueError(f"Unsupported switcher.type: {value}")
        return normalized
