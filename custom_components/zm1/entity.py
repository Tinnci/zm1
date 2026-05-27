"""Base entity for zM1."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ZM1Coordinator


class ZM1Entity(CoordinatorEntity[ZM1Coordinator]):
    """Base zM1 entity."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        data: dict[str, Any] = self.coordinator.data or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.mac)},
            manufacturer="zM1",
            model="zM1",
            name=data.get("name") or self.coordinator.device_name,
            sw_version=data.get("version"),
        )

