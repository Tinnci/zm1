"""Coordinator for zM1 device state."""

from __future__ import annotations

from datetime import timedelta
import inspect
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo

from .const import (
    CONF_MAC,
    CONF_LAST_HOST,
    CONF_MQTT_BASE_TOPIC,
    CONF_SCAN_INTERVAL,
    CONF_TRANSPORT,
    CONF_UDP_COMMAND_PORT,
    CONF_UDP_RESPONSE_PORT,
    CONF_ZEROCONF_NAME,
    DEFAULT_MQTT_BASE_TOPIC,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SENSOR_REPORT_TIMEOUT,
    DEFAULT_TIMEOUT,
    DEFAULT_UDP_COMMAND_PORT,
    DEFAULT_UDP_RESPONSE_PORT,
    DOMAIN,
    TRANSPORT_MQTT,
    TRANSPORT_UDP,
    ZM1_ZEROCONF_TYPE,
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
        self.configured_host = str(entry.data.get(CONF_HOST, "") or "").strip()
        self.last_host = str(entry.data.get(CONF_LAST_HOST) or "").strip()
        self.host = self.configured_host or None
        self.zeroconf_name = str(entry.data.get(CONF_ZEROCONF_NAME) or "").strip()
        self.command_port = entry.data.get(CONF_UDP_COMMAND_PORT, DEFAULT_UDP_COMMAND_PORT)
        self.response_port = entry.data.get(CONF_UDP_RESPONSE_PORT, DEFAULT_UDP_RESPONSE_PORT)
        self.mqtt_base_topic = entry.data.get(CONF_MQTT_BASE_TOPIC, DEFAULT_MQTT_BASE_TOPIC)
        self.device_name = entry.data.get(CONF_NAME) or f"zM1 {self.mac[-4:].upper()}"
        self._mqtt_unsubs: list[CALLBACK_TYPE] = []
        self._udp_client: ZM1UDPClient | None = None

        if self.host:
            self._udp_client = self._new_udp_client(self.host)

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

        try:
            client = await self._async_get_udp_client()
            response = await client.query("brightness", "version", "name", "ota_progress")
            data = self._merge_response(response)
            if client.last_sensor_report:
                data = self._merge_response(client.last_sensor_report, base=data)
            try:
                sensor_report = await client.read_sensor_report(
                    timeout=DEFAULT_SENSOR_REPORT_TIMEOUT
                )
            except ZM1Error as err:
                _LOGGER.debug("Unable to read zM1 sensor report: %s", err)
            else:
                if sensor_report:
                    data = self._merge_response(sensor_report, base=data)
            return data
        except ZM1TimeoutError as err:
            try:
                client = await self._async_get_udp_client(force_discovery=True)
                response = await client.query("brightness", "version", "name", "ota_progress")
            except ZM1Error as retry_err:
                raise UpdateFailed(
                    "Timed out waiting for zM1 UDP response. mDNS/host discovery can "
                    "succeed while state queries fail if Home Assistant cannot receive "
                    f"UDP {self.response_port} from the zM1 device"
                ) from retry_err
        except ZM1Error as err:
            raise UpdateFailed(str(err)) from err
        data = self._merge_response(response)
        if client.last_sensor_report:
            data = self._merge_response(client.last_sensor_report, base=data)
        return data

    async def async_send_command(self, values: dict[str, Any]) -> dict[str, Any] | None:
        """Send a command using the configured transport."""
        if self.transport == TRANSPORT_UDP:
            client = await self._async_get_udp_client()
            response = await client.send(values)
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
        client = await self._async_get_udp_client()
        response = await client.configure_mqtt(
            mqtt_uri=mqtt_uri,
            mqtt_port=mqtt_port,
            mqtt_user=mqtt_user,
            mqtt_password=mqtt_password,
        )
        self.async_set_updated_data(self._merge_response(response))
        return response

    async def async_start_ota(self, ota_url: str) -> dict[str, Any]:
        """Start an OTA update over UDP."""
        client = await self._async_get_udp_client()
        response = await client.start_ota(ota_url)
        self.async_set_updated_data(self._merge_response(response))
        return response

    def _new_udp_client(self, host: str) -> ZM1UDPClient:
        return ZM1UDPClient(
            host,
            self.mac,
            command_port=self.command_port,
            response_port=self.response_port,
            timeout=DEFAULT_TIMEOUT,
        )

    async def _async_get_udp_client(self, *, force_discovery: bool = False) -> ZM1UDPClient:
        if self._udp_client is not None and not force_discovery:
            return self._udp_client

        if self.configured_host and not force_discovery:
            self.host = self.configured_host
            self._udp_client = self._new_udp_client(self.host)
            return self._udp_client

        if self.zeroconf_name:
            host = await self._async_resolve_mdns_host()
            if host:
                self.host = host
                self._udp_client = self._new_udp_client(host)
                return self._udp_client

        if self.last_host and not force_discovery:
            self.host = self.last_host
            self._udp_client = self._new_udp_client(self.last_host)
            return self._udp_client

        from .mdns import discover_mdns

        mdns_info = await discover_mdns(self.mac, timeout=DEFAULT_TIMEOUT)
        if mdns_info is not None:
            self.command_port = mdns_info.port or self.command_port
            self.host = mdns_info.host
            self._udp_client = self._new_udp_client(mdns_info.host)
            updated_data = {
                **self.entry.data,
                CONF_LAST_HOST: mdns_info.host,
                CONF_ZEROCONF_NAME: mdns_info.name,
                CONF_UDP_COMMAND_PORT: mdns_info.port or self.command_port,
            }
            if updated_data != self.entry.data:
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    data=updated_data,
                )
            return self._udp_client

        from .udp import discover, find_discovered_host

        responses = await discover(
            command_port=self.command_port,
            response_port=self.response_port,
            timeout=DEFAULT_TIMEOUT,
        )
        host = find_discovered_host(responses, self.mac)
        if not host:
            raise ZM1Error("Unable to discover zM1 UDP host")

        self.host = host
        self._udp_client = self._new_udp_client(host)
        return self._udp_client

    async def _async_resolve_mdns_host(self) -> str | None:
        from homeassistant.components import zeroconf

        zc = await zeroconf.async_get_instance(self.hass)
        info = AsyncServiceInfo(ZM1_ZEROCONF_TYPE, self.zeroconf_name)
        if not await info.async_request(zc, DEFAULT_TIMEOUT * 1000):
            return None
        addresses = info.parsed_addresses(IPVersion.V4Only)
        if not addresses:
            addresses = info.parsed_addresses()
        return addresses[0] if addresses else None

    async def _async_publish_mqtt(self, values: dict[str, Any]) -> None:
        from homeassistant.components import mqtt

        topics = build_mqtt_topics(
            self.mac,
            self.mqtt_base_topic,
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
            self.mqtt_base_topic,
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
