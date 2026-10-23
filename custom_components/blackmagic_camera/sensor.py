"""Sensors for the Blackmagic Camera integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    P_AUTOFOCUS,
    P_CODEC_FORMAT,
    P_IRIS,
    P_MEDIA_ACTIVE,
    P_POWER,
    P_STREAM,
    P_STREAM_PLATFORM,
    P_SYSTEM_FORMAT,
    P_TRANSPORT,
    P_VIDEO_FORMAT,
    P_WORKINGSET,
    P_ZOOM,
)
from .coordinator import BlackmagicCameraConfigEntry
from .entity import BlackmagicCameraEntity
from .values import active_slot, field, number, snake, workingset_slots

PARALLEL_UPDATES = 0

POWER_SOURCES = ["battery", "ac", "fiber", "usb", "poe"]
AUTOFOCUS_STATES = ["idle", "focusing", "focused", "paused", "error"]
TRANSPORT_MODES = ["input_preview", "input_record", "output"]
STREAM_STATES = ["idle", "connecting", "streaming", "flushing", "interrupted"]


@dataclass(frozen=True, kw_only=True)
class CameraSensorDescription(SensorEntityDescription):
    """Describes a camera sensor."""

    path: str
    value_fn: Callable[[BlackmagicCameraEntity], Any]
    attributes_fn: Callable[[BlackmagicCameraEntity], dict[str, Any] | None] | None = None


def _enum(key: str, options: list[str]) -> Callable[[BlackmagicCameraEntity], str | None]:
    def _value(entity: BlackmagicCameraEntity) -> str | None:
        raw = entity.prop_field(key)
        if not isinstance(raw, str):
            return None
        value = snake(raw)
        return value if value in options else None

    return _value


def _rounded(value: Any, digits: int) -> float | None:
    numeric = number(value)
    return None if numeric is None else round(numeric, digits)


def _first_battery(entity: BlackmagicCameraEntity) -> dict[str, Any] | None:
    batteries = entity.prop_field("batteries")
    if isinstance(batteries, list) and batteries and isinstance(batteries[0], dict):
        return batteries[0]
    return None


def _battery_attributes(entity: BlackmagicCameraEntity) -> dict[str, Any] | None:
    batteries = entity.prop_field("batteries")
    if not isinstance(batteries, list):
        return None
    attributes: dict[str, Any] = {"battery_count": len(batteries)}
    for index, battery in enumerate(batteries, start=1):
        if not isinstance(battery, dict):
            continue
        volts = number(battery.get("milliVolt"))
        attributes[f"battery_{index}_voltage"] = None if volts is None else round(volts / 1000, 1)
        attributes[f"battery_{index}_charge"] = battery.get("chargeRemainingPercent")
        attributes[f"battery_{index}_status"] = battery.get("statusFlags")
    return attributes


def _remaining_record_time(entity: BlackmagicCameraEntity) -> float | None:
    slot = active_slot(entity.prop(P_WORKINGSET), entity.prop(P_MEDIA_ACTIVE))
    return number(slot.get("remainingRecordTime")) if slot else None


def _active_media_attributes(entity: BlackmagicCameraEntity) -> dict[str, Any] | None:
    slot = active_slot(entity.prop(P_WORKINGSET), entity.prop(P_MEDIA_ACTIVE))
    if slot is None:
        return None
    return {"volume": slot.get("volume"), "device_name": slot.get("deviceName")}


def _resolution(entity: BlackmagicCameraEntity) -> str | None:
    resolution = entity.prop_field("recordResolution")
    width = number(field(resolution, "width"))
    height = number(field(resolution, "height"))
    if width is None or height is None:
        return None
    return f"{int(width)}x{int(height)}"


def _format_attributes(entity: BlackmagicCameraEntity) -> dict[str, Any] | None:
    value = entity.prop()
    if not isinstance(value, dict):
        return None
    return {
        "off_speed_enabled": value.get("offSpeedEnabled"),
        "off_speed_frame_rate": value.get("offSpeedFrameRate"),
    }


def _resolution_attributes(entity: BlackmagicCameraEntity) -> dict[str, Any] | None:
    value = entity.prop()
    if not isinstance(value, dict):
        return None
    sensor = value.get("sensorResolution")
    return {
        "description": field(value.get("resolutionDescriptor"), "description"),
        "aspect_ratio": field(value.get("resolutionDescriptor"), "aspectRatio"),
        "sensor_resolution": (
            f"{field(sensor, 'width')}x{field(sensor, 'height')}"
            if isinstance(sensor, dict)
            else None
        ),
    }


def _platform_attributes(entity: BlackmagicCameraEntity) -> dict[str, Any] | None:
    # Only non secret fields: the stream key, passphrase and URL are left out.
    value = entity.prop()
    if not isinstance(value, dict):
        return None
    return {"server": value.get("server"), "quality": value.get("quality")}


SENSORS: tuple[CameraSensorDescription, ...] = (
    CameraSensorDescription(
        key="remaining_record_time",
        translation_key="remaining_record_time",
        path=P_WORKINGSET,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=_remaining_record_time,
        attributes_fn=_active_media_attributes,
    ),
    CameraSensorDescription(
        key="battery",
        translation_key="battery",
        path=P_POWER,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda e: number(field(_first_battery(e), "chargeRemainingPercent")),
        attributes_fn=_battery_attributes,
    ),
    CameraSensorDescription(
        key="voltage",
        translation_key="voltage",
        path=P_POWER,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        suggested_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=lambda e: number(e.prop_field("milliVolt")),
    ),
    CameraSensorDescription(
        key="power_source",
        translation_key="power_source",
        path=P_POWER,
        device_class=SensorDeviceClass.ENUM,
        options=POWER_SOURCES,
        value_fn=_enum("source", POWER_SOURCES),
    ),
    CameraSensorDescription(
        key="lens_aperture",
        translation_key="lens_aperture",
        path=P_IRIS,
        suggested_display_precision=1,
        value_fn=lambda e: _rounded(e.prop_field("apertureStop"), 1),
    ),
    CameraSensorDescription(
        key="lens_focal_length",
        translation_key="lens_focal_length",
        path=P_ZOOM,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        suggested_display_precision=0,
        value_fn=lambda e: number(e.prop_field("focalLength")),
    ),
    CameraSensorDescription(
        key="lens_autofocus_state",
        translation_key="lens_autofocus_state",
        path=P_AUTOFOCUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=AUTOFOCUS_STATES,
        value_fn=_enum("state", AUTOFOCUS_STATES),
    ),
    CameraSensorDescription(
        key="video_format",
        translation_key="video_format",
        path=P_VIDEO_FORMAT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.prop_field("name"),
    ),
    CameraSensorDescription(
        key="codec",
        translation_key="codec",
        path=P_SYSTEM_FORMAT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.prop_field("codec") or e.prop_field("codec", P_CODEC_FORMAT),
    ),
    CameraSensorDescription(
        key="frame_rate",
        translation_key="frame_rate",
        path=P_SYSTEM_FORMAT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: e.prop_field("frameRate"),
        attributes_fn=_format_attributes,
    ),
    CameraSensorDescription(
        key="recording_resolution",
        translation_key="recording_resolution",
        path=P_SYSTEM_FORMAT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_resolution,
        attributes_fn=_resolution_attributes,
    ),
    CameraSensorDescription(
        key="transport_mode",
        translation_key="transport_mode",
        path=P_TRANSPORT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        device_class=SensorDeviceClass.ENUM,
        options=TRANSPORT_MODES,
        value_fn=_enum("mode", TRANSPORT_MODES),
    ),
    CameraSensorDescription(
        key="stream_status",
        translation_key="stream_status",
        path=P_STREAM,
        entity_registry_enabled_default=False,
        device_class=SensorDeviceClass.ENUM,
        options=STREAM_STATES,
        value_fn=_enum("status", STREAM_STATES),
    ),
    CameraSensorDescription(
        key="stream_bitrate",
        translation_key="stream_bitrate",
        path=P_STREAM,
        entity_registry_enabled_default=False,
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        value_fn=lambda e: number(e.prop_field("bitrate")),
    ),
    CameraSensorDescription(
        key="stream_duration",
        translation_key="stream_duration",
        path=P_STREAM,
        entity_registry_enabled_default=False,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda e: number(e.prop_field("duration")),
    ),
    CameraSensorDescription(
        key="stream_cache",
        translation_key="stream_cache",
        path=P_STREAM,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda e: number(e.prop_field("cache")),
    ),
    CameraSensorDescription(
        key="stream_platform",
        translation_key="stream_platform",
        path=P_STREAM_PLATFORM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.prop_field("platform"),
        attributes_fn=_platform_attributes,
    ),
)


def _media_slot_description(index: int) -> CameraSensorDescription:
    def _slot(entity: BlackmagicCameraEntity) -> dict[str, Any] | None:
        slots = workingset_slots(entity.prop())
        return slots[index] if index < len(slots) else None

    def _value(entity: BlackmagicCameraEntity) -> float | None:
        slot = _slot(entity)
        total = number(field(slot, "totalSpace"))
        remaining = number(field(slot, "remainingSpace"))
        if not total or remaining is None:
            return None
        return round(remaining / total * 100, 1)

    def _attributes(entity: BlackmagicCameraEntity) -> dict[str, Any] | None:
        slot = _slot(entity)
        if slot is None:
            return {"inserted": False}
        total = number(slot.get("totalSpace"))
        remaining = number(slot.get("remainingSpace"))
        return {
            "inserted": True,
            "volume": slot.get("volume"),
            "device_name": slot.get("deviceName"),
            "active": bool(slot.get("activeDisk")),
            "clip_count": slot.get("clipCount"),
            "remaining_record_time": slot.get("remainingRecordTime"),
            "total_gb": None if total is None else round(total / 1e9, 1),
            "remaining_gb": None if remaining is None else round(remaining / 1e9, 1),
        }

    return CameraSensorDescription(
        key="media_slot",
        translation_key="media_slot",
        path=P_WORKINGSET,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=_value,
        attributes_fn=_attributes,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlackmagicCameraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up camera sensors."""
    coordinator = entry.runtime_data
    caps = coordinator.capabilities
    entities: list[CameraSensor] = [
        CameraSensor(coordinator, description, description.path)
        for description in SENSORS
        if caps.supports(description.path)
    ]
    if caps.supports(P_WORKINGSET):
        entities.extend(
            CameraSensor(
                coordinator,
                _media_slot_description(index),
                P_WORKINGSET,
                unique_suffix=str(index),
                placeholders={"slot": str(index + 1)},
            )
            for index in range(caps.media_slots)
        )
    async_add_entities(entities)


class CameraSensor(BlackmagicCameraEntity, SensorEntity):
    """A sensor backed by a camera property."""

    entity_description: CameraSensorDescription

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self)
