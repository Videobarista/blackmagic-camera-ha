"""The integration only talks to the configured camera.

The runtime half of the "local only" badge: every HTTP request, websocket
and TLS connection the integration makes during setup, polling and commands
goes to the camera's address. Real network access is blocked in the tests
anyway, so anything else would fail. The static half is
scripts/check_local_only.py, which is tested here as well.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import shutil
from typing import Any
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from yarl import URL

from custom_components.blackmagic_camera.api import BlackmagicCameraApi, WebsocketUnsupported
from custom_components.blackmagic_camera.const import CONF_USE_HTTPS, DOMAIN
from homeassistant.const import CONF_HOST, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import HOST

ROOT = Path(__file__).resolve().parents[1]


def _load_checker() -> Any:
    spec = importlib.util.spec_from_file_location(
        "check_local_only", ROOT / "scripts" / "check_local_only.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_all_traffic_goes_to_the_camera(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_camera: Callable[..., None],
    mock_fingerprint: AsyncMock,
) -> None:
    """Setup, certificate pinning, polling, push and commands only reach the camera."""
    http_urls: list[URL] = []
    ws_urls: list[URL] = []
    match_request = aioclient_mock.match_request

    async def _record_http(method: str, url: Any, **kwargs: Any) -> Any:
        http_urls.append(URL(str(url)))
        return await match_request(method, url, **kwargs)

    async def _record_ws(api: BlackmagicCameraApi, path: str) -> Any:
        ws_urls.append(URL(api._websocket_url(path)))
        raise WebsocketUnsupported("not used in this test")

    mock_camera(scheme="https")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="PYXIS 6K",
        data={CONF_HOST: HOST, CONF_USE_HTTPS: True, CONF_VERIFY_SSL: False},
        minor_version=2,
    )
    entry.add_to_hass(hass)
    with (
        patch.object(aioclient_mock, "match_request", _record_http),
        patch.object(BlackmagicCameraApi, "_ws_connect_path", _record_ws),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        for minutes in (1, 2, 3):
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=minutes))
            await hass.async_block_till_done()
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.pyxis_6k_auto_white_balance"}, blocking=True
        )
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": "switch.pyxis_6k_color_bars"}, blocking=True
        )
        assert await hass.config_entries.async_unload(entry.entry_id)

    assert http_urls, "the test made no requests"
    assert ws_urls, "the test opened no websocket"
    assert {(url.scheme, url.host, url.port) for url in http_urls} == {("https", HOST, 443)}
    assert {(url.scheme, url.host) for url in ws_urls} == {("wss", HOST)}
    assert [call.args[0] for call in mock_fingerprint.await_args_list] == [HOST]


def test_static_check_passes() -> None:
    """The integration passes scripts/check_local_only.py."""
    checker = _load_checker()
    assert checker.main(["check_local_only.py"]) == 0


def test_static_check_catches_leaks(tmp_path: Path) -> None:
    """The check fails on an integration that could send data elsewhere."""
    checker = _load_checker()
    leaky = tmp_path / "leaky"
    shutil.copytree(ROOT / "custom_components" / "blackmagic_camera", leaky)
    manifest = json.loads((leaky / "manifest.json").read_text())
    manifest.update(iot_class="cloud_polling", requirements=["phonehome==1.0"])
    (leaky / "manifest.json").write_text(json.dumps(manifest))
    (leaky / "telemetry.py").write_text(
        "import asyncio\n"
        "import requests\n"
        "from homeassistant.helpers.aiohttp_client import async_get_clientsession\n\n\n"
        "async def leak(hass):\n"
        "    session = async_get_clientsession(hass)\n"
        "    await session.post('https://telemetry.example.com/collect')\n"
        "    await asyncio.start_server(None, '0.0.0.0', 8080)\n"
        "    await asyncio.open_connection('8.8.8.8', 53)\n"
        "    return 'analytics.vendor.io'\n"
    )

    problems = checker.check_manifest(leaky)
    problems += checker.check_source(leaky / "telemetry.py", "leaky/telemetry.py")
    text = "\n".join(problems)
    for expected in (
        "iot_class",
        "phonehome",
        "imports requests",
        "async_get_clientsession() outside",
        "session.post() outside",
        "start_server() listens",
        "open_connection() outside",
        "https://telemetry.example.com",
        "8.8.8.8",
        "analytics.vendor.io",
    ):
        assert expected in text, expected
    assert checker.main(["check_local_only.py", str(leaky)]) == 1
