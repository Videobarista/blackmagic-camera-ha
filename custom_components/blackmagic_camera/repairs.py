"""Repairs for the Blackmagic Camera integration.

A camera that presents a different TLS certificate than the pinned one is
kept offline until the user confirms the new certificate here. That happens
after a firmware update or factory reset, but would also happen if someone
on the network posed as the camera, so it is never accepted silently.
Trusting the new certificate keeps the same config entry and entities.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .api import BlackmagicCameraError, format_fingerprint
from .const import CONF_CERT_FINGERPRINT
from .helpers import async_get_fingerprint


class TrustCertificateFlow(RepairsFlow):
    """Show the old and new certificate fingerprint and pin the new one."""

    def __init__(self, entry: ConfigEntry | None) -> None:
        """Initialize the flow."""
        super().__init__()
        self._entry = entry
        self._fingerprint: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        """Start the fix flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Ask the user to trust the certificate the camera presents now."""
        entry = self._entry
        if entry is None:
            return self.async_abort(reason="entry_removed")

        if user_input is not None and self._fingerprint is not None:
            # Pin exactly the certificate the user was shown.
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_CERT_FINGERPRINT: self._fingerprint}
            )
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_create_entry(data={})

        try:
            self._fingerprint = await async_get_fingerprint(self.hass, entry.data[CONF_HOST])
        except BlackmagicCameraError:
            return self.async_abort(reason="cannot_connect")

        old = entry.data.get(CONF_CERT_FINGERPRINT)
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": entry.title,
                "host": entry.data[CONF_HOST],
                "old_fingerprint": format_fingerprint(old) if old else "-",
                "new_fingerprint": format_fingerprint(self._fingerprint),
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for a changed certificate."""
    entry_id = (data or {}).get("entry_id")
    entry = hass.config_entries.async_get_entry(entry_id) if isinstance(entry_id, str) else None
    return TrustCertificateFlow(entry)
