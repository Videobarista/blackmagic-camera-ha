"""Tests for controlling the camera through its entities."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.number import ATTR_VALUE, DOMAIN as NUMBER_DOMAIN, SERVICE_SET_VALUE
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .conftest import BASE, calls


@pytest.fixture
async def setup_camera(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_camera: Callable[..., None],
) -> MockConfigEntry:
    """Set up the integration with the recorded PYXIS answers."""
    mock_camera()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def _enable(hass: HomeAssistant, entry: MockConfigEntry, entity_id: str) -> None:
    """Enable an entity that is disabled by default."""
    er.async_get(hass).async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def _call(
    hass: HomeAssistant, domain: str, service: str, entity_id: str, **data: Any
) -> None:
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )


@pytest.mark.usefixtures("setup_camera")
async def test_record(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The record switch starts and stops recording."""
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, "switch.pyxis_6k_record")
    assert len(calls(aioclient_mock, "post", "/transports/0/record")) == 1
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, "switch.pyxis_6k_record")
    assert len(calls(aioclient_mock, "post", "/transports/0/stop")) == 1


async def test_record_falls_back_on_old_firmware(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_camera: Callable[..., None],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Firmware without POST /transports/0/record uses the older PUT call."""
    aioclient_mock.post(f"{BASE}/transports/0/record", status=405)
    mock_camera()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, "switch.pyxis_6k_record")
    assert calls(aioclient_mock, "put", "/transports/0/record") == [{"recording": True}]


@pytest.mark.usefixtures("setup_camera")
async def test_switches(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Boolean features are written as {"enabled": bool}."""
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, "switch.pyxis_6k_color_bars")
    assert calls(aioclient_mock, "put", "/camera/colorBars") == [{"enabled": True}]
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, "switch.pyxis_6k_monitor_lcd_zebra")
    assert calls(aioclient_mock, "put", "/monitoring/LCD/zebra") == [{"enabled": False}]
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, "switch.pyxis_6k_auto_prerecord")
    assert calls(aioclient_mock, "put", "/transports/0/prerecord/auto") == [{"autoEnabled": True}]
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, "switch.pyxis_6k_audio_1_phantom_power")
    assert calls(aioclient_mock, "put", "/audio/channel/0/phantomPower") == [{"enabled": False}]


async def test_stream_switch(
    hass: HomeAssistant, setup_camera: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """The optional stream switch starts and stops the livestream."""
    await _enable(hass, setup_camera, "switch.pyxis_6k_stream")
    assert hass.states.get("switch.pyxis_6k_stream").state == "off"
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, "switch.pyxis_6k_stream")
    assert len(calls(aioclient_mock, "put", "/livestreams/0/start")) == 1
    await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, "switch.pyxis_6k_stream")
    assert len(calls(aioclient_mock, "put", "/livestreams/0/stop")) == 1


@pytest.mark.usefixtures("setup_camera")
@pytest.mark.parametrize(
    ("entity_id", "option", "path", "payload"),
    [
        ("select.pyxis_6k_iso", "800", "/video/iso", {"iso": 800}),
        ("select.pyxis_6k_gain", "+6 dB", "/video/gain", {"gain": 6}),
        ("select.pyxis_6k_shutter", "1/100", "/video/shutter", {"shutterSpeed": 100}),
        ("select.pyxis_6k_tally", "program", "/camera/tallyStatus", {"status": "Program"}),
        ("select.pyxis_6k_auto_exposure", "one_shot", "/video/autoExposure", {"mode": "OneShot"}),
        ("select.pyxis_6k_preset", "Basis", "/presets/active", {"preset": "Basis.cset"}),
        ("select.pyxis_6k_dynamic_range", "film", "/system/dynamicRange", {"dynamicRange": "Film"}),
        ("select.pyxis_6k_active_media", "3: USB-DISK", "/media/active", {"workingsetIndex": 2}),
        (
            "select.pyxis_6k_prerecord_duration",
            "30 s",
            "/transports/0/prerecord/maxDuration",
            {"maxDuration": 30},
        ),
        (
            "select.pyxis_6k_lens_autofocus_mode",
            "track_face",
            "/lens/focus/autoFocus",
            {"mode": "TrackFace"},
        ),
        (
            "select.pyxis_6k_focus_assist_color",
            "green",
            "/monitoring/focusAssist",
            {"color": "Green", "intensity": 0, "mode": "Peak"},
        ),
        (
            "select.pyxis_6k_audio_1_input",
            "XLR1 - Line",
            "/audio/channel/0/input",
            {"input": "XLR1 - Line"},
        ),
        (
            "select.pyxis_6k_shutter_measurement",
            "shutter_angle",
            "/video/shutter/measurement",
            {"measurement": "ShutterAngle"},
        ),
    ],
)
async def test_selects(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entity_id: str,
    option: str,
    path: str,
    payload: dict[str, Any],
) -> None:
    """Selecting an option writes the camera value."""
    await _call(hass, SELECT_DOMAIN, SERVICE_SELECT_OPTION, entity_id, **{ATTR_OPTION: option})
    assert calls(aioclient_mock, "put", path) == [payload]


async def test_shutter_angle_mode(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_camera: Callable[..., None],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """In angle mode options are degrees while the camera gets value x 100."""
    mock_camera(
        {
            "/video/shutter/measurement": {"status": 200, "json": {"measurement": "ShutterAngle"}},
            "/video/shutter": {
                "status": 200,
                "json": {"continuousShutterAutoExposure": False, "shutterAngle": 18000},
            },
        }
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("select.pyxis_6k_shutter")
    assert state.state == "180°"
    assert "172.8°" in state.attributes["options"]
    await _call(hass, SELECT_DOMAIN, SERVICE_SELECT_OPTION, "select.pyxis_6k_shutter", option="90°")
    assert calls(aioclient_mock, "put", "/video/shutter") == [{"shutterAngle": 9000}]


@pytest.mark.usefixtures("setup_camera")
async def test_select_invalid_option(hass: HomeAssistant) -> None:
    """Options that are not offered are refused."""
    with pytest.raises(ServiceValidationError):
        await _call(hass, SELECT_DOMAIN, SERVICE_SELECT_OPTION, "select.pyxis_6k_iso", option="123")


@pytest.mark.usefixtures("setup_camera")
@pytest.mark.parametrize(
    ("entity_id", "value", "path", "payload"),
    [
        ("number.pyxis_6k_white_balance", 3200, "/video/whiteBalance", {"whiteBalance": 3200}),
        ("number.pyxis_6k_tint", -5, "/video/whiteBalanceTint", {"whiteBalanceTint": -5}),
        ("number.pyxis_6k_lens_iris", 4.0, "/lens/iris", {"apertureStop": 4.0}),
        ("number.pyxis_6k_lens_focus", 50, "/lens/focus", {"normalised": 0.5}),
        ("number.pyxis_6k_audio_1_gain", 20, "/audio/channel/0/level", {"gain": 20.0}),
        (
            "number.pyxis_6k_focus_assist_intensity",
            75,
            "/monitoring/focusAssist",
            {"color": "Red", "intensity": 75, "mode": "Peak"},
        ),
    ],
)
async def test_numbers(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entity_id: str,
    value: float,
    path: str,
    payload: dict[str, Any],
) -> None:
    """Setting a number writes the camera value."""
    await _call(hass, NUMBER_DOMAIN, SERVICE_SET_VALUE, entity_id, **{ATTR_VALUE: value})
    assert calls(aioclient_mock, "put", path) == [payload]


async def test_number_ranges(hass: HomeAssistant, setup_camera: MockConfigEntry) -> None:
    """Ranges reported by the camera are used."""
    iris = hass.states.get("number.pyxis_6k_lens_iris")
    assert iris.attributes["min"] == pytest.approx(2.8, abs=0.01)
    assert iris.attributes["max"] == 22.0
    gain = hass.states.get("number.pyxis_6k_audio_1_gain")
    assert (gain.attributes["min"], gain.attributes["max"]) == (0.0, 60.0)


async def test_color_correction(
    hass: HomeAssistant, setup_camera: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Colour correction values and the reset button."""
    await _enable(hass, setup_camera, "number.pyxis_6k_color_lift_red")
    await _call(
        hass, NUMBER_DOMAIN, SERVICE_SET_VALUE, "number.pyxis_6k_color_lift_red", value=0.25
    )
    assert calls(aioclient_mock, "put", "/colorCorrection/lift") == [{"red": 0.25}]

    await _enable(hass, setup_camera, "button.pyxis_6k_color_correction_reset")
    aioclient_mock.mock_calls.clear()
    await _call(hass, BUTTON_DOMAIN, SERVICE_PRESS, "button.pyxis_6k_color_correction_reset")
    assert calls(aioclient_mock, "put", "/colorCorrection/gain") == [
        {"red": 1.0, "green": 1.0, "blue": 1.0, "luma": 1.0}
    ]


@pytest.mark.usefixtures("setup_camera")
@pytest.mark.parametrize(
    ("entity_id", "method", "path", "payload"),
    [
        ("button.pyxis_6k_capture_still", "post", "/transports/0/doStillCapture", None),
        ("button.pyxis_6k_auto_white_balance", "put", "/video/whiteBalance/doAuto", None),
        ("button.pyxis_6k_lens_autofocus", "put", "/lens/focus/doAutoFocus", None),
        ("button.pyxis_6k_start_prerecord", "post", "/transports/0/prerecord", None),
        (
            "button.pyxis_6k_function_button_f1",
            "put",
            "/panel/devices/f1/state",
            {"action": "press"},
        ),
    ],
)
async def test_buttons(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entity_id: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> None:
    """Buttons trigger camera actions."""
    await _call(hass, BUTTON_DOMAIN, SERVICE_PRESS, entity_id)
    assert calls(aioclient_mock, method, path) == [payload]


async def test_command_errors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_camera: Callable[..., None],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Rejected or failed commands raise a readable error."""
    aioclient_mock.put(f"{BASE}/video/iso", status=403, json={"error": "ISO is locked"})
    aioclient_mock.put(f"{BASE}/camera/colorBars", exc=TimeoutError())
    aioclient_mock.put(f"{BASE}/camera/tallyStatus", status=401)
    mock_camera()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="ISO is locked"):
        await _call(hass, SELECT_DOMAIN, SERVICE_SELECT_OPTION, "select.pyxis_6k_iso", option="800")
    with pytest.raises(HomeAssistantError):
        await _call(hass, SWITCH_DOMAIN, SERVICE_TURN_ON, "switch.pyxis_6k_color_bars")
    with pytest.raises(HomeAssistantError):
        await _call(
            hass, SELECT_DOMAIN, SERVICE_SELECT_OPTION, "select.pyxis_6k_tally", option="preview"
        )


async def test_sensor_attributes(hass: HomeAssistant, setup_camera: MockConfigEntry) -> None:
    """Media slots and the battery expose details as attributes."""
    slot = hass.states.get("sensor.pyxis_6k_media_slot_3")
    assert slot.attributes["volume"] == "USB-DISK"
    assert slot.attributes["active"] is False
    assert slot.attributes["total_gb"] == 500.1
    battery = hass.states.get("sensor.pyxis_6k_battery")
    assert battery.attributes["battery_1_voltage"] == 16.3
    remaining = hass.states.get("sensor.pyxis_6k_remaining_record_time")
    assert remaining.attributes["volume"] == "A006"
    assert float(remaining.state) == pytest.approx(2579 / 60, abs=0.01)
