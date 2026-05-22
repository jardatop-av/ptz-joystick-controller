from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    CAMERA = "camera"
    MEDIA_PLAYER = "media_player"
    STILL = "still"
    BLACK = "black"
    AUX = "aux"
    INTERNAL = "internal"
    UNKNOWN = "unknown"
