"""Base entity for the Blackmagic Camera integration."""

from __future__ import annotations

from typing import Any

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
        data = self.coordinator.data
        if data is None:
            return None
        return data.get(path or self._path or "")

    def prop_field(self, field: str, path: str | None = None) -> Any:
        """Return one field of a property object."""
        value = self.prop(path)
        if isinstance(value, dict):
            return value.get(field)
        return None

    @property
    def available(self) -> bool:
        """Return True when the camera reports a value for this entity."""
        if not super().available:
            return False
        if self._path is None:
            return True
        return self.prop() is not None
