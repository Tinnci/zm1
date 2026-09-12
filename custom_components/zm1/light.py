"""Light platform for zM1."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ZM1Coordinator
from .entity import ZM1Entity
from .protocol import (
    MAX_ZM1_BRIGHTNESS,
    clamp_zm1_brightness,
    ha_brightness_to_zm1,
    zm1_brightness_to_ha,
)


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
    def is_on(self) -> bool | None:
        raw = self._raw_brightness
        return raw > 0 if raw is not None else None

    @property
    def brightness(self) -> int | None:
        raw = self._raw_brightness
        return zm1_brightness_to_ha(raw) if raw is not None else None

    @property
    def available(self) -> bool:
        return self.coordinator.field_is_fresh("brightness")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.observation_attributes("brightness")

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get("brightness")
        raw = ha_brightness_to_zm1(
            brightness,
            fallback=self._raw_brightness or MAX_ZM1_BRIGHTNESS,
        )
        if self.coordinator.data is not None and raw == self._raw_brightness:
            return
        await self.coordinator.async_send_command({"brightness": raw})

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.coordinator.data is not None and self._raw_brightness == 0:
            return
        await self.coordinator.async_send_command({"brightness": 0})

    @property
    def _raw_brightness(self) -> int | None:
        if not self.available:
            return None
        value = (self.coordinator.data or {}).get("brightness")
        return clamp_zm1_brightness(value)
