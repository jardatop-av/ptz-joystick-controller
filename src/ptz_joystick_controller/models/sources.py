from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .source_types import SourceType


class SourceCapability(StrEnum):
    PROGRAM = "program"
    PREVIEW = "preview"
    TALLY = "tally"
    PTZ_MAPPING = "ptz_mapping"


class Source(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    type: SourceType = SourceType.UNKNOWN
    native_id: str | None = None
    supports_preview: bool = True
    supports_tally: bool = True
    capabilities: frozenset[SourceCapability] = frozenset(
        {SourceCapability.PROGRAM, SourceCapability.PREVIEW, SourceCapability.TALLY}
    )

    @field_validator("id", "display_name", "native_id", mode="before")
    @classmethod
    def strip_strings(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    def supports(self, capability: SourceCapability) -> bool:
        if capability == SourceCapability.PREVIEW:
            return self.supports_preview and capability in self.capabilities
        if capability == SourceCapability.TALLY:
            return self.supports_tally and capability in self.capabilities
        return capability in self.capabilities


class SourceMapping(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str = Field(min_length=1)
    display_name: str | None = None
    ptz_camera_id: str | None = None

    @field_validator("source_id", "display_name", "ptz_camera_id", mode="before")
    @classmethod
    def strip_optional_strings(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class SourceMap(BaseModel):
    model_config = ConfigDict(frozen=True)

    available_mode: str = "auto"
    mappings: tuple[SourceMapping, ...] = ()

    @field_validator("mappings")
    @classmethod
    def require_unique_source_ids(cls, mappings: tuple[SourceMapping, ...]) -> tuple[SourceMapping, ...]:
        seen: set[str] = set()
        for mapping in mappings:
            if mapping.source_id in seen:
                raise ValueError(f"Duplicate source_id: {mapping.source_id}")
            seen.add(mapping.source_id)
        return mappings

    def source_ids(self) -> set[str]:
        return {mapping.source_id for mapping in self.mappings}

    def has_source(self, source_id: str) -> bool:
        return source_id in self.source_ids()

    def mapping_for_source(self, source_id: str) -> SourceMapping | None:
        for mapping in self.mappings:
            if mapping.source_id == source_id:
                return mapping
        return None

    def camera_for_source(self, source_id: str) -> str | None:
        mapping = self.mapping_for_source(source_id)
        return mapping.ptz_camera_id if mapping else None


class UnsupportedSourceError(ValueError):
    def __init__(self, source_id: str) -> None:
        super().__init__(f"Unsupported or unmapped source_id: {source_id}")
        self.source_id = source_id


class SourceSelector:
    def __init__(self, source_map: SourceMap) -> None:
        self.source_map = source_map

    def require_supported_preview_source(self, source_id: str) -> SourceMapping:
        mapping = self.source_map.mapping_for_source(source_id)
        if mapping is None:
            raise UnsupportedSourceError(source_id)
        return mapping

    def active_ptz_for_preview(self, preview_source_id: str | None) -> str | None:
        if preview_source_id is None:
            return None
        return self.source_map.camera_for_source(preview_source_id)
