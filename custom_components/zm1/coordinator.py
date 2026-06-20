"""Coordinator for zM1 device state."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MAC,
    CONF_TRANSPORT,
    DOMAIN,
)
from .protocol import normalize_mac
from .transport import ZM1Transport, create_transport

_LOGGER = logging.getLogger(__name__)


class ZM1Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """zM1 state coordinator."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.mac = normalize_mac(entry.data[CONF_MAC])
        self.transport = entry.data[CONF_TRANSPORT]
        self.device_name = entry.data.get(CONF_NAME) or f"zM1 {self.mac[-4:].upper()}"
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
        """Return the UDP response port used for repairs."""
        return self._transport.response_port

    async def _async_setup(self) -> None:
        await self._transport.async_setup()
        self.update_interval = self._transport.update_interval

    async def async_shutdown(self) -> None:
        """Clean up subscriptions."""
        await self._transport.async_shutdown()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            response = await self._transport.async_update_data(
                current_data=self.data or {},
                device_name=self.device_name,
            )
        finally:
            self.update_interval = self._transport.update_interval
        if response is None:
            return self.data or {}
        return self._merge_response(response)

    async def async_send_command(self, values: dict[str, Any]) -> dict[str, Any] | None:
        """Send a command using the configured transport."""
        response = await self._transport.async_send_command(values)
        if response is not None:
            self.async_set_updated_data(self._merge_response(response))
        return response

    async def async_configure_mqtt(
        self,
        *,
        mqtt_uri: str,
        mqtt_port: int,
        mqtt_user: str | None,
        mqtt_password: str | None,
    ) -> dict[str, Any]:
        """Write device MQTT settings over UDP."""
        response = await self._transport.async_configure_mqtt(
            mqtt_uri=mqtt_uri,
            mqtt_port=mqtt_port,
            mqtt_user=mqtt_user,
            mqtt_password=mqtt_password,
        )
        self.async_set_updated_data(self._merge_response(response))
        return response

    async def async_start_ota(self, ota_url: str) -> dict[str, Any]:
        """Start an OTA update over UDP."""
        response = await self._transport.async_start_ota(ota_url)
        self.async_set_updated_data(self._merge_response(response))
        return response

    def _merge_response(
        self,
        response: dict[str, Any],
        *,
        base: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = dict(base or self.data or {})
        data.update(response)
        if "name" in response and response["name"]:
            self.device_name = str(response["name"])
        data["_last_seen"] = dt_util.utcnow()
        return data

    def _handle_transport_message(self, payload: dict[str, Any]) -> None:
        self.async_set_updated_data(self._merge_response(payload))
