"""Buttons for the Blackmagic Camera integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    P_AUTO_WHITE_BALANCE,
    P_CC_COLOR,
    P_CC_CONTRAST,
    P_CC_GAIN,
    P_CC_GAMMA,
    P_CC_LIFT,
    P_CC_LUMA,
    P_CC_OFFSET,
    P_DO_AUTOFOCUS,
    P_FOCUS,
    P_FOCUS_DESC,
    P_PRERECORD,
    P_STILL_CAPTURE,
    P_TINT,
    P_WHITE_BALANCE,
)
from .coordinator import BlackmagicCameraConfigEntry, BlackmagicCameraCoordinator
from .entity import BlackmagicCameraEntity

PARALLEL_UPDATES = 1

# Defaults from the Blackmagic camera control protocol.
COLOR_DEFAULTS: tuple[tuple[str, dict[str, float]], ...] = (
    (P_CC_LIFT, {"red": 0.0, "green": 0.0, "blue": 0.0, "luma": 0.0}),
    (P_CC_GAMMA, {"red": 0.0, "green": 0.0, "blue": 0.0, "luma": 0.0}),
    (P_CC_GAIN, {"red": 1.0, "green": 1.0, "blue": 1.0, "luma": 1.0}),
    (P_CC_OFFSET, {"red": 0.0, "green": 0.0, "blue": 0.0, "luma": 0.0}),
    (P_CC_CONTRAST, {"pivot": 0.5, "adjust": 1.0}),
    (P_CC_COLOR, {"hue": 0.0, "saturation": 1.0}),
    (P_CC_LUMA, {"lumaContribution": 1.0}),
)


@dataclass(frozen=True, kw_only=True)
class CameraButtonDescription(ButtonEntityDescription):
    """Describes a camera button."""

    press_fn: Callable[[BlackmagicCameraCoordinator], Awaitable[None]]
    available_fn: Callable[[BlackmagicCameraEntity], bool] | None = None


async def _reset_color(coordinator: BlackmagicCameraCoordinator) -> None:
    for path, payload in COLOR_DEFAULTS:
        if coordinator.capabilities.supports(path):
            await coordinator.async_command("put", path, payload, refresh=())
    await coordinator.async_refresh_paths(path for path, _ in COLOR_DEFAULTS)


CAPTURE_STILL = CameraButtonDescription(
    key="capture_still",
    translation_key="capture_still",
    press_fn=lambda c: c.async_command("post", P_STILL_CAPTURE, refresh=()),
)
AUTO_WHITE_BALANCE = CameraButtonDescription(
    key="auto_white_balance",
    translation_key="auto_white_balance",
    press_fn=lambda c: c.async_command(
        "put", P_AUTO_WHITE_BALANCE, refresh=(P_WHITE_BALANCE, P_TINT)
    ),
)
AUTOFOCUS = CameraButtonDescription(
    key="lens_autofocus",
    translation_key="lens_autofocus",
    # The camera wants a region of interest; the centre of the frame.
    press_fn=lambda c: c.async_command(
        "put", P_DO_AUTOFOCUS, {"position": {"x": 0.5, "y": 0.5}}, refresh=(P_FOCUS,)
    ),
    available_fn=lambda e: e.prop_field("controllable", P_FOCUS_DESC) is not False,
)
PRERECORD = CameraButtonDescription(
    key="start_prerecord",
    translation_key="start_prerecord",
    press_fn=lambda c: c.async_command("post", P_PRERECORD, refresh=(P_PRERECORD,)),
)
COLOR_RESET = CameraButtonDescription(
    key="color_correction_reset",
    translation_key="color_correction_reset",
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
    press_fn=_reset_color,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlackmagicCameraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up camera buttons."""
    coordinator = entry.runtime_data
    caps = coordinator.capabilities
    entities: list[CameraButton] = []
    if caps.still_capture:
        entities.append(CameraButton(coordinator, CAPTURE_STILL, None))
    if caps.supports(P_WHITE_BALANCE):
        entities.append(CameraButton(coordinator, AUTO_WHITE_BALANCE, None))
    if caps.supports(P_FOCUS):
        entities.append(CameraButton(coordinator, AUTOFOCUS, None))
    if caps.supports(P_PRERECORD):
        entities.append(CameraButton(coordinator, PRERECORD, None))
    if caps.supports(P_CC_LIFT):
        entities.append(CameraButton(coordinator, COLOR_RESET, None))
    entities.extend(
        CameraButton(
            coordinator,
            _panel_button(name),
            None,
            unique_suffix=name,
            placeholders={"button": name.upper()},
        )
        for name in caps.panel_buttons
    )
    async_add_entities(entities)


def _panel_button(name: str) -> CameraButtonDescription:
    """Press one of the camera's assignable function buttons."""
    return CameraButtonDescription(
        key="panel_button",
        translation_key="panel_button",
        press_fn=lambda c: c.async_command(
            "put", f"/panel/devices/{name}/state", {"action": "press"}, refresh=()
        ),
    )


class CameraButton(BlackmagicCameraEntity, ButtonEntity):
    """A button that triggers a camera action."""

    entity_description: CameraButtonDescription

    @property
    def available(self) -> bool:
        """Return True when the action can be triggered."""
        if not super().available:
            return False
        check = self.entity_description.available_fn
        return check is None or check(self)

    async def async_press(self) -> None:
        """Trigger the action."""
        await self.entity_description.press_fn(self.coordinator)
