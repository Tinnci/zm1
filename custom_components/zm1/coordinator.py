"""Coordinator for zM1 device state."""

from __future__ import annotations

from datetime import timedelta
import inspect
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MAC,
    CONF_MQTT_BASE_TOPIC,
    CONF_SCAN_INTERVAL,
    CONF_TRANSPORT,
    CONF_UDP_COMMAND_PORT,
    CONF_UDP_RESPONSE_PORT,
    DEFAULT_MQTT_BASE_TOPIC,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DEFAULT_UDP_COMMAND_PORT,
    DEFAULT_UDP_RESPONSE_PORT,
    DOMAIN,
    TRANSPORT_MQTT,
    TRANSPORT_UDP,
)
from .protocol import build_mqtt_topics, decode_payload, encode_payload, normalize_mac
from .udp import ZM1Error, ZM1TimeoutError, ZM1UDPClient

_LOGGER = logging.getLogger(__name__)


class ZM1Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """zM1 state coordinator."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.mac = normalize_mac(entry.data[CONF_MAC])
        self.transport = entry.data[CONF_TRANSPORT]
        self.host = entry.data.get(CONF_HOST)
        self.device_name = entry.data.get(CONF_NAME) or f"zM1 {self.mac[-4:].upper()}"
        self._mqtt_unsubs: list[CALLBACK_TYPE] = []
        self._udp_client: ZM1UDPClient | None = None

        if self.host:
            self._udp_client = ZM1UDPClient(
                self.host,
                self.mac,
                command_port=entry.data.get(CONF_UDP_COMMAND_PORT, DEFAULT_UDP_COMMAND_PORT),
                response_port=entry.data.get(CONF_UDP_RESPONSE_PORT, DEFAULT_UDP_RESPONSE_PORT),
                timeout=DEFAULT_TIMEOUT,
            )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self.mac}",
            update_interval=timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
            always_update=False,
        )

    async def _async_setup(self) -> None:
        if self.transport == TRANSPORT_MQTT:
            await self._async_subscribe_mqtt()

    async def async_shutdown(self) -> None:
        """Clean up subscriptions."""
        for unsub in self._mqtt_unsubs:
            unsub()
        self._mqtt_unsubs.clear()

    async def _async_update_data(self) -> dict[str, Any]:
        if self.transport == TRANSPORT_MQTT:
            return self.data or {}

        if self._udp_client is None:
            raise UpdateFailed("UDP host is not configured")

        try:
            response = await self._udp_client.query("brightness", "version", "name", "ota_progress")
        except ZM1TimeoutError as err:
            raise UpdateFailed("Timed out waiting for zM1 UDP response") from err
        except ZM1Error as err:
            raise UpdateFailed(str(err)) from err
        return self._merge_response(response)

    async def async_send_command(self, values: dict[str, Any]) -> dict[str, Any] | None:
        """Send a command using the configured transport."""
        if self.transport == TRANSPORT_UDP:
            if self._udp_client is None:
                raise HomeAssistantError("UDP host is not configured")
            response = await self._udp_client.send(values)
            self.async_set_updated_data(self._merge_response(response))
            return response

        await self._async_publish_mqtt(values)
        optimistic = self._merge_response(values)
        self.async_set_updated_data(optimistic)
        return optimistic

    async def async_configure_mqtt(
        self,
        *,
        mqtt_uri: str,
        mqtt_port: int,
        mqtt_user: str | None,
        mqtt_password: str | None,
    ) -> dict[str, Any]:
        """Write device MQTT settings over UDP."""
        if self._udp_client is None:
            raise HomeAssistantError("MQTT configuration requires a UDP host for the device")
        response = await self._udp_client.configure_mqtt(
            mqtt_uri=mqtt_uri,
            mqtt_port=mqtt_port,
            mqtt_user=mqtt_user,
            mqtt_password=mqtt_password,
        )
        self.async_set_updated_data(self._merge_response(response))
        return response

    async def async_start_ota(self, ota_url: str) -> dict[str, Any]:
        """Start an OTA update over UDP."""
        if self._udp_client is None:
            raise HomeAssistantError("OTA requires a UDP host for the device")
        response = await self._udp_client.start_ota(ota_url)
        self.async_set_updated_data(self._merge_response(response))
        return response

    async def _async_publish_mqtt(self, values: dict[str, Any]) -> None:
        from homeassistant.components import mqtt

        topics = build_mqtt_topics(
            self.mac,
            self.entry.data.get(CONF_MQTT_BASE_TOPIC, DEFAULT_MQTT_BASE_TOPIC),
        )
        payload = encode_payload({"mac": self.mac, **values}).decode()
        result = mqtt.async_publish(self.hass, topics.command, payload, qos=0, retain=False)
        if inspect.isawaitable(result):
            await result

    async def _async_subscribe_mqtt(self) -> None:
        from homeassistant.components import mqtt

        await mqtt.async_wait_for_mqtt_client(self.hass)

        topics = build_mqtt_topics(
            self.mac,
            self.entry.data.get(CONF_MQTT_BASE_TOPIC, DEFAULT_MQTT_BASE_TOPIC),
        )

        @callback
        def handle_message(msg: Any) -> None:
            try:
                payload = decode_payload(msg.payload)
            except ValueError as err:
                _LOGGER.debug("Ignoring invalid zM1 MQTT payload on %s: %s", msg.topic, err)
                return
            if payload.get("mac", self.mac) != self.mac:
                return
            self.async_set_updated_data(self._merge_response(payload))

        try:
            for topic in (topics.state, topics.sensor):
                result = mqtt.async_subscribe(self.hass, topic, handle_message, qos=0)
                unsub = await result if inspect.isawaitable(result) else result
                self._mqtt_unsubs.append(unsub)
        except Exception as err:
            raise ConfigEntryNotReady("MQTT is not ready") from err

    def _merge_response(self, response: dict[str, Any]) -> dict[str, Any]:
        data = dict(self.data or {})
        data.update(response)
        if "name" in response and response["name"]:
            self.device_name = str(response["name"])
        data["_last_seen"] = dt_util.utcnow()
        return data
