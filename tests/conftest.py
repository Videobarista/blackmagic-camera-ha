"""Fixtures for the Blackmagic Camera tests."""

from __future__ import annotations

from collections.abc import Callable, Generator
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.blackmagic_camera.const import CONF_USE_HTTPS, CONF_USE_WEBSOCKET, DOMAIN
from homeassistant.const import CONF_HOST, CONF_VERIFY_SSL

HOST = "192.0.2.10"
BASE = f"http://{HOST}/control/api/v1"
FIXTURE = Path(__file__).parent / "fixtures" / "pyxis_6k.json"

ENTRY_DATA = {CONF_HOST: HOST, CONF_USE_HTTPS: False, CONF_VERIFY_SSL: False}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations in all tests."""


@pytest.fixture(autouse=True)
def fast_read_back() -> Generator[None]:
    """Skip the read back delay after commands."""
    with patch("custom_components.blackmagic_camera.coordinator._READ_BACK_DELAY", 0):
        yield


@pytest.fixture
def camera_data() -> dict[str, dict[str, Any]]:
    """Return the recorded answers of a PYXIS 6K (firmware 10.2)."""
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def mock_camera(
    aioclient_mock: AiohttpClientMocker, camera_data: dict[str, dict[str, Any]]
) -> Callable[..., None]:
    """Register the camera answers; unknown paths answer 501 like the camera."""

    def _register(overrides: dict[str, dict[str, Any]] | None = None, host: str = HOST) -> None:
        base = f"http://{host}/control/api/v1"
        answers = {**camera_data, **(overrides or {})}
        for path, answer in answers.items():
            aioclient_mock.get(
                f"{base}{path}",
                status=answer["status"],
                json=answer.get("json"),
                text=answer.get("text"),
                exc=answer.get("exc"),
            )
        for method in ("put", "post"):
            aioclient_mock.request(method, f"{base}/panel/devices/f1/state", status=204)
        # Writes answer 204 by default.
        for path in answers:
            aioclient_mock.put(f"{base}{path}", status=204)
            aioclient_mock.post(f"{base}{path}", status=204)
        for path in (
            "/transports/0/stop",
            "/transports/0/doStillCapture",
            "/video/whiteBalance/doAuto",
            "/lens/focus/doAutoFocus",
            "/livestreams/0/start",
            "/livestreams/0/stop",
        ):
            aioclient_mock.put(f"{base}{path}", status=204)
            aioclient_mock.post(f"{base}{path}", status=204)
        aioclient_mock.get(f"{base}/livestreams/0/start", status=200, json=False)

    return _register


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry with the websocket disabled (tests use polling)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="PYXIS 6K",
        data=dict(ENTRY_DATA),
        options={CONF_USE_WEBSOCKET: False},
    )


def calls(aioclient_mock: AiohttpClientMocker, method: str, path: str) -> list[Any]:
    """Return the payloads sent with method to path."""
    return [
        data
        for call_method, url, data, _headers in aioclient_mock.mock_calls
        if call_method.lower() == method and str(url) == f"{BASE}{path}"
    ]
