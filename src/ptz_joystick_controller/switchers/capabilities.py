from __future__ import annotations

from ..models.source_types import SourceType
from ..models.sources import Source, SourceCapability
from ..models.switcher import SwitcherCapabilities, SwitcherCapability, SwitcherType

DEFAULT_SWITCHER_CAPABILITIES = SwitcherCapabilities(
    capabilities=frozenset(
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
)


def _source(source_id: str, display_name: str, source_type: SourceType) -> Source:
    return Source(
        id=source_id,
        display_name=display_name,
        type=source_type,
        native_id=source_id,
        supports_preview=True,
        supports_tally=True,
        capabilities=frozenset(
            {
                SourceCapability.PROGRAM,
                SourceCapability.PREVIEW,
                SourceCapability.TALLY,
            }
        ),
    )


def _camera_sources(count: int) -> tuple[Source, ...]:
    return tuple(_source(f"CH{index}", f"CH{index}", SourceType.CAMERA) for index in range(1, count + 1))


def _vmix_sources(count: int = 100) -> tuple[Source, ...]:
    return tuple(
        _source(f"Input {index}", f"Input {index}", SourceType.CAMERA)
        for index in range(1, count + 1)
    )


AVAILABLE_SOURCES_BY_SWITCHER: dict[SwitcherType, tuple[Source, ...]] = {
    SwitcherType.OSEE_GOSTREAM_DECK: (
        *_camera_sources(4),
        _source("AUX", "AUX", SourceType.AUX),
        _source("STILL1", "STILL1", SourceType.STILL),
        _source("STILL2", "STILL2", SourceType.STILL),
        _source("BLACK", "BLACK", SourceType.BLACK),
    ),
    SwitcherType.OSEE_GOSTREAM_DUET: (
        *_camera_sources(8),
        _source("MP1", "MP1", SourceType.MEDIA_PLAYER),
        _source("MP2", "MP2", SourceType.MEDIA_PLAYER),
        _source("M/SRC", "M/SRC", SourceType.INTERNAL),
    ),
    SwitcherType.ATEM_MINI_PRO: _camera_sources(4),
    SwitcherType.ATEM_TV_STUDIO_PRO_4K: _camera_sources(10),
    SwitcherType.VMIX: _vmix_sources(100),
}


def get_switcher_capabilities(switcher_type: SwitcherType | str) -> SwitcherCapabilities:
    normalized = SwitcherType(str(switcher_type))
    if normalized not in AVAILABLE_SOURCES_BY_SWITCHER:
        raise ValueError(f"Unsupported switcher type: {switcher_type}")
    return DEFAULT_SWITCHER_CAPABILITIES


def get_available_sources(switcher_type: SwitcherType | str) -> tuple[Source, ...]:
    normalized = SwitcherType(str(switcher_type))
    try:
        return AVAILABLE_SOURCES_BY_SWITCHER[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported switcher type: {switcher_type}") from exc


def get_source_ids(switcher_type: SwitcherType | str) -> tuple[str, ...]:
    return tuple(source.id for source in get_available_sources(switcher_type))
