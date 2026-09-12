"""Base entity for zM1."""

from __future__ import annotations

import copy
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ZM1Coordinator

REPORT_INTERVAL = 60.0


class ZM1Entity(CoordinatorEntity[ZM1Coordinator]):
    """Base zM1 entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ZM1Coordinator) -> None:
        super().__init__(coordinator)
        self._published_state: tuple | None = None
        self._published_at = 0.0
        self._publish_unsub: CALLBACK_TYPE | None = None

    def _publication_state(self) -> tuple:
        """Compare physical values and metadata without treating receipt time as a change."""
        attrs = {**(self.state_attributes or {}), **(self.extra_state_attributes or {})}
        attrs.pop("observed_at", None)
        return self.available, self.state, attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._published_state = copy.deepcopy(self._publication_state())
        self._published_at = time.monotonic()

    async def async_will_remove_from_hass(self) -> None:
        if self._publish_unsub is not None:
            self._publish_unsub()
            self._publish_unsub = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Publish every value/availability change and coalesce identical reports."""
        elapsed = time.monotonic() - self._published_at
        if self._publication_state() != self._published_state or elapsed >= REPORT_INTERVAL:
            self._publish_report()
        elif self._publish_unsub is None:
            # Flush the final received timestamp even if the device falls silent.
            self._publish_unsub = async_call_later(self.hass, REPORT_INTERVAL - elapsed, self._publish_report)

    @callback
    def _publish_report(self, _now: datetime | None = None) -> None:
        if self._publish_unsub is not None:
            self._publish_unsub()
            self._publish_unsub = None
        self._published_state = copy.deepcopy(self._publication_state())
        self._published_at = time.monotonic()
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        data: Mapping[str, Any] = self.coordinator.data or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.mac)},
            manufacturer="zM1",
            model="zM1",
            name=data.get("name") or self.coordinator.device_name,
            sw_version=data.get("version"),
        )
