"""Binary sensors for the Blackmagic Camera integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import P_PRERECORD, P_REFERENCE_LOCK, P_STREAM_AVAILABLE
from .coordinator import BlackmagicCameraConfigEntry
from .entity import BlackmagicCameraEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class CameraBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a camera binary sensor."""

    path: str
    state_field: str
    attributes_fn: Callable[[BlackmagicCameraEntity], dict[str, Any] | None] | None = None


BINARY_SENSORS: tuple[CameraBinarySensorDescription, ...] = (
    CameraBinarySensorDescription(
        key="prerecording",
        translation_key="prerecording",
        path=P_PRERECORD,
        state_field="prerecording",
        attributes_fn=lambda e: {"duration": e.prop_field("duration")},
    ),
    CameraBinarySensorDescription(
        key="reference_lock",
        translation_key="reference_lock",
        path=P_REFERENCE_LOCK,
        state_field="locked",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    CameraBinarySensorDescription(
        key="stream_available",
        translation_key="stream_available",
        path=P_STREAM_AVAILABLE,
        state_field="available",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        attributes_fn=lambda e: {"reasons": e.prop_field("reasons") or []},
    ),
)

CONNECTION = BinarySensorEntityDescription(
    key="connection",
    translation_key="connection",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlackmagicCameraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up camera binary sensors."""
    coordinator = entry.runtime_data
    caps = coordinator.capabilities
    entities: list[BinarySensorEntity] = [ConnectionBinarySensor(coordinator, CONNECTION, None)]
    entities.extend(
        CameraBinarySensor(coordinator, description, description.path)
        for description in BINARY_SENSORS
        if caps.supports(description.path)
    )
    async_add_entities(entities)


class CameraBinarySensor(BlackmagicCameraEntity, BinarySensorEntity):
    """A binary sensor backed by a boolean field of a property."""

    entity_description: CameraBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        value = self.prop_field(self.entity_description.state_field)
        return value if isinstance(value, bool) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self)


class ConnectionBinarySensor(BlackmagicCameraEntity, BinarySensorEntity):
    """Shows whether the camera answers; stays available while it is off."""

    @property
    def available(self) -> bool:
        """Always available, so an offline camera shows as disconnected."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True while the camera is reachable."""
        return self.coordinator.online

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return how state updates arrive."""
        return {"push_updates": self.coordinator.websocket_connected}
