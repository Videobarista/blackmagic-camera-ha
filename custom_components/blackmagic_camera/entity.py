"""Base entity for the Blackmagic Camera integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import BlackmagicCameraCoordinator


class BlackmagicCameraEntity(CoordinatorEntity[BlackmagicCameraCoordinator]):
    """Common base for all camera entities.

    ``path`` is the REST property the entity reads; the entity is only
    available while the camera reports a value for it.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BlackmagicCameraCoordinator,
        description: EntityDescription,
        path: str | None,
        *,
        unique_suffix: str | None = None,
        placeholders: dict[str, str] | None = None,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._path = path
        # Properties this entity has read; used to skip unrelated updates.
        self._watched: set[str] = set()
        entry = coordinator.config_entry
        key = description.key if unique_suffix is None else f"{description.key}_{unique_suffix}"
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        if placeholders:
            self._attr_translation_placeholders = placeholders
        product = coordinator.capabilities.product
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            model=product.get("productName"),
            name=entry.title,
            sw_version=product.get("softwareVersion"),
            configuration_url=coordinator.api.web_url,
        )

    def prop(self, path: str | None = None) -> Any:
        """Return the current value of a property (default: own path)."""
        key = path or self._path or ""
        self._watched.add(key)
        data = self.coordinator.data
        if data is None:
            return None
        return data.get(key)

    def prop_field(self, field: str, path: str | None = None) -> Any:
        """Return one field of a property object."""
        value = self.prop(path)
        if isinstance(value, dict):
            return value.get(field)
        return None

    @property
    def available(self) -> bool:
        """Return True while the camera is online and reports a value."""
        if not super().available or not self.coordinator.online:
            return False
        if self._path is None:
            return True
        return self.prop() is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Only write a new state when a property this entity reads changed."""
        changed = self.coordinator.changed_paths
        if changed is not None and self._watched and self._watched.isdisjoint(changed):
            return
        super()._handle_coordinator_update()
