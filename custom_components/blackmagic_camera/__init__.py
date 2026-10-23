"""The Blackmagic Camera integration.

Controls Blackmagic Design cameras (PYXIS, Cinema Camera 6K, URSA Cine,
URSA Broadcast G2, Studio Cameras, ...) through the Camera Control REST API.
This is an unofficial, community made integration.
"""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .coordinator import BlackmagicCameraConfigEntry, BlackmagicCameraCoordinator
from .helpers import async_create_api

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: BlackmagicCameraConfigEntry) -> bool:
    """Set up a Blackmagic camera from a config entry."""
    api, cleanup = async_create_api(hass, entry.data)
    entry.async_on_unload(cleanup)

    coordinator = BlackmagicCameraCoordinator(hass, entry, api)
    await coordinator.async_initialize()
    if not coordinator.last_update_success or coordinator.data is None:
        # Camera is offline: record that, but never fail the setup.
        await coordinator.async_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_websocket()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BlackmagicCameraConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BlackmagicCameraConfigEntry) -> None:
    """Remove the cached camera features when the entry is deleted."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: BlackmagicCameraConfigEntry) -> None:
    """Reload when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)
