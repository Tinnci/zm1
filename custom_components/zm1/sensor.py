"""Sensor platform for zM1."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ZM1Coordinator
from .entity import ZM1Entity


@dataclass(frozen=True, kw_only=True)
class ZM1SensorEntityDescription(SensorEntityDescription):
    """Describes a zM1 sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[ZM1SensorEntityDescription, ...] = (
    ZM1SensorEntityDescription(
        key="version",
        translation_key="version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("version"),
    ),
    ZM1SensorEntityDescription(
        key="ota_progress",
        translation_key="ota_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("ota_progress"),
    ),
    ZM1SensorEntityDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("_last_seen"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up zM1 sensors."""
    coordinator: ZM1Coordinator = entry.runtime_data
    async_add_entities([ZM1Sensor(coordinator, description) for description in SENSORS])


class ZM1Sensor(ZM1Entity, SensorEntity):
    """zM1 diagnostic sensor."""

    entity_description: ZM1SensorEntityDescription

    def __init__(self, coordinator: ZM1Coordinator, description: ZM1SensorEntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.mac}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data or {})

