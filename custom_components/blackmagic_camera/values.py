"""Helpers to read and format camera property values."""

from __future__ import annotations

import re
from typing import Any


def field(value: Any, key: str) -> Any:
    """Return a field of a property object, or None."""
    if isinstance(value, dict):
        return value.get(key)
    return None


def number(value: Any) -> float | None:
    """Return value when it is numeric (ints stay ints), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def normalized(value: Any) -> float | None:
    """Return the normalized (0..1) lens value, accepting both spellings."""
    if not isinstance(value, dict):
        return None
    return number(value.get("normalized", value.get("normalised")))


def normalized_key(value: Any) -> str:
    """Return the spelling of the normalized field this firmware uses."""
    if isinstance(value, dict) and "normalised" in value and "normalized" not in value:
        return "normalised"
    return "normalized"


def snake(value: str) -> str:
    """Convert an API enum value ("OneShot", "Extended Video") to snake_case."""
    value = re.sub(r"[^0-9A-Za-z]+", "_", value.strip())
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return value.strip("_").lower()


def audio_capabilities(description: Any) -> dict[str, Any]:
    """Return the capabilities of an audio input description.

    Firmware 10.x reports them as a one element list, the documentation as an
    object; both are accepted.
    """
    capabilities = field(description, "capabilities")
    if isinstance(capabilities, list):
        merged: dict[str, Any] = {}
        for item in capabilities:
            if isinstance(item, dict):
                merged.update(item)
        return merged
    return capabilities if isinstance(capabilities, dict) else {}


def fmt_number(value: float) -> str:
    """Format a number without a trailing .0."""
    return f"{value:g}"


# ---------------------------------------------------------------------------
# Shutter
# ---------------------------------------------------------------------------
def shutter_angle_scaled(angles: list[Any]) -> bool:
    """Return True if the firmware reports shutter angles multiplied by 100."""
    numeric = [a for a in angles if number(a) is not None]
    return bool(numeric) and max(numeric) > 360


def angle_to_degrees(value: float, scaled: bool) -> float:
    """Convert a reported shutter angle to degrees."""
    return value / 100 if scaled else value


def fmt_angle(degrees: float) -> str:
    """Format a shutter angle option."""
    return f"{round(degrees, 2):g}°"


def fmt_speed(speed: float) -> str:
    """Format a shutter speed option."""
    return f"1/{int(speed)}"


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------
def workingset_slots(value: Any) -> list[dict[str, Any] | None]:
    """Return the working set slots of /media/workingset."""
    slots = field(value, "workingset")
    if not isinstance(slots, list):
        return []
    return [slot if isinstance(slot, dict) else None for slot in slots]


def active_slot(workingset: Any, active: Any) -> dict[str, Any] | None:
    """Return the working set slot that is currently recorded to."""
    slots = workingset_slots(workingset)
    index = field(active, "workingsetIndex")
    if isinstance(index, int) and 0 <= index < len(slots) and slots[index] is not None:
        return slots[index]
    for slot in slots:
        if slot is not None and slot.get("activeDisk"):
            return slot
    return None


def slot_label(index: int, slot: dict[str, Any]) -> str:
    """Return a human readable label for a working set slot."""
    volume = slot.get("volume") or slot.get("deviceName") or "?"
    return f"{index + 1}: {volume}"
