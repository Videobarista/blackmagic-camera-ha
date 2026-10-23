"""Tests for the Blackmagic Camera config flow."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.blackmagic_camera.const import (
    CONF_SCAN_INTERVAL,
    CONF_USE_HTTPS,
    CONF_USE_WEBSOCKET,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import BASE, ENTRY_DATA, HOST

PRODUCT = {"deviceName": "PYXIS 6K", "productName": "PYXIS 6K", "softwareVersion": "10.2"}

USER_INPUT = {CONF_HOST: HOST, CONF_USE_HTTPS: False, CONF_VERIFY_SSL: False}


@pytest.fixture(autouse=True)
def no_setup() -> None:
    """Do not set up the integration after creating an entry."""
    with patch("custom_components.blackmagic_camera.async_setup_entry", return_value=True):
        yield


async def test_user_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A reachable camera creates an entry named after the camera."""
    aioclient_mock.get(f"{BASE}/system/product", json=PRODUCT)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_HOST: f"http://{HOST}/"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "PYXIS 6K"
    assert result["data"] == ENTRY_DATA


async def test_user_flow_with_credentials(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Credentials are stored with the entry."""
    aioclient_mock.get(f"https://{HOST}/control/api/v1/system/product", json=PRODUCT)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: HOST,
            CONF_USE_HTTPS: True,
            CONF_VERIFY_SSL: False,
            CONF_USERNAME: " admin ",
            CONF_PASSWORD: "secret",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_HOST: HOST,
        CONF_USE_HTTPS: True,
        CONF_VERIFY_SSL: False,
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "secret",
    }


async def test_title_falls_back_to_host(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without a name in the product info the host is used as title."""
    aioclient_mock.get(f"{BASE}/system/product", json={"softwareVersion": "10.2"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == HOST


@pytest.mark.parametrize(
    ("mock_kwargs", "error"),
    [
        ({"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        ({"exc": TimeoutError()}, "cannot_connect"),
        ({"status": 401}, "invalid_auth"),
        ({"status": 404}, "unknown"),
        ({"json": ["not", "a", "dict"]}, "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict,
    error: str,
) -> None:
    """Errors are shown and the flow recovers once the camera answers."""
    aioclient_mock.get(f"{BASE}/system/product", **mock_kwargs)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/system/product", json=PRODUCT)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_unexpected_exception(hass: HomeAssistant) -> None:
    """An unexpected exception is reported as unknown."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch(
        "custom_components.blackmagic_camera.api.BlackmagicCameraApi.get_product",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_already_configured(hass: HomeAssistant) -> None:
    """The same camera cannot be added twice."""
    MockConfigEntry(domain=DOMAIN, data=dict(ENTRY_DATA)).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The connection settings can be changed."""
    entry = MockConfigEntry(domain=DOMAIN, data=dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    new_host = "192.0.2.20"
    aioclient_mock.get(f"http://{new_host}/control/api/v1/system/product", json=PRODUCT)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_HOST: new_host}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == new_host


async def test_reconfigure_error_then_success(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Reconfigure shows connection errors and recovers."""
    entry = MockConfigEntry(domain=DOMAIN, data=dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    aioclient_mock.get(f"{BASE}/system/product", exc=aiohttp.ClientConnectionError())

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/system/product", json=PRODUCT)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


async def test_reconfigure_to_existing_host(hass: HomeAssistant) -> None:
    """Reconfigure cannot point an entry at another configured camera."""
    entry = MockConfigEntry(domain=DOMAIN, data=dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    MockConfigEntry(domain=DOMAIN, data={**ENTRY_DATA, CONF_HOST: "192.0.2.30"}).add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_HOST: "192.0.2.30"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant) -> None:
    """Polling interval and websocket use can be changed."""
    entry = MockConfigEntry(domain=DOMAIN, data=dict(ENTRY_DATA))
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30, CONF_USE_WEBSOCKET: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 30, CONF_USE_WEBSOCKET: False}
