"""Tests for setting up the Blackmagic Camera integration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.blackmagic_camera.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import BASE


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_unload(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_camera: Callable[..., None],
    hass_storage: dict[str, Any],
) -> None:
    """The camera is set up with entities for the features it reports."""
    mock_camera()
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), config_entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert (DOMAIN, config_entry.entry_id) in device.identifiers
    assert device.manufacturer == "Blackmagic Design"
    assert device.model == "PYXIS 6K"
    assert device.sw_version == "10.2"

    # Features the PYXIS does not have get no entity.
    entities = er.async_entries_for_config_entry(er.async_get(hass), config_entry.entry_id)
    keys = {entity.unique_id.removeprefix(f"{config_entry.entry_id}_") for entity in entities}
    assert "nd_filter" not in keys
    assert "detail_sharpening" not in keys
    assert "video_format" not in keys
    assert {"record", "iso", "shutter", "battery", "capture_still", "monitor_zebra_LCD"} <= keys
    assert "monitor_zebra_Stream" not in keys
    assert {"panel_button_f1", "panel_button_f2", "panel_button_f3"} <= keys
    assert {"media_slot_0", "media_slot_1", "media_slot_2"} <= keys

    # Detected features are cached for when the camera is offline at startup.
    assert hass_storage[f"{DOMAIN}.{config_entry.entry_id}"]["data"]["product"]["productName"] == (
        "PYXIS 6K"
    )

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_states(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_camera: Callable[..., None]
) -> None:
    """Values from the camera are translated into entity states."""
    mock_camera()
    await _setup(hass, config_entry)

    assert hass.states.get("switch.pyxis_6k_record").state == STATE_OFF
    assert hass.states.get("select.pyxis_6k_iso").state == "400"
    assert hass.states.get("select.pyxis_6k_gain").state == "0 dB"
    assert hass.states.get("select.pyxis_6k_shutter").state == "1/50"
    assert hass.states.get("select.pyxis_6k_tally").state == "none"
    assert hass.states.get("select.pyxis_6k_preset").state == "Default"
    assert hass.states.get("select.pyxis_6k_dynamic_range").state == "extended_video"
    assert hass.states.get("select.pyxis_6k_active_media").state == "1: A006"
    assert hass.states.get("select.pyxis_6k_audio_1_input").state == "XLR1 - Mic"
    assert hass.states.get("number.pyxis_6k_white_balance").state == "5600"
    assert hass.states.get("number.pyxis_6k_tint").state == "10"
    assert hass.states.get("number.pyxis_6k_lens_iris").state == "2.8"
    assert hass.states.get("sensor.pyxis_6k_battery").state == "100"
    assert hass.states.get("sensor.pyxis_6k_power_source").state == "ac"
    assert hass.states.get("sensor.pyxis_6k_lens_focal_length").state == "24"
    assert hass.states.get("sensor.pyxis_6k_codec").state == "BRaw:Q3"
    assert hass.states.get("sensor.pyxis_6k_recording_resolution").state == "6048x3408"
    assert float(hass.states.get("sensor.pyxis_6k_media_slot_1").state) == 99.2
    assert hass.states.get("binary_sensor.pyxis_6k_connection").state == STATE_ON

    # Zoom of this lens is not controllable and the 3.5mm line input has no
    # phantom power or adjustable gain.
    assert hass.states.get("number.pyxis_6k_lens_zoom").state == STATE_UNAVAILABLE
    assert hass.states.get("switch.pyxis_6k_audio_1_phantom_power").state == STATE_ON
    assert hass.states.get("switch.pyxis_6k_audio_2_phantom_power").state == STATE_UNAVAILABLE
    assert hass.states.get("number.pyxis_6k_audio_2_gain").state == STATE_UNAVAILABLE

    # Shutter angle values are reported multiplied by 100.
    select = hass.states.get("select.pyxis_6k_shutter")
    assert "1/50" in select.attributes["options"]


async def test_setup_while_camera_offline(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A camera that is switched off does not make the setup fail."""
    aioclient_mock.get(f"{BASE}/system/product", exc=aiohttp.ClientConnectionError())
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("binary_sensor.pyxis_6k_connection").state == STATE_OFF
    assert hass.states.get("switch.pyxis_6k_record").state == STATE_UNAVAILABLE


async def test_offline_start_uses_cache_and_recovers(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_camera: Callable[..., None],
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Cached features are used while offline; entities return with the camera."""
    mock_camera()
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/system/product", exc=aiohttp.ClientConnectionError())
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("select.pyxis_6k_iso").state == STATE_UNAVAILABLE
    assert hass.states.get("binary_sensor.pyxis_6k_connection").state == STATE_OFF

    aioclient_mock.clear_requests()
    mock_camera()
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("select.pyxis_6k_iso").state == "400"
    assert hass.states.get("binary_sensor.pyxis_6k_connection").state == STATE_ON


async def test_camera_goes_offline(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_camera: Callable[..., None],
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Entities become unavailable when the camera stops answering."""
    mock_camera()
    await _setup(hass, config_entry)
    assert hass.states.get("select.pyxis_6k_iso").state == "400"

    aioclient_mock.clear_requests()
    mock_camera(
        {
            path: {"status": 0, "exc": aiohttp.ClientConnectionError()}
            for path in hass.config_entries.async_get_entry(
                config_entry.entry_id
            ).runtime_data.capabilities.supported
        }
    )
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("select.pyxis_6k_iso").state == STATE_UNAVAILABLE
    assert hass.states.get("binary_sensor.pyxis_6k_connection").state == STATE_OFF


async def test_invalid_auth_fails_setup(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Rejected credentials stop the setup with an error instead of retrying."""
    aioclient_mock.get(f"{BASE}/system/product", status=401)
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_remove_entry_clears_cache(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_camera: Callable[..., None],
    hass_storage: dict[str, Any],
) -> None:
    """Removing the entry removes the cached features."""
    mock_camera()
    await _setup(hass, config_entry)
    key = f"{DOMAIN}.{config_entry.entry_id}"
    assert key in hass_storage
    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_options_update_reloads(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_camera: Callable[..., None]
) -> None:
    """Changing the options reloads the entry."""
    mock_camera()
    await _setup(hass, config_entry)
    hass.config_entries.async_update_entry(
        config_entry, options={"scan_interval": 20, "use_websocket": False}
    )
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data.update_interval == timedelta(seconds=20)
