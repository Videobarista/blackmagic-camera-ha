"""Feature detection for Blackmagic cameras.

Different camera models (PYXIS, Cinema Camera 6K, URSA Broadcast G2, Studio
Cameras, ...) implement different parts of the REST API. Instead of keeping a
model table, every property is probed once: a property that answers with
HTTP 404/501 simply does not get an entity.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import logging
import re
from typing import Any

from .api import BlackmagicCameraApi, CannotConnect, InvalidAuth, gather_limited
from .const import (
    AUDIO_FEATURES,
    EVENT_STILL_CAPTURED,
    GLOBAL_PATHS,
    MAX_PARALLEL_REQUESTS,
    MAX_SUB_DEVICES,
    MONITOR_BRIGHTNESS,
    MONITOR_FEATURES,
    P_AUDIO_CHANNELS,
    P_DISPLAYS,
    P_EVENT_LIST,
    P_PANEL_DEVICES,
    P_RECORD,
    P_WORKINGSET,
    audio_path,
    monitor_path,
)

_LOGGER = logging.getLogger(__name__)

# Names the camera reports (displays, panel buttons) end up in URL paths, so
# only plain names are accepted: no "/", "..", "?", "%" or control characters.
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}")


def safe_names(values: Iterable[Any]) -> list[str]:
    """Return the plain, unique names among values (at most MAX_SUB_DEVICES)."""
    names: list[str] = []
    for value in values:
        name = str(value) if isinstance(value, (str, int)) else ""
        if not _SAFE_NAME.fullmatch(name):
            _LOGGER.debug("Ignoring unexpected name from the camera: %r", name[:64])
            continue
        if name not in names:
            names.append(name)
    return names[:MAX_SUB_DEVICES]


def bounded_count(value: Any) -> int:
    """Return a count reported by the camera, limited to 0..MAX_SUB_DEVICES."""
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return max(0, min(value, MAX_SUB_DEVICES))


@dataclass
class CameraCapabilities:
    """What a specific camera supports."""

    product: dict[str, Any] = field(default_factory=dict)
    supported: set[str] = field(default_factory=set)
    displays: list[str] = field(default_factory=list)
    audio_channels: int = 0
    media_slots: int = 0
    still_capture: bool = False
    panel_buttons: list[str] = field(default_factory=list)
    # Properties the notification websocket can push (from /event/list).
    events: list[str] = field(default_factory=list)

    @classmethod
    def minimal(cls) -> CameraCapabilities:
        """Return capabilities for a camera that has never been reached."""
        return cls(supported={P_RECORD})

    def supports(self, path: str) -> bool:
        """Return True if the camera supports a property path."""
        return path in self.supported

    def as_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "product": self.product,
            "supported": sorted(self.supported),
            "displays": self.displays,
            "audio_channels": self.audio_channels,
            "media_slots": self.media_slots,
            "still_capture": self.still_capture,
            "panel_buttons": self.panel_buttons,
            "events": self.events,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CameraCapabilities:
        """Deserialize from storage."""
        return cls(
            product=dict(data.get("product") or {}),
            supported=set(data.get("supported") or ()),
            displays=safe_names(data.get("displays") or ()),
            audio_channels=bounded_count(data.get("audio_channels")),
            media_slots=bounded_count(data.get("media_slots")),
            still_capture=bool(data.get("still_capture")),
            panel_buttons=safe_names(data.get("panel_buttons") or ()),
            events=[str(e) for e in data.get("events") or ()],
        )

    def same_features(self, other: CameraCapabilities) -> bool:
        """Return True if both describe the same set of entities."""
        return (
            self.supported == other.supported
            and self.displays == other.displays
            and self.audio_channels == other.audio_channels
            and self.media_slots == other.media_slots
            and self.still_capture == other.still_capture
            and self.panel_buttons == other.panel_buttons
        )

    def state_paths(self) -> list[str]:
        """Return all supported property paths that hold state."""
        return sorted(self.supported)


async def _probe_many(api: BlackmagicCameraApi, paths: list[str]) -> dict[str, tuple[bool, Any]]:
    """Probe a list of paths, re-raising connection and auth errors."""
    results = await gather_limited(MAX_PARALLEL_REQUESTS, *(api.probe(p) for p in paths))
    probed: dict[str, tuple[bool, Any]] = {}
    for path, result in zip(paths, results, strict=True):
        if isinstance(result, (CannotConnect, InvalidAuth)):
            raise result
        if isinstance(result, BaseException):
            raise CannotConnect(f"Unexpected error probing {path}: {result!r}") from result
        probed[path] = result
    return probed


def _event_names(value: Any) -> list[str]:
    """Return the event names of /event/list (a list, or {"events": [...]})."""
    events: Any = value
    if isinstance(value, dict):
        events = value.get("events") or []
    if not isinstance(events, list):
        return []
    return [str(event) for event in events if isinstance(event, str)]


async def async_probe_capabilities(
    api: BlackmagicCameraApi,
) -> tuple[CameraCapabilities, dict[str, Any]]:
    """Detect camera features and return them with the initial state."""
    product = await api.get_product()
    caps = CameraCapabilities(product=product)
    data: dict[str, Any] = {}

    probed = await _probe_many(
        api, [*GLOBAL_PATHS, P_DISPLAYS, P_AUDIO_CHANNELS, P_EVENT_LIST, P_PANEL_DEVICES]
    )

    for path in GLOBAL_PATHS:
        supported, value = probed[path]
        if supported:
            caps.supported.add(path)
            data[path] = value

    # Still capture has no GET endpoint; the event list tells us whether the
    # firmware knows about it. If the event list itself is missing, offer the
    # button anyway and let the camera answer.
    events_supported, events = probed[P_EVENT_LIST]
    caps.events = _event_names(events) if events_supported else []
    caps.still_capture = EVENT_STILL_CAPTURED in caps.events if events_supported else True

    workingset = data.get(P_WORKINGSET)
    if isinstance(workingset, dict):
        slots = workingset.get("workingset")
        size = workingset.get("size")
        caps.media_slots = bounded_count(size)
        if isinstance(slots, list):
            caps.media_slots = max(caps.media_slots, bounded_count(len(slots)))

    displays_supported, displays = probed[P_DISPLAYS]
    if displays_supported and isinstance(displays, dict):
        caps.displays = safe_names(displays.get("displays") or [])

    panel_supported, panel = probed[P_PANEL_DEVICES]
    if panel_supported and isinstance(panel, list):
        caps.panel_buttons = safe_names(
            item.get("name")
            for item in panel
            if isinstance(item, dict) and item.get("type", "button") == "button"
        )

    channels_supported, channels = probed[P_AUDIO_CHANNELS]
    if channels_supported and isinstance(channels, dict):
        count = channels.get("channels")
        caps.audio_channels = bounded_count(count)

    sub_paths = [
        monitor_path(display, feature)
        for display in caps.displays
        for feature in (*MONITOR_FEATURES, MONITOR_BRIGHTNESS)
    ] + [
        audio_path(channel, feature)
        for channel in range(caps.audio_channels)
        for feature in AUDIO_FEATURES
    ]
    if sub_paths:
        for path, (supported, value) in (await _probe_many(api, sub_paths)).items():
            if supported:
                caps.supported.add(path)
                data[path] = value

    _LOGGER.debug(
        "Detected %s (%s): %d properties, displays %s, %d audio channels",
        product.get("productName"),
        product.get("softwareVersion"),
        len(caps.supported),
        caps.displays,
        caps.audio_channels,
    )
    return caps, data
