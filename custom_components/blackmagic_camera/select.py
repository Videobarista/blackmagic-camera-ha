"""Selects for the Blackmagic Camera integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DOMAIN,
    P_AUTO_EXPOSURE,
    P_AUTOFOCUS,
    P_AUTOFOCUS_DESC,
    P_DETAIL_SHARPENING_LEVEL,
    P_DYNAMIC_RANGE,
    P_FOCUS_ASSIST,
    P_FRAME_GUIDE_PRESETS,
    P_FRAME_GUIDE_RATIO,
    P_GAIN,
    P_ISO,
    P_MEDIA_ACTIVE,
    P_ND_FILTER,
    P_PRERECORD_MAX,
    P_PRERECORD_SUPPORTED,
    P_PRESET_ACTIVE,
    P_PRESETS,
    P_SHUTTER,
    P_SHUTTER_MEASUREMENT,
    P_SUPPORTED_DYNAMIC_RANGES,
    P_SUPPORTED_GAINS,
    P_SUPPORTED_ISOS,
    P_SUPPORTED_ND,
    P_SUPPORTED_SHUTTERS,
    P_TALLY,
    P_WORKINGSET,
    REJECTED_STATUS,
    audio_path,
)
from .coordinator import BlackmagicCameraConfigEntry
from .entity import BlackmagicCameraEntity
from .values import (
    angle_to_degrees,
    field,
    fmt_angle,
    fmt_number,
    fmt_speed,
    number,
    shutter_angle_scaled,
    slot_label,
    snake,
    workingset_slots,
)

PARALLEL_UPDATES = 1

# Mapping of option label -> value to send.
type OptionMap = dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class CameraSelectDescription(SelectEntityDescription):
    """Describes a camera select.

    ``options_fn`` returns the option labels mapped to the value that is
    written; ``current_fn`` returns the label of the current value and
    ``write_fn`` turns a value into (path, payload, paths to read back).
    """

    path: str
    options_fn: Callable[[BlackmagicCameraEntity], OptionMap]
    current_fn: Callable[[BlackmagicCameraEntity], str | None]
    write_fn: Callable[[BlackmagicCameraEntity, Any], tuple[str, Any, tuple[str, ...]]]
    available_fn: Callable[[BlackmagicCameraEntity], bool] | None = None
    # Retry with the full current object merged in when the camera refuses a
    # partial update (documentation and firmware disagree on this).
    merge_on_reject: bool = False


# ---------------------------------------------------------------------------
# Fixed vocabularies (translated in the UI)
# ---------------------------------------------------------------------------
def _enum_options(values: tuple[str, ...]) -> Callable[[BlackmagicCameraEntity], OptionMap]:
    mapping = {snake(v): v for v in values}
    return lambda _e: mapping


def _enum_current(state_field: str) -> Callable[[BlackmagicCameraEntity], str | None]:
    def _current(entity: BlackmagicCameraEntity) -> str | None:
        value = entity.prop_field(state_field)
        return snake(value) if isinstance(value, str) else None

    return _current


def _simple_write(path: str, key: str) -> Callable[..., tuple[str, Any, tuple[str, ...]]]:
    return lambda _e, value: (path, {key: value}, (path,))


TALLY = ("None", "Preview", "Program")
AUTO_EXPOSURE = ("Off", "Continuous", "OneShot")
SHUTTER_MEASUREMENT = ("ShutterAngle", "ShutterSpeed")
SHARPENING_LEVEL = ("Low", "Medium", "High")
DYNAMIC_RANGE = ("Video", "Film", "Extended Video", "PQ", "HLG", "DaVinci")
AUTOFOCUS_MODE = ("OneShot", "Continuous", "TrackObject", "TrackFace")
FOCUS_ASSIST_MODE = ("Peak", "ColoredLines")
FOCUS_ASSIST_COLOR = ("Red", "Green", "Blue", "White", "Black")


def _filtered_enum(
    values: tuple[str, ...], supported_path: str, supported_key: str
) -> Callable[[BlackmagicCameraEntity], OptionMap]:
    """Enum options limited to what the camera reports as supported."""

    def _options(entity: BlackmagicCameraEntity) -> OptionMap:
        supported = entity.prop_field(supported_key, supported_path)
        allowed = (
            [v for v in supported if isinstance(v, str)] if isinstance(supported, list) else []
        )
        known = [v for v in values if not allowed or v in allowed]
        known += [v for v in allowed if v not in values]
        return {snake(v): v for v in known}

    return _options


def _auto_exposure_write(
    entity: BlackmagicCameraEntity, value: Any
) -> tuple[str, Any, tuple[str, ...]]:
    payload: dict[str, Any] = {"mode": value}
    current_type = entity.prop_field("type")
    if isinstance(current_type, str) and current_type:
        payload["type"] = current_type
    return P_AUTO_EXPOSURE, payload, (P_AUTO_EXPOSURE, P_SHUTTER)


def _focus_assist_write(key: str) -> Callable[..., tuple[str, Any, tuple[str, ...]]]:
    """Write one global focus assist field (the full object is the fallback)."""
    return lambda _e, value: (P_FOCUS_ASSIST, {key: value}, (P_FOCUS_ASSIST,))


# ---------------------------------------------------------------------------
# Value lists reported by the camera
# ---------------------------------------------------------------------------
def _list_options(
    supported_path: str, supported_key: str | None, fmt: Callable[[Any], str]
) -> Callable[[BlackmagicCameraEntity], OptionMap]:
    """Options from a list property (or a list field of a property)."""

    def _options(entity: BlackmagicCameraEntity) -> OptionMap:
        if supported_key is None:
            values = entity.prop(supported_path)
        else:
            values = entity.prop_field(supported_key, supported_path)
        if not isinstance(values, list):
            return {}
        return {fmt(v): v for v in values if number(v) is not None}

    return _options


def _list_current(
    state_key: str, fmt: Callable[[Any], str]
) -> Callable[[BlackmagicCameraEntity], str | None]:
    def _current(entity: BlackmagicCameraEntity) -> str | None:
        value = entity.prop_field(state_key)
        return fmt(value) if number(value) is not None else None

    return _current


def _fmt_gain(value: Any) -> str:
    return f"{int(value):+d} dB" if int(value) else "0 dB"


def _fmt_nd(value: Any) -> str:
    return "Clear" if float(value) == 0 else f"{fmt_number(float(value))} stops"


def _fmt_seconds(value: Any) -> str:
    return f"{int(value)} s"


# Shutter: options depend on the measurement mode (angle or speed).
def _shutter_mode(entity: BlackmagicCameraEntity) -> str:
    mode = entity.prop_field("measurement", P_SHUTTER_MEASUREMENT)
    if isinstance(mode, str):
        return mode
    return "ShutterAngle" if entity.prop_field("shutterAngle") is not None else "ShutterSpeed"


def _shutter_scaled(entity: BlackmagicCameraEntity) -> bool:
    angles = entity.prop_field("shutterAngles", P_SUPPORTED_SHUTTERS)
    if isinstance(angles, list) and angles:
        return shutter_angle_scaled(angles)
    current = number(entity.prop_field("shutterAngle"))
    return current is not None and current > 360


def _shutter_options(entity: BlackmagicCameraEntity) -> OptionMap:
    if _shutter_mode(entity) == "ShutterAngle":
        angles = entity.prop_field("shutterAngles", P_SUPPORTED_SHUTTERS)
        scaled = _shutter_scaled(entity)
        values = angles if isinstance(angles, list) else []
        return {
            fmt_angle(angle_to_degrees(float(v), scaled)): v
            for v in values
            if number(v) is not None
        }
    speeds = entity.prop_field("shutterSpeeds", P_SUPPORTED_SHUTTERS)
    values = speeds if isinstance(speeds, list) else []
    return {fmt_speed(v): v for v in values if number(v) is not None}


def _shutter_current(entity: BlackmagicCameraEntity) -> str | None:
    if _shutter_mode(entity) == "ShutterAngle":
        angle = number(entity.prop_field("shutterAngle"))
        if angle is None:
            return None
        return fmt_angle(angle_to_degrees(angle, _shutter_scaled(entity)))
    speed = number(entity.prop_field("shutterSpeed"))
    return fmt_speed(speed) if speed else None


def _shutter_write(entity: BlackmagicCameraEntity, value: Any) -> tuple[str, Any, tuple[str, ...]]:
    key = "shutterAngle" if _shutter_mode(entity) == "ShutterAngle" else "shutterSpeed"
    return P_SHUTTER, {key: value}, (P_SHUTTER,)


# Presets: "name.cset" files plus the camera default.
def _preset_label(value: str) -> str:
    if value.lower() == "default":
        return "Default"
    return value.removesuffix(".cset")


def _preset_options(entity: BlackmagicCameraEntity) -> OptionMap:
    presets = entity.prop_field("presets", P_PRESETS)
    files = [p for p in presets if isinstance(p, str)] if isinstance(presets, list) else []
    options: OptionMap = {"Default": "default"}
    for name in files:
        file_name = name if name.endswith(".cset") else f"{name}.cset"
        options[_preset_label(file_name)] = file_name
    return options


def _preset_current(entity: BlackmagicCameraEntity) -> str | None:
    value = entity.prop_field("preset")
    return _preset_label(value) if isinstance(value, str) else None


# Active media: the populated working set slots.
def _media_options(entity: BlackmagicCameraEntity) -> OptionMap:
    options: OptionMap = {}
    for position, slot in enumerate(workingset_slots(entity.prop(P_WORKINGSET))):
        if slot is None:
            continue
        index = slot.get("index") if isinstance(slot.get("index"), int) else position
        options[slot_label(index, slot)] = index
    return options


def _media_current(entity: BlackmagicCameraEntity) -> str | None:
    index = entity.prop_field("workingsetIndex")
    for label, value in _media_options(entity).items():
        if value == index:
            return label
    return None


# Frame guide ratio: free text values with a preset list.
def _frame_guide_options(entity: BlackmagicCameraEntity) -> OptionMap:
    presets = entity.prop_field("presets", P_FRAME_GUIDE_PRESETS)
    options: OptionMap = {str(p): str(p) for p in presets} if isinstance(presets, list) else {}
    current = entity.prop_field("ratio")
    if isinstance(current, str) and current not in options:
        options[current] = current
    return options


def _string_current(key: str) -> Callable[[BlackmagicCameraEntity], str | None]:
    def _current(entity: BlackmagicCameraEntity) -> str | None:
        value = entity.prop_field(key)
        return value if isinstance(value, str) else None

    return _current


GLOBAL_SELECTS: tuple[CameraSelectDescription, ...] = (
    CameraSelectDescription(
        key="tally",
        translation_key="tally",
        path=P_TALLY,
        options_fn=_enum_options(TALLY),
        current_fn=_enum_current("status"),
        write_fn=_simple_write(P_TALLY, "status"),
    ),
    CameraSelectDescription(
        key="iso",
        translation_key="iso",
        path=P_ISO,
        options_fn=_list_options(P_SUPPORTED_ISOS, "supportedISOs", lambda v: str(int(v))),
        current_fn=_list_current("iso", lambda v: str(int(v))),
        write_fn=_simple_write(P_ISO, "iso"),
    ),
    CameraSelectDescription(
        key="gain",
        translation_key="gain",
        path=P_GAIN,
        options_fn=_list_options(P_SUPPORTED_GAINS, "supportedGains", _fmt_gain),
        current_fn=_list_current("gain", _fmt_gain),
        write_fn=_simple_write(P_GAIN, "gain"),
    ),
    CameraSelectDescription(
        key="shutter",
        translation_key="shutter",
        path=P_SHUTTER,
        options_fn=_shutter_options,
        current_fn=_shutter_current,
        write_fn=_shutter_write,
    ),
    CameraSelectDescription(
        key="nd_filter",
        translation_key="nd_filter",
        path=P_ND_FILTER,
        options_fn=_list_options(P_SUPPORTED_ND, "supportedStops", _fmt_nd),
        current_fn=_list_current("stop", _fmt_nd),
        write_fn=_simple_write(P_ND_FILTER, "stop"),
    ),
    CameraSelectDescription(
        key="auto_exposure",
        translation_key="auto_exposure",
        path=P_AUTO_EXPOSURE,
        options_fn=_enum_options(AUTO_EXPOSURE),
        current_fn=_enum_current("mode"),
        write_fn=_auto_exposure_write,
    ),
    CameraSelectDescription(
        key="preset",
        translation_key="preset",
        path=P_PRESET_ACTIVE,
        options_fn=_preset_options,
        current_fn=_preset_current,
        write_fn=_simple_write(P_PRESET_ACTIVE, "preset"),
    ),
    CameraSelectDescription(
        key="shutter_measurement",
        translation_key="shutter_measurement",
        path=P_SHUTTER_MEASUREMENT,
        entity_category=EntityCategory.CONFIG,
        options_fn=_enum_options(SHUTTER_MEASUREMENT),
        current_fn=_enum_current("measurement"),
        write_fn=lambda _e, value: (
            P_SHUTTER_MEASUREMENT,
            {"measurement": value},
            (P_SHUTTER_MEASUREMENT, P_SHUTTER, P_SUPPORTED_SHUTTERS),
        ),
    ),
    CameraSelectDescription(
        key="detail_sharpening_level",
        translation_key="detail_sharpening_level",
        path=P_DETAIL_SHARPENING_LEVEL,
        entity_category=EntityCategory.CONFIG,
        options_fn=_enum_options(SHARPENING_LEVEL),
        current_fn=_enum_current("level"),
        write_fn=_simple_write(P_DETAIL_SHARPENING_LEVEL, "level"),
    ),
    CameraSelectDescription(
        key="dynamic_range",
        translation_key="dynamic_range",
        path=P_DYNAMIC_RANGE,
        entity_category=EntityCategory.CONFIG,
        options_fn=_filtered_enum(
            DYNAMIC_RANGE, P_SUPPORTED_DYNAMIC_RANGES, "supportedDynamicRanges"
        ),
        current_fn=_enum_current("dynamicRange"),
        write_fn=_simple_write(P_DYNAMIC_RANGE, "dynamicRange"),
    ),
    CameraSelectDescription(
        key="active_media",
        translation_key="active_media",
        path=P_MEDIA_ACTIVE,
        entity_category=EntityCategory.CONFIG,
        options_fn=_media_options,
        current_fn=_media_current,
        write_fn=lambda _e, value: (
            P_MEDIA_ACTIVE,
            {"workingsetIndex": value},
            (P_MEDIA_ACTIVE, P_WORKINGSET),
        ),
    ),
    CameraSelectDescription(
        key="prerecord_duration",
        translation_key="prerecord_duration",
        path=P_PRERECORD_MAX,
        entity_category=EntityCategory.CONFIG,
        options_fn=_list_options(P_PRERECORD_SUPPORTED, None, _fmt_seconds),
        current_fn=_list_current("maxDuration", _fmt_seconds),
        write_fn=_simple_write(P_PRERECORD_MAX, "maxDuration"),
    ),
    CameraSelectDescription(
        key="lens_autofocus_mode",
        translation_key="lens_autofocus_mode",
        path=P_AUTOFOCUS,
        entity_category=EntityCategory.CONFIG,
        options_fn=_filtered_enum(AUTOFOCUS_MODE, P_AUTOFOCUS_DESC, "supportedModes"),
        current_fn=_enum_current("mode"),
        write_fn=_simple_write(P_AUTOFOCUS, "mode"),
        available_fn=lambda e: e.prop_field("supported") is not False,
    ),
    CameraSelectDescription(
        key="focus_assist_mode",
        translation_key="focus_assist_mode",
        path=P_FOCUS_ASSIST,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        options_fn=_enum_options(FOCUS_ASSIST_MODE),
        current_fn=_enum_current("mode"),
        write_fn=_focus_assist_write("mode"),
        merge_on_reject=True,
    ),
    CameraSelectDescription(
        key="focus_assist_color",
        translation_key="focus_assist_color",
        path=P_FOCUS_ASSIST,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        options_fn=_enum_options(FOCUS_ASSIST_COLOR),
        current_fn=_enum_current("color"),
        write_fn=_focus_assist_write("color"),
        merge_on_reject=True,
    ),
    CameraSelectDescription(
        key="frame_guide_ratio",
        translation_key="frame_guide_ratio",
        path=P_FRAME_GUIDE_RATIO,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        options_fn=_frame_guide_options,
        current_fn=_string_current("ratio"),
        write_fn=_simple_write(P_FRAME_GUIDE_RATIO, "ratio"),
    ),
)


def _audio_input_description(channel: int) -> CameraSelectDescription:
    path = audio_path(channel, "input")
    supported_path = audio_path(channel, "supportedInputs")

    def _options(entity: BlackmagicCameraEntity) -> OptionMap:
        inputs = entity.prop(supported_path)
        options: OptionMap = {}
        if isinstance(inputs, list):
            for item in inputs:
                name = field(item, "input") if isinstance(item, dict) else item
                if isinstance(name, str):
                    options[name] = name
        current = entity.prop_field("input")
        if isinstance(current, str):
            options.setdefault(current, current)
        return options

    return CameraSelectDescription(
        key="audio_input",
        translation_key="audio_input",
        path=path,
        entity_category=EntityCategory.CONFIG,
        options_fn=_options,
        current_fn=_string_current("input"),
        write_fn=lambda _e, value: (
            path,
            {"input": value},
            (
                path,
                audio_path(channel, "input/description"),
                audio_path(channel, "level"),
                audio_path(channel, "phantomPower"),
                audio_path(channel, "padding"),
                audio_path(channel, "lowCutFilter"),
            ),
        ),
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlackmagicCameraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up camera selects."""
    coordinator = entry.runtime_data
    caps = coordinator.capabilities
    entities: list[CameraSelect] = [
        CameraSelect(coordinator, description, description.path)
        for description in GLOBAL_SELECTS
        if caps.supports(description.path)
    ]
    for channel in range(caps.audio_channels):
        description = _audio_input_description(channel)
        if caps.supports(description.path):
            entities.append(
                CameraSelect(
                    coordinator,
                    description,
                    description.path,
                    unique_suffix=str(channel),
                    placeholders={"channel": str(channel + 1)},
                )
            )
    async_add_entities(entities)


class CameraSelect(BlackmagicCameraEntity, SelectEntity):
    """A select backed by a camera property."""

    entity_description: CameraSelectDescription

    @property
    def available(self) -> bool:
        """Return True when the select can be used."""
        if not super().available or not self.options:
            return False
        check = self.entity_description.available_fn
        return check is None or check(self)

    def _option_map(self) -> OptionMap:
        options = dict(self.entity_description.options_fn(self))
        current = self.entity_description.current_fn(self)
        if current is not None and current not in options:
            # Keep an unlisted current value visible instead of showing nothing.
            options[current] = None
        return options

    @property
    def options(self) -> list[str]:
        """Return the available options."""
        return list(self._option_map())

    @property
    def current_option(self) -> str | None:
        """Return the selected option."""
        return self.entity_description.current_fn(self)

    async def async_select_option(self, option: str) -> None:
        """Write the selected option to the camera."""
        value = self._option_map().get(option)
        if value is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_option",
                translation_placeholders={"option": option},
            )
        path, payload, refresh = self.entity_description.write_fn(self, value)
        fallback = None
        if self.entity_description.merge_on_reject:
            current = self.prop(path)
            if isinstance(current, dict):
                fallback = ("put", path, {**current, **payload})
        await self.coordinator.async_command(
            "put",
            path,
            payload,
            refresh=refresh,
            fallback=fallback,
            fallback_status=REJECTED_STATUS,
        )
