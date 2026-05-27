"""Light platform for zM1."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ZM1Coordinator
from .entity import ZM1Entity

MAX_ZM1_BRIGHTNESS = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up zM1 light."""
    async_add_entities([ZM1Light(entry.runtime_data)])


class ZM1Light(ZM1Entity, LightEntity):
    """zM1 brightness light."""

    _attr_name = None
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(self, coordinator: ZM1Coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_light"

    @property
    def is_on(self) -> bool:
        return self._raw_brightness > 0

    @property
    def brightness(self) -> int | None:
        raw = self._raw_brightness
        if raw <= 0:
            return 0
        return round(raw / MAX_ZM1_BRIGHTNESS * 255)

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get("brightness")
        if brightness is None:
            raw = self._raw_brightness or MAX_ZM1_BRIGHTNESS
        else:
            raw = max(1, min(MAX_ZM1_BRIGHTNESS, round(brightness / 255 * MAX_ZM1_BRIGHTNESS)))
        await self.coordinator.async_send_command({"brightness": raw})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_send_command({"brightness": 0})

    @property
    def _raw_brightness(self) -> int:
        value = (self.coordinator.data or {}).get("brightness", 0)
        try:
            return max(0, min(MAX_ZM1_BRIGHTNESS, int(value)))
        except (TypeError, ValueError):
            return 0

