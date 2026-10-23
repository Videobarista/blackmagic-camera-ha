"""Config flow for the Blackmagic Camera integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ApiError, CannotConnect, HttpsRequired, InvalidAuth
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_USE_HTTPS,
    CONF_USE_WEBSOCKET,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_WEBSOCKET,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .helpers import async_create_api

_LOGGER = logging.getLogger(__name__)


def _connection_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Return the connection form, pre-filled with defaults."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): TextSelector(),
            vol.Required(
                CONF_USE_HTTPS, default=defaults.get(CONF_USE_HTTPS, False)
            ): BooleanSelector(),
            vol.Required(
                CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, False)
            ): BooleanSelector(),
            vol.Optional(
                CONF_USERNAME, description={"suggested_value": defaults.get(CONF_USERNAME)}
            ): TextSelector(TextSelectorConfig(autocomplete="username")),
            vol.Optional(
                CONF_PASSWORD, description={"suggested_value": defaults.get(CONF_PASSWORD)}
            ): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
            ),
        }
    )


def _clean_input(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the form input."""
    host = str(user_input[CONF_HOST]).strip()
    for prefix in ("http://", "https://"):
        if host.lower().startswith(prefix):
            host = host[len(prefix) :]
    host = host.split("/", 1)[0]
    data: dict[str, Any] = {
        CONF_HOST: host,
        CONF_USE_HTTPS: bool(user_input.get(CONF_USE_HTTPS, False)),
        CONF_VERIFY_SSL: bool(user_input.get(CONF_VERIFY_SSL, False)),
    }
    if username := str(user_input.get(CONF_USERNAME) or "").strip():
        data[CONF_USERNAME] = username
        data[CONF_PASSWORD] = str(user_input.get(CONF_PASSWORD) or "")
    return data


async def _async_validate(hass: HomeAssistant, data: Mapping[str, Any]) -> dict[str, Any]:
    """Connect to the camera and return its product information."""
    api, cleanup = async_create_api(hass, data)
    try:
        return await api.get_product()
    finally:
        cleanup()


class BlackmagicCameraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Blackmagic cameras."""

    VERSION = 1
    # 2: "Capture still" switched off once for entries made before 1.0.6.
    MINOR_VERSION = 2

    async def _async_try_connect(
        self, data: dict[str, Any], errors: dict[str, str]
    ) -> dict[str, Any] | None:
        """Validate the connection, filling errors on failure."""
        try:
            return await _async_validate(self.hass, data)
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except HttpsRequired:
            errors["base"] = "https_required"
        except ApiError as err:
            # 404: the camera answers but the REST API (web media manager) is off.
            _LOGGER.debug("Camera %s answered: %s", data.get(CONF_HOST), err)
            errors["base"] = "api_unavailable" if err.status == 404 else "unknown"
        except Exception:
            _LOGGER.exception("Unexpected error connecting to %s", data.get(CONF_HOST))
            errors["base"] = "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean_input(user_input)
            self._async_abort_entries_match({CONF_HOST: data[CONF_HOST]})
            product = await self._async_try_connect(data, errors)
            if product is not None:
                title = product.get("deviceName") or product.get("productName") or data[CONF_HOST]
                return self.async_create_entry(title=str(title), data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the connection settings of an existing camera."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean_input(user_input)
            for other in self._async_current_entries(include_ignore=False):
                if (
                    other.entry_id != entry.entry_id
                    and other.data.get(CONF_HOST) == data[CONF_HOST]
                ):
                    return self.async_abort(reason="already_configured")
            if await self._async_try_connect(data, errors) is not None:
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(user_input or entry.data),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BlackmagicCameraOptionsFlow:
        """Return the options flow."""
        return BlackmagicCameraOptionsFlow()


class BlackmagicCameraOptionsFlow(OptionsFlow):
    """Polling and push options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_USE_WEBSOCKET: bool(user_input[CONF_USE_WEBSOCKET]),
                }
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Required(
                    CONF_USE_WEBSOCKET,
                    default=options.get(CONF_USE_WEBSOCKET, DEFAULT_USE_WEBSOCKET),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
