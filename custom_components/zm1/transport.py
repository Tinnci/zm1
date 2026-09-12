"""Transport adapters for zM1."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo

from .const import (
    CONF_LAST_HOST,
    CONF_MQTT_BASE_TOPIC,
    CONF_SCAN_INTERVAL,
    CONF_TRANSPORT,
    CONF_UDP_COMMAND_PORT,
    CONF_UDP_RESPONSE_PORT,
    CONF_ZEROCONF_NAME,
    DEFAULT_MQTT_BASE_TOPIC,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DEFAULT_UDP_COMMAND_PORT,
    DEFAULT_UDP_RESPONSE_PORT,
    MAX_ADAPTIVE_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    TRANSPORT_MQTT,
    ZM1_ZEROCONF_TYPE,
)
from .polling import AdaptivePollingPolicy
from .protocol import build_command, build_mqtt_topics, decode_payload, encode_payload
from .repairs import (
    ISSUE_MQTT_NOT_READY,
    ISSUE_UDP_RESPONSE_UNAVAILABLE,
    async_create_mqtt_not_ready_issue,
    async_create_udp_response_issue,
    async_delete_issue,
)
from .udp import ZM1Error, ZM1TimeoutError, ZM1UDPClient

_LOGGER = logging.getLogger(__name__)
TransportMessageHandler = Callable[[dict[str, Any], datetime, str], None]


class ZM1Transport(Protocol):
    """Transport interface used by the coordinator."""

    @property
    def update_interval(self) -> timedelta:
        """Return the next polling interval."""

    @property
    def response_port(self) -> int:
        """Return the UDP response port used for repairs."""

    async def async_setup(self) -> None:
        """Prepare the transport."""

    async def async_shutdown(self) -> None:
        """Release transport resources."""

    async def async_update_data(
        self,
        *,
        current_data: Mapping[str, Any],
        device_name: str,
    ) -> dict[str, Any] | None:
        """Fetch state through the transport."""

    async def async_send_command(self, values: dict[str, Any]) -> dict[str, Any] | None:
        """Send a device command through the transport."""

    async def async_configure_mqtt(
        self,
        *,
        mqtt_uri: str,
        mqtt_port: int,
        mqtt_user: str | None,
        mqtt_password: str | None,
    ) -> dict[str, Any]:
        """Write device-side MQTT settings."""

    async def async_start_ota(self, ota_url: str) -> dict[str, Any]:
        """Start an OTA update."""


def create_transport(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    mac: str,
    on_message: TransportMessageHandler,
) -> ZM1Transport:
    """Create the configured transport adapter."""
    if entry.data[CONF_TRANSPORT] == TRANSPORT_MQTT:
        return ZM1MqttTransport(hass, entry, mac=mac, on_message=on_message)
    return ZM1UdpTransport(hass, entry, mac=mac, on_message=on_message)


class ZM1UdpTransport:
    """Continuous UDP observations with bounded retries for read-only queries."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        mac: str,
        on_message: TransportMessageHandler | None = None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.mac = mac
        self.configured_host = str(entry.data.get(CONF_HOST, "") or "").strip()
        self.last_host = str(entry.data.get(CONF_LAST_HOST) or "").strip()
        self.zeroconf_name = str(entry.data.get(CONF_ZEROCONF_NAME) or "").strip()
        self.command_port = entry.data.get(CONF_UDP_COMMAND_PORT, DEFAULT_UDP_COMMAND_PORT)
        self._response_port = entry.data.get(CONF_UDP_RESPONSE_PORT, DEFAULT_UDP_RESPONSE_PORT)
        self._on_message = on_message
        self._unavailable_reported = False
        self._polling_policy = AdaptivePollingPolicy(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            min_interval=MIN_SCAN_INTERVAL,
            max_interval=MAX_ADAPTIVE_SCAN_INTERVAL,
        )
        self._client = ZM1UDPClient(
            self.configured_host or self.last_host,
            self.mac,
            command_port=self.command_port,
            response_port=self._response_port,
            timeout=DEFAULT_TIMEOUT,
            on_report=self._handle_report,
        )

    @property
    def update_interval(self) -> timedelta:
        return timedelta(seconds=self._polling_policy.interval)

    @property
    def response_port(self) -> int:
        return self._response_port

    async def async_setup(self) -> None:
        await self._client.async_start()

    async def async_shutdown(self) -> None:
        await self._client.async_close()

    def _handle_report(self, payload: dict[str, Any], received_at: datetime) -> None:
        self._polling_policy.record_success()
        if self._polling_policy.is_healthy:
            self._unavailable_reported = False
            async_delete_issue(self.hass, ISSUE_UDP_RESPONSE_UNAVAILABLE, self.entry.entry_id)
        if self._on_message is not None:
            self._on_message(payload, received_at, "udp")

    def _has_recent_report(self) -> bool:
        received = self._client.last_received_monotonic
        return received is not None and time.monotonic() - received < self._polling_policy.base_interval

    async def async_update_data(
        self,
        *,
        current_data: Mapping[str, Any],
        device_name: str,
    ) -> None:
        try:
            client = await self._async_get_udp_client()
            try:
                await client.query("brightness", "version", "name")
            except ZM1TimeoutError:
                # Sensor broadcasts remain valid even if a state query was lost.
                if self._has_recent_report():
                    return
                await asyncio.sleep(0.2)
                client = await self._async_get_udp_client(force_discovery=self._polling_policy.failures > 0)
                await client.query("brightness", "version", "name")
        except ZM1Error as err:
            if self._has_recent_report():
                return
            interval = self._polling_policy.record_failure()
            _LOGGER.debug("zM1 UDP poll failed; next query in %s seconds", interval)
            if current_data and not self._polling_policy.should_report_unavailable:
                return
            if self._polling_policy.should_report_unavailable:
                async_create_udp_response_issue(
                    self.hass,
                    entry_id=self.entry.entry_id,
                    device_name=device_name,
                    response_port=self.response_port,
                )
                self._unavailable_reported = True
            raise UpdateFailed(str(err)) from err

    async def async_send_command(self, values: dict[str, Any]) -> dict[str, Any]:
        client = await self._async_get_udp_client()
        # Arbitrary commands include restart and OTA; a timeout must not replay them.
        return await client.send(values)

    async def async_configure_mqtt(
        self,
        *,
        mqtt_uri: str,
        mqtt_port: int,
        mqtt_user: str | None,
        mqtt_password: str | None,
    ) -> dict[str, Any]:
        client = await self._async_get_udp_client()
        return await client.configure_mqtt(
            mqtt_uri=mqtt_uri,
            mqtt_port=mqtt_port,
            mqtt_user=mqtt_user,
            mqtt_password=mqtt_password,
        )

    async def async_start_ota(self, ota_url: str) -> dict[str, Any]:
        client = await self._async_get_udp_client()
        return await client.start_ota(ota_url)

    async def _async_get_udp_client(self, *, force_discovery: bool = False) -> ZM1UDPClient:
        await self._client.async_start()
        if self.configured_host:
            self._client.host = self.configured_host
            return self._client
        if self._client.host and not force_discovery:
            return self._client

        host = await self._async_resolve_mdns_host() if self.zeroconf_name else None
        if not host:
            from .mdns import discover_mdns

            info = await discover_mdns(self.mac, timeout=DEFAULT_TIMEOUT)
            if info is not None:
                host = info.host
                self.zeroconf_name = info.name
                self.command_port = info.port or self.command_port
        if not host:
            from .udp import discover, find_discovered_host

            responses = await discover(
                command_port=self.command_port,
                response_port=self.response_port,
                timeout=DEFAULT_TIMEOUT,
            )
            host = find_discovered_host(responses, self.mac)
        if not host:
            raise ZM1Error("Unable to discover zM1 UDP host")

        self._client.host = self.last_host = host
        self._client.command_port = self.command_port
        updated = {
            **self.entry.data,
            CONF_LAST_HOST: host,
            CONF_ZEROCONF_NAME: self.zeroconf_name,
            CONF_UDP_COMMAND_PORT: self.command_port,
        }
        if updated != self.entry.data:
            self.hass.config_entries.async_update_entry(self.entry, data=updated)
        return self._client

    async def _async_resolve_mdns_host(self) -> str | None:
        from homeassistant.components import zeroconf

        zc = await zeroconf.async_get_instance(self.hass)
        info = AsyncServiceInfo(ZM1_ZEROCONF_TYPE, self.zeroconf_name)
        if not await info.async_request(zc, DEFAULT_TIMEOUT * 1000):
            return None
        addresses = info.parsed_addresses(IPVersion.V4Only)
        return addresses[0] if addresses else None


class ZM1MqttTransport:
    """MQTT transport adapter."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        mac: str,
        on_message: TransportMessageHandler,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.mac = mac
        self.mqtt_base_topic = entry.data.get(CONF_MQTT_BASE_TOPIC, DEFAULT_MQTT_BASE_TOPIC)
        self._on_message = on_message
        self._mqtt_unsubs: list[CALLBACK_TYPE] = []
        self._udp_maintenance = ZM1UdpTransport(hass, entry, mac=mac, on_message=on_message)

    @property
    def update_interval(self) -> timedelta:
        return timedelta(seconds=DEFAULT_SCAN_INTERVAL)

    @property
    def response_port(self) -> int:
        return self._udp_maintenance.response_port

    async def async_setup(self) -> None:
        from homeassistant.components import mqtt

        @callback
        def handle_message(msg: Any) -> None:
            # Retained payloads have no sampling timestamp and cannot establish freshness.
            if msg.retain:
                return
            try:
                payload = decode_payload(msg.payload)
            except ValueError as err:
                _LOGGER.debug("Ignoring invalid zM1 MQTT payload on %s: %s", msg.topic, err)
                return
            if payload.get("mac", self.mac) != self.mac:
                return
            self._on_message(payload, dt_util.utcnow(), "mqtt")

        try:
            await mqtt.async_wait_for_mqtt_client(self.hass)
            topics = build_mqtt_topics(self.mac, self.mqtt_base_topic)
            for topic in (topics.state, topics.sensor):
                result = mqtt.async_subscribe(self.hass, topic, handle_message, qos=0)
                unsub = await result if inspect.isawaitable(result) else result
                self._mqtt_unsubs.append(unsub)
        except Exception as err:
            await self.async_shutdown()
            async_create_mqtt_not_ready_issue(
                self.hass,
                entry_id=self.entry.entry_id,
                device_name=self.entry.title,
            )
            raise ConfigEntryNotReady("MQTT is not ready") from err

        async_delete_issue(self.hass, ISSUE_MQTT_NOT_READY, self.entry.entry_id)

    async def async_shutdown(self) -> None:
        for unsub in self._mqtt_unsubs:
            unsub()
        self._mqtt_unsubs.clear()
        await self._udp_maintenance.async_shutdown()

    async def async_update_data(
        self,
        *,
        current_data: Mapping[str, Any],
        device_name: str,
    ) -> dict[str, Any] | None:
        return None

    async def async_send_command(self, values: dict[str, Any]) -> None:
        await self._async_publish_mqtt(values)

    async def async_configure_mqtt(
        self,
        *,
        mqtt_uri: str,
        mqtt_port: int,
        mqtt_user: str | None,
        mqtt_password: str | None,
    ) -> dict[str, Any]:
        return await self._udp_maintenance.async_configure_mqtt(
            mqtt_uri=mqtt_uri,
            mqtt_port=mqtt_port,
            mqtt_user=mqtt_user,
            mqtt_password=mqtt_password,
        )

    async def async_start_ota(self, ota_url: str) -> dict[str, Any]:
        return await self._udp_maintenance.async_start_ota(ota_url)

    async def _async_publish_mqtt(self, values: dict[str, Any]) -> None:
        from homeassistant.components import mqtt

        topics = build_mqtt_topics(self.mac, self.mqtt_base_topic)
        payload = encode_payload(build_command(self.mac, values)).decode()
        result = mqtt.async_publish(self.hass, topics.command, payload, qos=0, retain=False)
        if inspect.isawaitable(result):
            await result
