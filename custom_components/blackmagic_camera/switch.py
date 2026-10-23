"""Switches for the Blackmagic Camera integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    P_AUTOFOCUS,
    P_COLOR_BARS,
    P_DETAIL_SHARPENING,
    P_OIS,
    P_PRERECORD,
    P_PRERECORD_AUTO,
    P_PROGRAM_FEED,
    P_PROXY_RECORDING,
    P_RECORD,
    P_STOP,
    P_STREAM,
    P_STREAM_START,
    P_STREAM_STOP,
    P_TRANSPORT,
    audio_path,
    monitor_path,
)
from .coordinator import BlackmagicCameraConfigEntry
from .entity import BlackmagicCameraEntity
from .values import audio_capabilities, field

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class CameraSwitchDescription(SwitchEntityDescription):
    """Describes a camera switch backed by an ``{"enabled": bool}`` style property."""

    path: str
    state_field: str = "enabled"
    available_fn: Callable[[BlackmagicCameraEntity], bool] | None = None


GLOBAL_SWITCHES: tuple[CameraSwitchDescription, ...] = (
    CameraSwitchDescription(key="color_bars", translation_key="color_bars", path=P_COLOR_BARS),
    CameraSwitchDescription(
        key="program_feed_display",
        translation_key="program_feed_display",
        path=P_PROGRAM_FEED,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
    CameraSwitchDescription(
        key="detail_sharpening",
        translation_key="detail_sharpening",
        path=P_DETAIL_SHARPENING,
        entity_category=EntityCategory.CONFIG,
    ),
    CameraSwitchDescription(
        key="lens_image_stabilization",
        translation_key="lens_image_stabilization",
        path=P_OIS,
        entity_category=EntityCategory.CONFIG,
        available_fn=lambda e: e.prop_field("controlAvailable") is not False,
    ),
    CameraSwitchDescription(
        key="lens_autofocus_enabled",
        translation_key="lens_autofocus_enabled",
        path=P_AUTOFOCUS,
        entity_category=EntityCategory.CONFIG,
        available_fn=lambda e: e.prop_field("supported") is not False,
    ),
    CameraSwitchDescription(
        key="proxy_recording",
        translation_key="proxy_recording",
        path=P_PROXY_RECORDING,
        entity_category=EntityCategory.CONFIG,
    ),
    CameraSwitchDescription(
        key="auto_prerecord",
        translation_key="auto_prerecord",
        path=P_PRERECORD_AUTO,
        state_field="autoEnabled",
        entity_category=EntityCategory.CONFIG,
    ),
)

# Per display monitoring overlays: (feature, translation key, enabled by default)
MONITOR_SWITCHES: tuple[tuple[str, str, bool], ...] = (
    ("displayLUT", "monitor_display_lut", True),
    ("zebra", "monitor_zebra", True),
    ("focusAssist", "monitor_focus_assist", True),
    ("falseColor", "monitor_false_color", True),
    ("cleanFeed", "monitor_clean_feed", False),
    ("frameGuide", "monitor_frame_guide", False),
    ("frameGrids", "monitor_frame_grids", False),
    ("safeArea", "monitor_safe_area", False),
)

# Per channel audio switches: (feature, translation key, capability check)
AUDIO_SWITCHES: tuple[tuple[str, str, Callable[[Any], bool]], ...] = (
    ("phantomPower", "audio_phantom_power", lambda caps: bool(field(caps, "PhantomPower"))),
    (
        "padding",
        "audio_padding",
        lambda caps: (
            bool(field(field(caps, "Padding"), "available"))
            and not field(field(caps, "Padding"), "forced")
        ),
    ),
    ("lowCutFilter", "audio_low_cut", lambda caps: bool(field(caps, "LowCutFilter"))),
)

RECORD_SWITCH = SwitchEntityDescription(key="record", translation_key="record")
STREAM_SWITCH = SwitchEntityDescription(
    key="stream", translation_key="stream", entity_registry_enabled_default=False
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlackmagicCameraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up camera switches."""
    coordinator = entry.runtime_data
    caps = coordinator.capabilities
    entities: list[SwitchEntity] = []

    if caps.supports(P_RECORD):
        entities.append(RecordSwitch(coordinator, RECORD_SWITCH, P_RECORD))
    if caps.supports(P_STREAM):
        entities.append(StreamSwitch(coordinator, STREAM_SWITCH, P_STREAM))

    entities.extend(
        CameraSwitch(coordinator, description, description.path)
        for description in GLOBAL_SWITCHES
        if caps.supports(description.path)
    )

    for display in caps.displays:
        for feature, translation_key, enabled in MONITOR_SWITCHES:
            path = monitor_path(display, feature)
            if not caps.supports(path):
                continue
            description = CameraSwitchDescription(
                key=f"monitor_{feature}",
                translation_key=translation_key,
                path=path,
                entity_category=EntityCategory.CONFIG,
                entity_registry_enabled_default=enabled,
            )
            entities.append(
                CameraSwitch(
                    coordinator,
                    description,
                    path,
                    unique_suffix=display,
                    placeholders={"display": display},
                )
            )

    for channel in range(caps.audio_channels):
        description_path = audio_path(channel, "input/description")
        for feature, translation_key, check in AUDIO_SWITCHES:
            path = audio_path(channel, feature)
            if not caps.supports(path):
                continue
            description = CameraSwitchDescription(
                key=f"audio_{feature}",
                translation_key=translation_key,
                path=path,
                entity_category=EntityCategory.CONFIG,
                available_fn=_audio_capability(description_path, check),
            )
            entities.append(
                CameraSwitch(
                    coordinator,
                    description,
                    path,
                    unique_suffix=str(channel),
                    placeholders={"channel": str(channel + 1)},
                )
            )

    async_add_entities(entities)


def _audio_capability(
    description_path: str, check: Callable[[Any], bool]
) -> Callable[[BlackmagicCameraEntity], bool]:
    """Return an availability check based on the input capabilities."""

    def _available(entity: BlackmagicCameraEntity) -> bool:
        description = entity.prop_field("description", description_path)
        if description is None:
            return True
        return check(audio_capabilities(description))

    return _available


class CameraSwitch(BlackmagicCameraEntity, SwitchEntity):
    """A switch backed by a boolean field of a property."""

    entity_description: CameraSwitchDescription

    @property
    def available(self) -> bool:
        """Return True if the switch can be used."""
        if not super().available:
            return False
        check = self.entity_description.available_fn
        return check is None or check(self)

    @property
    def is_on(self) -> bool | None:
        """Return the switch state."""
        value = self.prop_field(self.entity_description.state_field)
        return value if isinstance(value, bool) else None

    async def _async_set(self, state: bool) -> None:
        await self.coordinator.async_command(
            "put", self.entity_description.path, {self.entity_description.state_field: state}
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the feature on."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the feature off."""
        await self._async_set(False)


class RecordSwitch(BlackmagicCameraEntity, SwitchEntity):
    """Start and stop recording."""

    @property
    def is_on(self) -> bool | None:
        """Return True while recording."""
        value = self.prop_field("recording")
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start recording."""
        await self.coordinator.async_command(
            "post",
            P_RECORD,
            fallback=("put", P_RECORD, {"recording": True}),
            refresh=(P_RECORD, P_TRANSPORT, P_PRERECORD),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop recording."""
        await self.coordinator.async_command(
            "post",
            P_STOP,
            fallback=("put", P_RECORD, {"recording": False}),
            refresh=(P_RECORD, P_TRANSPORT, P_PRERECORD),
        )


class StreamSwitch(BlackmagicCameraEntity, SwitchEntity):
    """Start and stop the livestream."""

    @property
    def is_on(self) -> bool | None:
        """Return True while the livestream is active."""
        status = self.prop_field("status")
        if not isinstance(status, str):
            return None
        return status != "Idle"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the livestream."""
        await self.coordinator.async_command("put", P_STREAM_START, refresh=(P_STREAM,))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the livestream."""
        await self.coordinator.async_command("put", P_STREAM_STOP, refresh=(P_STREAM,))
