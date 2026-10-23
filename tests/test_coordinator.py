"""Tests for push updates, diagnostics and the API client."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.blackmagic_camera.api import (
    BlackmagicCameraApi,
    CannotConnect,
    InvalidAuth,
    WebsocketUnsupported,
)
from custom_components.blackmagic_camera.const import DOMAIN
from custom_components.blackmagic_camera.diagnostics import async_get_config_entry_diagnostics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import BASE, ENTRY_DATA, HOST


def ws_sequence(*items: Any) -> AsyncMock:
    """Return ws_connect results in order, then report no websocket forever."""
    queue = list(items)

    async def _connect() -> Any:
        if not queue:
            raise WebsocketUnsupported("no more sockets")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return AsyncMock(side_effect=_connect)


async def settle(hass: HomeAssistant) -> None:
    """Let the websocket background task run."""
    for _ in range(5):
        await asyncio.sleep(0)
        await hass.async_block_till_done()


WS_PATCHES = (
    "custom_components.blackmagic_camera.coordinator._WS_RETRY_MIN",
    "custom_components.blackmagic_camera.coordinator._WS_UNSUPPORTED_RETRY",
)


class FakeWebsocket:
    """A websocket that replays messages and stays open until released."""

    def __init__(self, messages: list[dict[str, Any] | str]) -> None:
        """Initialize."""
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.release = asyncio.Event()
        self._messages = messages

    async def send_json(self, data: dict[str, Any]) -> None:
        """Record a sent message."""
        self.sent.append(data)

    async def close(self) -> None:
        """Close the socket."""
        self.closed = True
        self.release.set()

    def __aiter__(self) -> FakeWebsocket:
        """Iterate over messages."""
        self._iter = iter(self._messages)
        return self

    async def __anext__(self) -> MagicMock:
        """Return the next message, then wait for release."""
        try:
            item = next(self._iter)
        except StopIteration:
            await self.release.wait()
            raise StopAsyncIteration from None
        message = MagicMock()
        message.type = aiohttp.WSMsgType.TEXT
        message.data = item if isinstance(item, str) else json.dumps(item)
        return message


async def test_websocket_push(
    hass: HomeAssistant, mock_camera: Callable[..., None], aioclient_mock: AiohttpClientMocker
) -> None:
    """Pushed property changes update entities without polling."""
    mock_camera()
    fake = FakeWebsocket(
        [
            {"type": "event", "data": {"action": "websocketOpened"}},
            "not json",
            {"type": "event"},
            {
                "type": "response",
                "id": 1,
                "data": {
                    "action": "subscribe",
                    "success": True,
                    "properties": ["/video/iso"],
                    "values": {"/video/iso": {"iso": 640}, "/unknown": {"x": 1}},
                },
            },
            {
                "type": "event",
                "data": {
                    "action": "propertyValueChanged",
                    "property": "/transports/0/record",
                    "value": {"recording": True},
                },
            },
        ]
    )
    entry = MockConfigEntry(domain=DOMAIN, title="PYXIS 6K", data=dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    with (
        patch.object(BlackmagicCameraApi, "ws_connect", ws_sequence(fake)),
        patch(WS_PATCHES[0], 0),
        patch(WS_PATCHES[1], 3600),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
        await hass.async_block_till_done()

        coordinator = entry.runtime_data
        assert coordinator.websocket_connected
        assert hass.states.get("select.pyxis_6k_iso").state == "640"
        assert hass.states.get("switch.pyxis_6k_record").state == "on"
        assert hass.states.get("binary_sensor.pyxis_6k_connection").attributes["push_updates"]
        # Everything the camera announces in /event/list in one request.
        assert len(fake.sent) == 1
        subscribed = fake.sent[0]["data"]["properties"]
        assert fake.sent[0]["data"]["action"] == "subscribe"
        assert "/video/iso" in subscribed
        assert "/transports/0/prerecord/supportedMaxDurations" not in subscribed

        # Subscribed properties are no longer polled between safety polls.
        coordinator._poll_count = 1
        assert "/video/iso" not in coordinator._paths_to_poll()

        # Only entities that read a changed property write a new state.
        battery_before = hass.states.get("sensor.pyxis_6k_battery").last_reported
        iso_before = hass.states.get("select.pyxis_6k_iso").last_reported
        coordinator._handle_ws_text(
            json.dumps(
                {
                    "type": "event",
                    "data": {
                        "action": "propertyValueChanged",
                        "property": "/video/iso",
                        "value": {"iso": 800},
                    },
                }
            )
        )
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        await hass.async_block_till_done()
        assert hass.states.get("select.pyxis_6k_iso").state == "800"
        assert hass.states.get("select.pyxis_6k_iso").last_reported != iso_before
        assert hass.states.get("sensor.pyxis_6k_battery").last_reported == battery_before

        # Closing the socket falls back to polling.
        fake.release.set()
        await settle(hass)
        assert not coordinator.websocket_connected

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_websocket_without_event_list(
    hass: HomeAssistant, mock_camera: Callable[..., None]
) -> None:
    """Without /event/list every property is subscribed on its own."""
    mock_camera({"/event/list": {"status": 404, "json": {"error": "Not found"}}})
    fake = FakeWebsocket([])
    entry = MockConfigEntry(domain=DOMAIN, title="PYXIS 6K", data=dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    with (
        patch.object(BlackmagicCameraApi, "ws_connect", ws_sequence(fake)),
        patch(WS_PATCHES[0], 0),
        patch(WS_PATCHES[1], 3600),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await settle(hass)
        coordinator = entry.runtime_data
        assert coordinator.capabilities.still_capture
        assert len(fake.sent) == len(coordinator.capabilities.state_paths())
        fake.release.set()
        await settle(hass)
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_websocket_unavailable_keeps_polling(
    hass: HomeAssistant, mock_camera: Callable[..., None]
) -> None:
    """Without a websocket the integration simply polls."""
    mock_camera()
    entry = MockConfigEntry(domain=DOMAIN, title="PYXIS 6K", data=dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    connect = ws_sequence(CannotConnect("down"), InvalidAuth("nope"))
    with (
        patch.object(BlackmagicCameraApi, "ws_connect", connect),
        patch(WS_PATCHES[0], 0),
        patch(WS_PATCHES[1], 3600),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await settle(hass)
        assert connect.await_count == 2
        assert not entry.runtime_data.websocket_connected
        assert hass.states.get("select.pyxis_6k_iso").state == "400"
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_websocket_retried_when_camera_returns(
    hass: HomeAssistant, mock_camera: Callable[..., None]
) -> None:
    """A websocket refused during a reboot is retried once the camera is back."""
    mock_camera()
    fake = FakeWebsocket(
        [{"type": "response", "id": 1, "data": {"action": "subscribe", "success": True}}]
    )
    connect = ws_sequence(WebsocketUnsupported("rebooting"), fake)
    entry = MockConfigEntry(domain=DOMAIN, title="PYXIS 6K", data=dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    with (
        patch.object(BlackmagicCameraApi, "ws_connect", connect),
        patch(WS_PATCHES[0], 0),
        patch(WS_PATCHES[1], 3600),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await settle(hass)
        coordinator = entry.runtime_data
        assert connect.await_count == 1
        assert not coordinator.websocket_connected

        # Offline and back: the paused websocket loop wakes up at once.
        coordinator._set_offline(CannotConnect("gone"))
        coordinator._set_online()
        await settle(hass)
        assert connect.await_count == 2
        assert coordinator.websocket_connected
        fake.release.set()
        await settle(hass)
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnostics_redact_secrets(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_camera: Callable[..., None],
) -> None:
    """Diagnostics never contain the host or stream secrets."""
    mock_camera()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    text = json.dumps(diagnostics)
    assert HOST not in text
    assert "abcd-secret-key" not in text
    assert diagnostics["capabilities"]["displays"] == ["LCD", "FrontUSBC", "MainSDI", "Stream"]
    assert diagnostics["data"]["/livestreams/0/activePlatform"]["key"] == "**REDACTED**"


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------
async def test_api_basic_auth_retry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A basic auth challenge is answered with the configured credentials."""
    answers = [
        AiohttpClientMockResponse(
            "get", f"{BASE}/video/iso", status=401, headers={"WWW-Authenticate": 'Basic realm="x"'}
        ),
        AiohttpClientMockResponse("get", f"{BASE}/video/iso", json={"iso": 800}),
    ]

    async def _side_effect(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        return answers.pop(0)

    aioclient_mock.get(f"{BASE}/video/iso", side_effect=_side_effect)
    session = aiohttp.ClientSession()
    object.__setattr__(session, "_request", aioclient_mock.match_request)
    api = BlackmagicCameraApi(session, HOST, username="admin", password="pw")
    assert await api.get("/video/iso") == {"iso": 800}
    assert aioclient_mock.mock_calls[1][3] == {"Authorization": "Basic YWRtaW46cHc="}
    await session.close()


async def test_api_probe_and_bodies(aioclient_mock: AiohttpClientMocker) -> None:
    """Probe results and response decoding."""
    aioclient_mock.get(f"{BASE}/a", status=501, json={"error": "Not implemented"})
    aioclient_mock.get(f"{BASE}/b", status=204)
    aioclient_mock.get(f"{BASE}/c", status=403)
    aioclient_mock.get(f"{BASE}/d", text="plain")
    aioclient_mock.put(f"{BASE}/e", status=400, text="bad value")
    session = aiohttp.ClientSession()
    object.__setattr__(session, "_request", aioclient_mock.match_request)
    api = BlackmagicCameraApi(session, f"{HOST}/")
    assert await api.probe("/a") == (False, None)
    assert await api.probe("/b") == (True, None)
    assert await api.probe("/c") == (True, None)
    assert await api.get("/d") == "plain"
    with pytest.raises(Exception, match="bad value"):
        await api.put("/e", {"x": 1})
    assert api.web_url == f"http://{HOST}/"
    await session.close()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (aiohttp.WSServerHandshakeError(MagicMock(), (), status=404), WebsocketUnsupported),
        (aiohttp.WSServerHandshakeError(MagicMock(), (), status=401), InvalidAuth),
        (aiohttp.WSServerHandshakeError(MagicMock(), (), status=500), CannotConnect),
        (aiohttp.ClientConnectionError(), CannotConnect),
    ],
)
async def test_api_websocket_errors(error: Exception, expected: type[Exception]) -> None:
    """Websocket handshake failures map to the client errors."""
    session = MagicMock()
    session.ws_connect = AsyncMock(side_effect=error)
    api = BlackmagicCameraApi(session, HOST, use_https=True)
    with pytest.raises(expected):
        await api.ws_connect()


async def test_api_websocket_path_is_remembered() -> None:
    """The first path that offers a websocket is used from then on."""
    ws = MagicMock()
    session = MagicMock()
    session.ws_connect = AsyncMock(return_value=ws)
    api = BlackmagicCameraApi(session, HOST)
    assert await api.ws_connect() is ws
    assert session.ws_connect.call_args[0][0] == f"ws://{HOST}/control/api/v1/event/websocket"
    assert await api.ws_connect() is ws
    assert session.ws_connect.call_count == 2
