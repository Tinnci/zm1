"""Publish zM1 observations without turning dispatch or cached data into feedback."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import CONF_MAC, CONF_TRANSPORT, DOMAIN
from .observations import EXPIRING_FIELDS, OBSERVATION_TTL, field_is_fresh, merge_report
from .protocol import normalize_mac
from .transport import ZM1Transport, create_transport

_LOGGER = logging.getLogger(__name__)


class ZM1Coordinator(DataUpdateCoordinator[Mapping[str, Any]]):
    """Track per-field reports independently of transport polling health."""

    data: Mapping[str, Any]

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.mac = normalize_mac(entry.data[CONF_MAC])
        self.transport = entry.data[CONF_TRANSPORT]
        self.device_name = entry.data.get(CONF_NAME) or f"zM1 {self.mac[-4:].upper()}"
        self._expiry_unsub: CALLBACK_TYPE | None = None
        self._transport: ZM1Transport = create_transport(
            hass,
            entry,
            mac=self.mac,
            on_message=self._handle_transport_message,
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self.mac}",
            update_interval=self._transport.update_interval,
            always_update=False,
        )

    @property
    def response_port(self) -> int:
        return self._transport.response_port

    async def _async_setup(self) -> None:
        await self._transport.async_setup()
        self.update_interval = self._transport.update_interval

    async def async_shutdown(self) -> None:
        """Release the shared listener, expiry callback and coordinator timers."""
        if self._expiry_unsub is not None:
            self._expiry_unsub()
            self._expiry_unsub = None
        await super().async_shutdown()
        await self._transport.async_shutdown()

    async def _async_update_data(self) -> Mapping[str, Any]:
        try:
            await self._transport.async_update_data(
                current_data=self.data or {},
                device_name=self.device_name,
            )
        finally:
            self.update_interval = self._transport.update_interval
        # Reports were already published at reception. A poll does not re-observe them.
        return self.data if self.data is not None else MappingProxyType({})

    async def async_send_command(self, values: dict[str, Any]) -> dict[str, Any] | None:
        """Return dispatch feedback; only the receive path updates observed state."""
        return await self._transport.async_send_command(values)

    async def async_configure_mqtt(
        self,
        *,
        mqtt_uri: str,
        mqtt_port: int,
        mqtt_user: str | None,
        mqtt_password: str | None,
    ) -> dict[str, Any]:
        return await self._transport.async_configure_mqtt(
            mqtt_uri=mqtt_uri,
            mqtt_port=mqtt_port,
            mqtt_user=mqtt_user,
            mqtt_password=mqtt_password,
        )

    async def async_start_ota(self, ota_url: str) -> dict[str, Any]:
        return await self._transport.async_start_ota(ota_url)

    def field_is_fresh(self, field: str) -> bool:
        return field_is_fresh(self.data or {}, field, now=dt_util.utcnow())

    def observation_attributes(self, field: str) -> dict[str, Any]:
        data = self.data or {}
        observed_at = data.get("_observed_at", {}).get(field)
        return {
            "observed_at": observed_at.isoformat() if observed_at is not None else None,
            "observation_source": data.get("_observation_sources", {}).get(field),
            "observation_max_age_s": OBSERVATION_TTL,
        }

    @callback
    def _handle_transport_message(self, payload: dict[str, Any], received_at: datetime, source: str) -> None:
        self.data = merge_report(self.data or {}, payload, received_at=received_at, source=source)
        if self.data.get("name"):
            self.device_name = str(self.data["name"])
        self.last_update_success = True
        self.update_interval = self._transport.update_interval
        # Keep scheduled diagnostic polls: async_set_updated_data resets their timer
        # on every sensor broadcast and would starve brightness/version queries.
        self.async_update_listeners()
        self._schedule_expiry()

    @callback
    def _schedule_expiry(self) -> None:
        if self._expiry_unsub is not None:
            self._expiry_unsub()
            self._expiry_unsub = None
        now = dt_util.utcnow()
        timestamps = (self.data or {}).get("_observed_at", {})
        deadlines = [
            timestamp + timedelta(seconds=OBSERVATION_TTL)
            for field, timestamp in timestamps.items()
            if field in EXPIRING_FIELDS and timestamp + timedelta(seconds=OBSERVATION_TTL) > now
        ]
        if deadlines:
            self._expiry_unsub = async_call_later(
                self.hass,
                (min(deadlines) - now).total_seconds(),
                self._expire_observations,
            )

    @callback
    def _expire_observations(self, now: datetime) -> None:
        self._expiry_unsub = None
        self.async_update_listeners()
        self._schedule_expiry()
