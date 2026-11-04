"""Tests for the security hardening: input validation, limits and pinning."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.blackmagic_camera.api import (
    ApiError,
    BlackmagicCameraApi,
    CannotConnect,
    CertificateMismatch,
    async_fetch_fingerprint,
    bound_json,
    format_fingerprint,
    parse_json_text,
    split_host_port,
)
from custom_components.blackmagic_camera.capabilities import (
    CameraCapabilities,
    bounded_count,
    safe_names,
)
from custom_components.blackmagic_camera.config_flow import normalize_host
from custom_components.blackmagic_camera.const import (
    AUDIO_FEATURES,
    CONF_CERT_FINGERPRINT,
    CONF_USE_HTTPS,
    CONF_USE_WEBSOCKET,
    DOMAIN,
    MAX_RESPONSE_BYTES,
    MAX_SUB_DEVICES,
    MAX_TEXT_LENGTH,
)
from custom_components.blackmagic_camera.diagnostics import async_get_config_entry_diagnostics
from custom_components.blackmagic_camera.repairs import async_create_fix_flow
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from .conftest import BASE, ENTRY_DATA, FINGERPRINT, HOST, NEW_FINGERPRINT

HTTPS_DATA = {CONF_HOST: HOST, CONF_USE_HTTPS: True, CONF_VERIFY_SSL: False}
HTTPS_BASE = f"https://{HOST}/control/api/v1"


def _api(aioclient_mock: AiohttpClientMocker, **kwargs: Any) -> BlackmagicCameraApi:
    session = aiohttp.ClientSession()
    object.__setattr__(session, "_request", aioclient_mock.match_request)
    return BlackmagicCameraApi(session, HOST, **kwargs)


def _https_entry(**data: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="PYXIS 6K",
        data={**HTTPS_DATA, **data},
        options={CONF_USE_WEBSOCKET: False},
        minor_version=2,
    )


# ---------------------------------------------------------------------------
# Host input
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("192.168.1.10", "192.168.1.10"),
        (" https://cam.local/control ", "cam.local"),
        ("cam.local:8443", "cam.local:8443"),
        ("fe80::1", "[fe80::1]"),
        ("[fe80::1]:443", "[fe80::1]:443"),
        ("admin@cam.local", None),
        ("cam.local?x=1", None),
        ("cam local", None),
        ("cam.local:99999", None),
        ("cam.local:", None),
        ("-cam.local", None),
        ("[::1", None),
        ("[::1]x", None),
        ("", None),
    ],
)
def test_normalize_host(value: str, expected: str | None) -> None:
    """Only an address or host name with an optional port is accepted."""
    assert normalize_host(value) == expected


async def test_invalid_host_in_flows(hass: HomeAssistant) -> None:
    """The user and reconfigure steps reject an invalid host."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENTRY_DATA, CONF_HOST: "admin@192.0.2.10"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "invalid_host"}

    entry = MockConfigEntry(domain=DOMAIN, data=dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENTRY_DATA, CONF_HOST: "cam?x=1"}
    )
    assert result["errors"] == {CONF_HOST: "invalid_host"}


# ---------------------------------------------------------------------------
# Untrusted camera data
# ---------------------------------------------------------------------------
def test_safe_names_and_counts() -> None:
    """Names used in URL paths are plain; counts are bounded."""
    assert safe_names(
        ["LCD", "Main SDI", "../../transports/0/record", "a/b", "x?y", "", "LCD"]
    ) == [
        "LCD",
        "Main SDI",
    ]
    assert safe_names(["%2e%2e", ".hidden", 7, None]) == ["7"]
    assert len(safe_names(f"d{i}" for i in range(1000))) == MAX_SUB_DEVICES
    assert bounded_count(10**9) == MAX_SUB_DEVICES
    assert bounded_count(-5) == 0
    assert bounded_count("4") == 0
    assert bounded_count(True) == 0
    cached = CameraCapabilities.from_dict(
        {"displays": ["LCD", "../x"], "audio_channels": 10**6, "panel_buttons": ["f1", "f/2"]}
    )
    assert cached.displays == ["LCD"]
    assert cached.audio_channels == MAX_SUB_DEVICES
    assert cached.panel_buttons == ["f1"]


async def test_hostile_camera_names_and_counts(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_camera: Callable[..., None],
) -> None:
    """A camera reporting odd names or huge counts cannot steer requests."""
    mock_camera(
        {
            "/monitoring/display": {
                "status": 200,
                "json": {"displays": ["LCD", "../../transports/0/record"]},
            },
            "/panel/devices": {
                "status": 200,
                "json": [{"name": "f1", "type": "button"}, {"name": "../../x", "type": "button"}],
            },
            "/audio/channels": {"status": 200, "json": {"channels": 10**9}},
            # Channels the camera does not really have answer 404.
            **{
                f"/audio/channel/{channel}/{feature}": {"status": 404, "json": {}}
                for channel in range(4, MAX_SUB_DEVICES)
                for feature in AUDIO_FEATURES
            },
        }
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    caps = config_entry.runtime_data.capabilities
    assert caps.displays == ["LCD"]
    assert caps.panel_buttons == ["f1"]
    assert caps.audio_channels == MAX_SUB_DEVICES


def test_bound_json() -> None:
    """Text, lists, nesting and odd numbers are limited."""
    assert bound_json("x" * 1000) == "x" * MAX_TEXT_LENGTH
    assert bound_json({"a": float("nan"), "b": [1] * 10_000}) == {"a": None, "b": [1] * 512}
    deep: Any = 1
    for _ in range(40):
        deep = [deep]
    bounded = bound_json(deep)
    for _ in range(15):
        bounded = bounded[0]
    assert bounded == [None]
    with pytest.raises(ValueError, match="nested too deeply"):
        parse_json_text("[" * 100_000 + "]" * 100_000)


async def test_response_size_limit(aioclient_mock: AiohttpClientMocker) -> None:
    """Answers larger than the limit are refused, announced or not."""
    aioclient_mock.get(
        f"{BASE}/a", text="{}", headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)}
    )
    aioclient_mock.get(f"{BASE}/b", text="x" * (MAX_RESPONSE_BYTES + 10))
    aioclient_mock.get(f"{BASE}/c", text="y" * 1000)
    api = _api(aioclient_mock)
    with pytest.raises(ApiError, match="larger than"):
        await api.get("/a")
    with pytest.raises(ApiError, match="larger than"):
        await api.get("/b")
    assert await api.get("/c") == "y" * MAX_TEXT_LENGTH
    await api._session.close()


async def test_hostile_websocket_frame_is_ignored(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_camera: Callable[..., None]
) -> None:
    """A websocket frame nested absurdly deep is dropped, not crashing the loop."""
    mock_camera()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data
    coordinator._handle_ws_text('{"data": ' * 50_000 + "1" + "}" * 50_000)
    coordinator._handle_ws_text(
        json.dumps(
            {
                "type": "event",
                "data": {
                    "action": "propertyValueChanged",
                    "property": "/video/iso",
                    "value": {"iso": "9" * 5000},
                },
            }
        )
    )
    assert len(coordinator._pending_push["/video/iso"]["iso"]) == MAX_TEXT_LENGTH
    coordinator._push_unsub()


async def test_commands_one_at_a_time(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_camera: Callable[..., None]
) -> None:
    """Commands are sent one after another, never in parallel."""
    mock_camera()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data
    running = 0
    peak = 0

    async def _slow_put(path: str, payload: Any = None) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1

    with patch.object(coordinator.api, "put", side_effect=_slow_put):
        await asyncio.gather(
            *(coordinator.async_command("put", "/video/iso", {"iso": 800}, ()) for _ in range(5))
        )
    assert peak == 1


async def test_diagnostics_redact_names(
    hass: HomeAssistant, mock_camera: Callable[..., None]
) -> None:
    """The camera name, media volume names and fingerprint stay out of diagnostics."""
    mock_camera(
        {
            "/system/product": {
                "status": 200,
                "json": {"deviceName": "Camera of Jane", "productName": "PYXIS 6K"},
            },
            "/media/workingset": {
                "status": 200,
                "json": {"size": 1, "workingset": [{"index": 0, "volume": "Jane's drive"}]},
            },
        }
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="PYXIS 6K",
        data={**ENTRY_DATA, CONF_CERT_FINGERPRINT: FINGERPRINT},
        options={CONF_USE_WEBSOCKET: False},
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    text = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    assert "Jane" not in text
    assert FINGERPRINT not in text
    assert "PYXIS 6K" in text


# ---------------------------------------------------------------------------
# Certificate pinning
# ---------------------------------------------------------------------------
def test_fingerprint_helpers() -> None:
    """Fingerprints display as AA:BB:..; hosts split into name and port."""
    assert format_fingerprint("ab01") == "AB:01"
    assert split_host_port("cam.local", 443) == ("cam.local", 443)
    assert split_host_port("cam.local:8443", 443) == ("cam.local", 8443)
    assert split_host_port("[fe80::1]:444", 443) == ("fe80::1", 444)
    assert split_host_port("[fe80::1]", 443) == ("fe80::1", 443)


async def test_fetch_fingerprint() -> None:
    """The SHA-256 of the presented certificate is returned."""
    der = b"certificate bytes"
    writer = MagicMock()
    writer.get_extra_info.return_value.getpeercert.return_value = der
    writer.wait_closed = AsyncMock(side_effect=OSError("reset"))
    context = MagicMock()
    with patch("asyncio.open_connection", AsyncMock(return_value=(None, writer))) as connect:
        assert await async_fetch_fingerprint("cam.local:8443", context) == (
            hashlib.sha256(der).hexdigest()
        )
    connect.assert_awaited_once_with("cam.local", 8443, ssl=context)
    writer.close.assert_called_once()

    writer.get_extra_info.return_value = None
    writer.wait_closed = AsyncMock()
    with (
        patch("asyncio.open_connection", AsyncMock(return_value=(None, writer))),
        pytest.raises(CannotConnect, match="did not present"),
    ):
        await async_fetch_fingerprint("cam.local", context)

    with (
        patch("asyncio.open_connection", AsyncMock(side_effect=ConnectionRefusedError())),
        pytest.raises(CannotConnect, match="ConnectionRefusedError"),
    ):
        await async_fetch_fingerprint("cam.local", context)


async def test_api_certificate_mismatch(aioclient_mock: AiohttpClientMocker) -> None:
    """A different certificate is reported with the fingerprint it has."""
    mismatch = aiohttp.ServerFingerprintMismatch(
        bytes.fromhex(FINGERPRINT), bytes.fromhex(NEW_FINGERPRINT), HOST, 443
    )
    aioclient_mock.get(f"{HTTPS_BASE}/system/product", exc=mismatch)
    api = _api(aioclient_mock, use_https=True, fingerprint=FINGERPRINT)
    with pytest.raises(CertificateMismatch) as err:
        await api.get("/system/product")
    assert err.value.fingerprint == NEW_FINGERPRINT
    await api._session.close()
    # Without HTTPS a fingerprint is meaningless and ignored.
    plain = _api(aioclient_mock, fingerprint=FINGERPRINT)
    assert plain._ssl is None
    await plain._session.close()


async def test_existing_entry_pins_certificate(
    hass: HomeAssistant, mock_camera: Callable[..., None]
) -> None:
    """An HTTPS entry without a pin trusts the certificate seen first, without a reload."""
    mock_camera(scheme="https")
    entry = _https_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert entry.data[CONF_CERT_FINGERPRINT] == FINGERPRINT
    assert coordinator.api._ssl is not None
    assert coordinator.online
    # The pin is stored without reloading the entry.
    assert entry.runtime_data is coordinator
    assert entry.state is ConfigEntryState.LOADED


async def test_no_pinning_when_verifying(
    hass: HomeAssistant, mock_camera: Callable[..., None], mock_fingerprint: AsyncMock
) -> None:
    """With certificate verification on (CA-signed), nothing is pinned."""
    mock_camera(scheme="https")
    entry = _https_entry(**{CONF_VERIFY_SSL: True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert CONF_CERT_FINGERPRINT not in entry.data
    mock_fingerprint.assert_not_awaited()


async def test_changed_certificate_and_repair(
    hass: HomeAssistant,
    mock_camera: Callable[..., None],
    mock_fingerprint: AsyncMock,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A changed certificate keeps the camera offline until the user trusts it."""
    mock_camera(scheme="https")
    entry = _https_entry(**{CONF_CERT_FINGERPRINT: FINGERPRINT})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entities_before = {
        e.entity_id for e in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    }

    # The camera gets a new certificate (firmware update, reset, or an impostor).
    mismatch = aiohttp.ServerFingerprintMismatch(
        bytes.fromhex(FINGERPRINT), bytes.fromhex(NEW_FINGERPRINT), HOST, 443
    )
    aioclient_mock.clear_requests()
    mock_camera({"/system/product": {"status": 0, "exc": mismatch}}, scheme="https")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert not coordinator.online
    assert coordinator.offline_reason == "certificate_changed"
    assert hass.states.get("binary_sensor.pyxis_6k_connection").state == "off"
    issue_id = f"certificate_changed_{entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable
    assert any(
        r.levelno == logging.WARNING and "different TLS certificate" in r.getMessage()
        for r in caplog.records
    )

    # The repair shows both fingerprints and pins the new one.
    mock_fingerprint.return_value = NEW_FINGERPRINT
    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.FORM
    placeholders = result["description_placeholders"]
    assert placeholders["old_fingerprint"] == format_fingerprint(FINGERPRINT)
    assert placeholders["new_fingerprint"] == format_fingerprint(NEW_FINGERPRINT)

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_CERT_FINGERPRINT] == NEW_FINGERPRINT
    reload.assert_called_once_with(entry.entry_id)

    # After the reload the camera is back with the same entities (no "_2").
    aioclient_mock.clear_requests()
    mock_camera(scheme="https")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.online
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    entities_after = {
        e.entity_id for e in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    }
    # Same entity IDs as before: nothing was added as a duplicate ("..._2").
    assert entities_after == entities_before


async def test_repair_aborts(hass: HomeAssistant, mock_fingerprint: AsyncMock) -> None:
    """The repair aborts when the camera is unreachable or the entry is gone."""
    entry = _https_entry(**{CONF_CERT_FINGERPRINT: FINGERPRINT})
    entry.add_to_hass(hass)
    mock_fingerprint.side_effect = CannotConnect("off")
    flow = await async_create_fix_flow(hass, "x", {"entry_id": entry.entry_id})
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"

    flow = await async_create_fix_flow(hass, "x", {"entry_id": "missing"})
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["reason"] == "entry_removed"


async def test_reconfigure_pins_new_certificate(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_fingerprint: AsyncMock
) -> None:
    """Reconfigure trusts the certificate the camera presents now."""
    entry = _https_entry(**{CONF_CERT_FINGERPRINT: FINGERPRINT})
    entry.add_to_hass(hass)
    aioclient_mock.get(f"{HTTPS_BASE}/system/product", json={"productName": "PYXIS 6K"})
    mock_fingerprint.return_value = NEW_FINGERPRINT
    result = await entry.start_reconfigure_flow(hass)
    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], HTTPS_DATA)
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_CERT_FINGERPRINT] == NEW_FINGERPRINT


async def test_options_change_reloads(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_camera: Callable[..., None]
) -> None:
    """Changing the options reloads; the integration's own data updates do not."""
    mock_camera()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    first = config_entry.runtime_data

    hass.config_entries.async_update_entry(config_entry, data={**config_entry.data, "x": 1})
    await hass.async_block_till_done()
    assert config_entry.runtime_data is first

    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, "scan_interval": 20}
    )
    await hass.async_block_till_done()
    assert config_entry.runtime_data is not first
