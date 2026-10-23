"""Diagnostics for the Blackmagic Camera integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import REDACT_KEYS
from .coordinator import BlackmagicCameraConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BlackmagicCameraConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), {*REDACT_KEYS, CONF_HOST}),
            "options": dict(entry.options),
        },
        "capabilities": coordinator.capabilities.as_dict(),
        "websocket_connected": coordinator.websocket_connected,
        "last_update_success": coordinator.last_update_success,
        "data": async_redact_data(coordinator.data or {}, REDACT_KEYS),
    }
