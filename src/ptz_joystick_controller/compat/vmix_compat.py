from __future__ import annotations

import re

VMIX_INPUT_MIN = 1
VMIX_INPUT_MAX = 100

_INPUT_RE = re.compile(r"^\s*(?:input\s*)?(\d{1,3})\s*$", re.IGNORECASE)


def normalize_vmix_source_id(source_id: str | int | None) -> str | None:
    """Normalize vMix input identifiers to the canonical ``Input N`` form.

    vMix may expose the current active/preview input as a number in the XML API,
    while the controller uses display-stable source IDs such as ``Input 3``.
    Valid vMix sources are ``Input 1`` through ``Input 100``.
    """

    if source_id is None:
        return None
    text = str(source_id).strip()
    if not text:
        return None
    match = _INPUT_RE.match(text)
    if not match:
        return text
    index = int(match.group(1))
    if VMIX_INPUT_MIN <= index <= VMIX_INPUT_MAX:
        return f"Input {index}"
    return text


def vmix_source_to_input_number(source_id: str | int) -> str:
    """Return the vMix API Input parameter for a canonical or numeric source."""

    normalized = normalize_vmix_source_id(source_id)
    if normalized is None:
        return ""
    if normalized.startswith("Input "):
        return normalized.split(" ", 1)[1]
    return normalized
