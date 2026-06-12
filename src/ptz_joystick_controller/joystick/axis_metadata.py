from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..models.joystick import AxisInvertConfig


DEFAULT_AXIS_INVERSION_LABELS: dict[str, str] = {
    "pan": "Reverse pan",
    "tilt": "Reverse tilt",
    "zoom": "Reverse zoom",
}


@dataclass(frozen=True)
class AxisInversionMetadata:
    axis_id: str
    label: str
    inverted: bool


class AxisInversionMetadataRegistry:
    """Human-readable axis inversion metadata for debug tools and future GUI."""

    def __init__(self, invert: AxisInvertConfig, labels: Mapping[str, str] | None = None) -> None:
        merged = dict(DEFAULT_AXIS_INVERSION_LABELS)
        if labels:
            merged.update({str(key): str(value) for key, value in labels.items()})
        self._invert = invert
        self._labels = merged

    def label_for(self, axis_id: str) -> str:
        return self._labels.get(axis_id, axis_id)

    def metadata_for(self, axis_id: str) -> AxisInversionMetadata:
        return AxisInversionMetadata(
            axis_id=axis_id,
            label=self.label_for(axis_id),
            inverted=bool(getattr(self._invert, axis_id, False)),
        )

    def all_metadata(self) -> dict[str, AxisInversionMetadata]:
        return {axis_id: self.metadata_for(axis_id) for axis_id in ("pan", "tilt", "zoom")}
