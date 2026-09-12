"""Sensor platform for zM1."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_MILLIGRAMS_PER_CUBIC_METER,
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ZM1Coordinator
from .entity import ZM1Entity
from .observations import EXPIRING_FIELDS


@dataclass(frozen=True, kw_only=True)
class ZM1SensorEntityDescription(SensorEntityDescription):
    """Describes a zM1 sensor."""

    value_fn: Callable[[Mapping[str, Any]], Any]


SENSORS: tuple[ZM1SensorEntityDescription, ...] = (
    ZM1SensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _numeric(data, "temperature"),
    ),
    ZM1SensorEntityDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _numeric(data, "humidity"),
    ),
    ZM1SensorEntityDescription(
        key="formaldehyde",
        translation_key="formaldehyde",
        native_unit_of_measurement=CONCENTRATION_MILLIGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _numeric(data, "formaldehyde"),
    ),
    ZM1SensorEntityDescription(
        key="pm25",
        translation_key="pm25",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _numeric(data, "PM25", "pm25"),
    ),
    ZM1SensorEntityDescription(
        key="version",
        translation_key="version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.get("version"),
    ),
    ZM1SensorEntityDescription(
        key="ota_progress",
        translation_key="ota_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: _numeric(data, "ota_progress"),
    ),
    ZM1SensorEntityDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.get("_last_seen"),
    ),
)


def _numeric(data: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up zM1 sensors."""
    coordinator: ZM1Coordinator = entry.runtime_data
    registry = er.async_get(hass)
    retired_ids = {f"{coordinator.mac}_{key}" for key in ("co2", "eco2", "tvoc")}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain == "sensor" and entity.platform == DOMAIN and entity.unique_id in retired_ids:
            registry.async_remove(entity.entity_id)
    async_add_entities([ZM1Sensor(coordinator, description) for description in SENSORS])


class ZM1Sensor(ZM1Entity, SensorEntity):
    """zM1 diagnostic sensor."""

    entity_description: ZM1SensorEntityDescription

    def __init__(self, coordinator: ZM1Coordinator, description: ZM1SensorEntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.mac}_{description.key}"

    def _publication_state(self) -> tuple:
        available, value, attrs = super()._publication_state()
        # A traffic timestamp is diagnostic metadata, not an environmental signal.
        return available, None if self.entity_description.key == "last_seen" else value, attrs

    @property
    def native_value(self) -> Any:
        if self.entity_description.key in EXPIRING_FIELDS and not self.available:
            return None
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def available(self) -> bool:
        key = self.entity_description.key
        if key in EXPIRING_FIELDS:
            return self.coordinator.field_is_fresh(key)
        return self.entity_description.value_fn(self.coordinator.data or {}) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key in EXPIRING_FIELDS:
            return self.coordinator.observation_attributes(self.entity_description.key)
        return None
