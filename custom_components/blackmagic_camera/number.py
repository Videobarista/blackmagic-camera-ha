"""Numbers for the Blackmagic Camera integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
from typing import Any

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    MONITOR_BRIGHTNESS,
    P_CC_COLOR,
    P_CC_CONTRAST,
    P_CC_GAIN,
    P_CC_GAMMA,
    P_CC_LIFT,
    P_CC_LUMA,
    P_CC_OFFSET,
    P_FOCUS,
    P_FOCUS_ASSIST,
    P_FOCUS_DESC,
    P_IRIS,
    P_IRIS_DESC,
    P_SAFE_AREA_PERCENT,
    P_TINT,
    P_TINT_DESC,
    P_WHITE_BALANCE,
    P_WHITE_BALANCE_DESC,
    P_ZOOM,
    P_ZOOM_DESC,
    REJECTED_STATUS,
    audio_path,
    monitor_path,
)
from .coordinator import BlackmagicCameraConfigEntry
from .entity import BlackmagicCameraEntity
from .values import field, normalized, normalized_key, number

PARALLEL_UPDATES = 1

UNIT_DECIBEL = "dB"


@dataclass(frozen=True, kw_only=True)
class CameraNumberDescription(NumberEntityDescription):
    """Describes a camera number.

    ``value_fn`` reads the value from the property, ``write_fn`` turns a new
    value into the PUT payload and ``range_fn`` may return a (min, max)
    reported by the camera.
    """

    path: str
    value_fn: Callable[[BlackmagicCameraEntity], float | None]
    write_fn: Callable[[BlackmagicCameraEntity, float], Any]
    range_fn: Callable[[BlackmagicCameraEntity], tuple[float | None, float | None]] | None = None
    available_fn: Callable[[BlackmagicCameraEntity], bool] | None = None
    refresh: tuple[str, ...] = ()
    # Retry with the full current object merged in when the camera refuses a
    # partial update (documentation and firmware disagree on this).
    merge_on_reject: bool = False


def _field_value(key: str) -> Callable[[BlackmagicCameraEntity], float | None]:
    return lambda e: number(e.prop_field(key))


def _range(
    path: str, key: str
) -> Callable[[BlackmagicCameraEntity], tuple[float | None, float | None]]:
    """Range from a description property like {key: {min, max}}."""

    def _get(entity: BlackmagicCameraEntity) -> tuple[float | None, float | None]:
        limits = entity.prop_field(key, path)
        return number(field(limits, "min")), number(field(limits, "max"))

    return _get


def _controllable(path: str) -> Callable[[BlackmagicCameraEntity], bool]:
    """Available unless the lens description says it cannot be controlled."""
    return lambda e: e.prop_field("controllable", path) is not False


def _lens_normalized_write(entity: BlackmagicCameraEntity, value: float) -> dict[str, float]:
    return {normalized_key(entity.prop()): round(value / 100, 4)}


GLOBAL_NUMBERS: tuple[CameraNumberDescription, ...] = (
    CameraNumberDescription(
        key="white_balance",
        translation_key="white_balance",
        path=P_WHITE_BALANCE,
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        native_min_value=2500,
        native_max_value=10000,
        native_step=50,
        mode=NumberMode.SLIDER,
        value_fn=_field_value("whiteBalance"),
        write_fn=lambda _e, v: {"whiteBalance": int(v)},
        range_fn=_range(P_WHITE_BALANCE_DESC, "whiteBalance"),
    ),
    CameraNumberDescription(
        key="tint",
        translation_key="tint",
        path=P_TINT,
        native_min_value=-50,
        native_max_value=50,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=_field_value("whiteBalanceTint"),
        write_fn=lambda _e, v: {"whiteBalanceTint": int(v)},
        range_fn=_range(P_TINT_DESC, "whiteBalanceTint"),
    ),
    CameraNumberDescription(
        key="lens_iris",
        translation_key="lens_iris",
        path=P_IRIS,
        native_min_value=1.0,
        native_max_value=22.0,
        native_step=0.1,
        mode=NumberMode.BOX,
        value_fn=lambda e: (
            None if (v := number(e.prop_field("apertureStop"))) is None else round(v, 1)
        ),
        write_fn=lambda _e, v: {"apertureStop": round(v, 1)},
        range_fn=_range(P_IRIS_DESC, "apertureStop"),
        available_fn=_controllable(P_IRIS_DESC),
    ),
    CameraNumberDescription(
        key="lens_focus",
        translation_key="lens_focus",
        path=P_FOCUS,
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=0.1,
        mode=NumberMode.SLIDER,
        value_fn=lambda e: None if (n := normalized(e.prop())) is None else round(n * 100, 1),
        write_fn=_lens_normalized_write,
        available_fn=_controllable(P_FOCUS_DESC),
    ),
    CameraNumberDescription(
        key="lens_zoom",
        translation_key="lens_zoom",
        path=P_ZOOM,
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=0.1,
        mode=NumberMode.SLIDER,
        value_fn=lambda e: None if (n := normalized(e.prop())) is None else round(n * 100, 1),
        write_fn=_lens_normalized_write,
        available_fn=_controllable(P_ZOOM_DESC),
    ),
    CameraNumberDescription(
        key="focus_assist_intensity",
        translation_key="focus_assist_intensity",
        path=P_FOCUS_ASSIST,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=_field_value("intensity"),
        write_fn=lambda _e, v: {"intensity": int(v)},
        merge_on_reject=True,
    ),
    CameraNumberDescription(
        key="safe_area_percent",
        translation_key="safe_area_percent",
        path=P_SAFE_AREA_PERCENT,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.BOX,
        value_fn=_field_value("percent"),
        write_fn=lambda _e, v: {"percent": int(v)},
    ),
)

# Colour correction (DaVinci Resolve style primaries). Ranges come from the
# Blackmagic SDI camera control protocol.
# (path, component, key suffix, min, max, step)
_COLOR_FIELDS: tuple[tuple[str, str, str, float, float, float], ...] = (
    *((P_CC_LIFT, c, f"lift_{c}", -2.0, 2.0, 0.01) for c in ("red", "green", "blue", "luma")),
    *((P_CC_GAMMA, c, f"gamma_{c}", -4.0, 4.0, 0.01) for c in ("red", "green", "blue", "luma")),
    *((P_CC_GAIN, c, f"gain_{c}", 0.0, 16.0, 0.01) for c in ("red", "green", "blue", "luma")),
    *((P_CC_OFFSET, c, f"offset_{c}", -8.0, 8.0, 0.01) for c in ("red", "green", "blue", "luma")),
    (P_CC_CONTRAST, "pivot", "contrast_pivot", 0.0, 1.0, 0.01),
    (P_CC_CONTRAST, "adjust", "contrast", 0.0, 2.0, 0.01),
    (P_CC_COLOR, "hue", "hue", -1.0, 1.0, 0.01),
    (P_CC_COLOR, "saturation", "saturation", 0.0, 2.0, 0.01),
    (P_CC_LUMA, "lumaContribution", "luma_contribution", 0.0, 1.0, 0.01),
)

COLOR_NUMBERS: tuple[CameraNumberDescription, ...] = tuple(
    CameraNumberDescription(
        key=f"color_{suffix}",
        translation_key=f"color_{suffix}",
        path=path,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        native_min_value=low,
        native_max_value=high,
        native_step=step,
        mode=NumberMode.BOX,
        value_fn=_field_value(component),
        write_fn=lambda _e, v, component=component: {component: round(v, 3)},
    )
    for path, component, suffix, low, high, step in _COLOR_FIELDS
)


def _audio_gain_description(channel: int) -> CameraNumberDescription:
    description_path = audio_path(channel, "input/description")

    def _gain_range(entity: BlackmagicCameraEntity) -> tuple[float | None, float | None]:
        gain_range = field(entity.prop_field("description", description_path), "gainRange")
        low = field(gain_range, "Min")
        high = field(gain_range, "Max")
        return (
            number(low if low is not None else field(gain_range, "min")),
            number(high if high is not None else field(gain_range, "max")),
        )

    return CameraNumberDescription(
        key="audio_gain",
        translation_key="audio_gain",
        path=audio_path(channel, "level"),
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=UNIT_DECIBEL,
        native_min_value=-20,
        native_max_value=70,
        native_step=0.5,
        mode=NumberMode.SLIDER,
        value_fn=_field_value("gain"),
        write_fn=lambda _e, v: {"gain": round(v, 1)},
        range_fn=_gain_range,
    )


def _brightness_description(display: str) -> CameraNumberDescription:
    return CameraNumberDescription(
        key="monitor_brightness",
        translation_key="monitor_brightness",
        path=monitor_path(display, MONITOR_BRIGHTNESS),
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        value_fn=_field_value("brightness"),
        write_fn=lambda _e, v: {"brightness": int(v)},
        available_fn=lambda e: e.prop_field("adjustable") is not False,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlackmagicCameraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up camera numbers."""
    coordinator = entry.runtime_data
    caps = coordinator.capabilities
    entities: list[CameraNumber] = [
        CameraNumber(coordinator, description, description.path)
        for description in (*GLOBAL_NUMBERS, *COLOR_NUMBERS)
        if caps.supports(description.path)
    ]
    for channel in range(caps.audio_channels):
        description = _audio_gain_description(channel)
        if caps.supports(description.path):
            entities.append(
                CameraNumber(
                    coordinator,
                    description,
                    description.path,
                    unique_suffix=str(channel),
                    placeholders={"channel": str(channel + 1)},
                )
            )
    for display in caps.displays:
        description = _brightness_description(display)
        if caps.supports(description.path):
            entities.append(
                CameraNumber(
                    coordinator,
                    description,
                    description.path,
                    unique_suffix=display,
                    placeholders={"display": display},
                )
            )
    async_add_entities(entities)


class CameraNumber(BlackmagicCameraEntity, NumberEntity):
    """A number backed by a camera property."""

    entity_description: CameraNumberDescription

    @property
    def available(self) -> bool:
        """Return True when the value can be changed."""
        if not super().available or self.native_value is None:
            return False
        if self.entity_description.range_fn is not None:
            low, high = self.entity_description.range_fn(self)
            if low is not None and high is not None and low >= high:
                # e.g. a line input whose gain range is 0..0 is not adjustable
                return False
        check = self.entity_description.available_fn
        return check is None or check(self)

    def _limits(self) -> tuple[float | None, float | None]:
        """Range reported by the camera, snapped inwards to the step.

        Cameras report values like 2.799999952316284; snapping keeps values
        the frontend sends (2.8) inside the range.
        """
        if self.entity_description.range_fn is None:
            return None, None
        low, high = self.entity_description.range_fn(self)
        step = self.entity_description.native_step or 1
        decimals = len(f"{step:g}".partition(".")[2])
        if low is not None:
            low = round(math.ceil(low / step - 1e-6) * step, decimals)
        if high is not None:
            high = round(math.floor(high / step + 1e-6) * step, decimals)
        if low is not None and high is not None and low >= high:
            return None, None
        return low, high

    @property
    def native_min_value(self) -> float:
        """Return the minimum value."""
        low, _ = self._limits()
        return low if low is not None else super().native_min_value

    @property
    def native_max_value(self) -> float:
        """Return the maximum value."""
        _, high = self._limits()
        return high if high is not None else super().native_max_value

    @property
    def native_value(self) -> float | None:
        """Return the current value (whole numbers for whole number steps)."""
        value = self.entity_description.value_fn(self)
        step = self.entity_description.native_step
        if value is not None and step is not None and step >= 1 and float(value).is_integer():
            return int(value)
        return value

    async def async_set_native_value(self, value: float) -> None:
        """Write a new value to the camera."""
        description = self.entity_description
        payload = description.write_fn(self, value)
        fallback = None
        if description.merge_on_reject and isinstance(current := self.prop(), dict):
            fallback = ("put", description.path, {**current, **payload})
        await self.coordinator.async_command(
            "put",
            description.path,
            payload,
            refresh=description.refresh or (description.path,),
            fallback=fallback,
            fallback_status=REJECTED_STATUS,
        )
