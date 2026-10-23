"""Feature detection for Blackmagic cameras.

Different camera models (PYXIS, Cinema Camera 6K, URSA Broadcast G2, Studio
Cameras, ...) implement different parts of the REST API. Instead of keeping a
model table, every property is probed once: a property that answers with
HTTP 404/501 simply does not get an entity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from .api import BlackmagicCameraApi, CannotConnect, InvalidAuth, gather_limited
from .const import (
    AUDIO_FEATURES,
    EVENT_STILL_CAPTURED,
    GLOBAL_PATHS,
    MAX_PARALLEL_REQUESTS,
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
            displays=[str(d) for d in data.get("displays") or ()],
            audio_channels=int(data.get("audio_channels") or 0),
            media_slots=int(data.get("media_slots") or 0),
            still_capture=bool(data.get("still_capture")),
            panel_buttons=[str(b) for b in data.get("panel_buttons") or ()],
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
        caps.media_slots = int(size) if isinstance(size, int) else 0
        if isinstance(slots, list):
            caps.media_slots = max(caps.media_slots, len(slots))

    displays_supported, displays = probed[P_DISPLAYS]
    if displays_supported and isinstance(displays, dict):
        caps.displays = [str(d) for d in displays.get("displays") or [] if d]

    panel_supported, panel = probed[P_PANEL_DEVICES]
    if panel_supported and isinstance(panel, list):
        caps.panel_buttons = [
            str(item["name"])
            for item in panel
            if isinstance(item, dict)
            and item.get("name")
            and item.get("type", "button") == "button"
        ]

    channels_supported, channels = probed[P_AUDIO_CHANNELS]
    if channels_supported and isinstance(channels, dict):
        count = channels.get("channels")
        caps.audio_channels = int(count) if isinstance(count, int) else 0

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
