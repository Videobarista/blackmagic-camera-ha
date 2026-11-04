"""Shared helpers for the Blackmagic Camera integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import aiohttp

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession, async_get_clientsession
from homeassistant.util.ssl import get_default_no_verify_context

from .api import BlackmagicCameraApi, async_fetch_fingerprint
from .const import CONF_CERT_FINGERPRINT, CONF_USE_HTTPS


def pins_certificate(data: Mapping[str, Any]) -> bool:
    """Return True if the camera's certificate is pinned instead of verified.

    Cameras use a self signed certificate, which cannot be verified against a
    certificate authority. Pinning its fingerprint at first contact still
    stops anyone else on the network from posing as the camera.
    """
    return bool(data.get(CONF_USE_HTTPS)) and not data.get(CONF_VERIFY_SSL)


async def async_get_fingerprint(hass: HomeAssistant, host: str) -> str:
    """Return the SHA-256 fingerprint of the camera's TLS certificate."""
    return await async_fetch_fingerprint(host, get_default_no_verify_context())


def async_create_api(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> tuple[BlackmagicCameraApi, Callable[[], None]]:
    """Create an API client for a config entry and a cleanup callback.

    Without credentials the shared Home Assistant session is used. With
    credentials a dedicated session is created that also answers HTTP digest
    challenges; basic authentication is handled by the client itself.
    """
    verify_ssl = bool(data.get(CONF_VERIFY_SSL, False))
    username = data.get(CONF_USERNAME) or None
    password = data.get(CONF_PASSWORD) or None

    if username:
        session = async_create_clientsession(
            hass,
            verify_ssl=verify_ssl,
            auto_cleanup=False,
            middlewares=(aiohttp.DigestAuthMiddleware(username, password or ""),),
        )
        cleanup: Callable[[], None] = session.detach
    else:
        session = async_get_clientsession(hass, verify_ssl=verify_ssl)

        def cleanup() -> None:
            """Nothing to clean up for the shared session."""

    api = BlackmagicCameraApi(
        session,
        data[CONF_HOST],
        use_https=bool(data.get(CONF_USE_HTTPS, False)),
        username=username,
        password=password,
        fingerprint=data.get(CONF_CERT_FINGERPRINT) if pins_certificate(data) else None,
    )
    return api, cleanup
