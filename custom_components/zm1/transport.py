"""Transport adapters for zM1."""

from __future__ import annotations

from datetime import timedelta
import inspect
import logging
from typing import Any, Callable, Protocol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed
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
    DEFAULT_SENSOR_REPORT_TIMEOUT,
    DEFAULT_TIMEOUT,
    DEFAULT_UDP_COMMAND_PORT,
    DEFAULT_UDP_RESPONSE_PORT,
    MAX_ADAPTIVE_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    TRANSPORT_MQTT,
    ZM1_ZEROCONF_TYPE,
)
from .polling import AdaptivePollingPolicy
from .protocol import build_mqtt_topics, decode_payload, encode_payload
from .repairs import (
    ISSUE_MQTT_NOT_READY,
    ISSUE_UDP_RESPONSE_UNAVAILABLE,
    async_create_mqtt_not_ready_issue,
    async_create_udp_response_issue,
    async_delete_issue,
)
from .udp import ZM1Error, ZM1TimeoutError, ZM1UDPClient

_LOGGER = logging.getLogger(__name__)
TransportMessageHandler = Callable[[dict[str, Any]], None]


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
        current_data: dict[str, Any],
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
    return ZM1UdpTransport(hass, entry, mac=mac)


class ZM1UdpTransport:
    """UDP transport adapter."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, *, mac: str) -> None:
        self.hass = hass
        self.entry = entry
        self.mac = mac
        self.configured_host = str(entry.data.get(CONF_HOST, "") or "").strip()
        self.last_host = str(entry.data.get(CONF_LAST_HOST) or "").strip()
        self.host = self.configured_host or None
        self.zeroconf_name = str(entry.data.get(CONF_ZEROCONF_NAME) or "").strip()
        self.command_port = entry.data.get(
            CONF_UDP_COMMAND_PORT, DEFAULT_UDP_COMMAND_PORT
        )
        self._response_port = entry.data.get(
            CONF_UDP_RESPONSE_PORT, DEFAULT_UDP_RESPONSE_PORT
        )
        self._client: ZM1UDPClient | None = None
        self._polling_policy = AdaptivePollingPolicy(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            min_interval=MIN_SCAN_INTERVAL,
            max_interval=MAX_ADAPTIVE_SCAN_INTERVAL,
        )

        if self.host:
            self._client = self._new_udp_client(self.host)

    @property
    def update_interval(self) -> timedelta:
        return timedelta(seconds=self._polling_policy.interval)

    @property
    def response_port(self) -> int:
        return self._response_port

    async def async_setup(self) -> None:
        """Prepare UDP transport."""

    async def async_shutdown(self) -> None:
        """Release UDP transport resources."""

    async def async_update_data(
        self,
        *,
        current_data: dict[str, Any],
        device_name: str,
    ) -> dict[str, Any]:
        try:
            client = await self._async_get_udp_client()
            data = await self._async_query_state(client, current_data=current_data)
            async_delete_issue(
                self.hass, ISSUE_UDP_RESPONSE_UNAVAILABLE, self.entry.entry_id
            )
            self._record_success()
            return data
        except ZM1TimeoutError:
            try:
                client = await self._async_get_udp_client(force_discovery=True)
                data = await self._async_query_state(client, current_data=current_data)
            except ZM1Error as retry_err:
                self._record_failure()
                async_create_udp_response_issue(
                    self.hass,
                    entry_id=self.entry.entry_id,
                    device_name=device_name,
                    response_port=self.response_port,
                )
                raise UpdateFailed(
                    "Timed out waiting for zM1 UDP response. mDNS/host discovery can "
                    "succeed while state queries fail if Home Assistant cannot receive "
                    f"UDP {self.response_port} from the zM1 device"
                ) from retry_err
        except ZM1Error as err:
            self._record_failure()
            raise UpdateFailed(str(err)) from err

        async_delete_issue(
            self.hass, ISSUE_UDP_RESPONSE_UNAVAILABLE, self.entry.entry_id
        )
        self._record_success()
        return data

    async def async_send_command(self, values: dict[str, Any]) -> dict[str, Any]:
        client = await self._async_get_udp_client()
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

    async def _async_query_state(
        self,
        client: ZM1UDPClient,
        *,
        current_data: dict[str, Any],
    ) -> dict[str, Any]:
        response = await client.query("brightness", "version", "name", "ota_progress")
        data = dict(current_data)
        data.update(response)
        if client.last_sensor_report:
            data.update(client.last_sensor_report)
        try:
            sensor_report = await client.read_sensor_report(
                timeout=DEFAULT_SENSOR_REPORT_TIMEOUT
            )
        except ZM1Error as err:
            _LOGGER.debug("Unable to read zM1 sensor report: %s", err)
        else:
            if sensor_report:
                data.update(sensor_report)
        return data

    def _new_udp_client(self, host: str) -> ZM1UDPClient:
        return ZM1UDPClient(
            host,
            self.mac,
            command_port=self.command_port,
            response_port=self.response_port,
            timeout=DEFAULT_TIMEOUT,
        )

    async def _async_get_udp_client(
        self, *, force_discovery: bool = False
    ) -> ZM1UDPClient:
        if self._client is not None and not force_discovery:
            return self._client

        if self.configured_host and not force_discovery:
            self.host = self.configured_host
            self._client = self._new_udp_client(self.host)
            return self._client

        if self.zeroconf_name:
            host = await self._async_resolve_mdns_host()
            if host:
                self.host = host
                self._client = self._new_udp_client(host)
                return self._client

        if self.last_host and not force_discovery:
            self.host = self.last_host
            self._client = self._new_udp_client(self.last_host)
            return self._client

        from .mdns import discover_mdns

        mdns_info = await discover_mdns(self.mac, timeout=DEFAULT_TIMEOUT)
        if mdns_info is not None:
            self.command_port = mdns_info.port or self.command_port
            self.host = mdns_info.host
            self._client = self._new_udp_client(mdns_info.host)
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
            return self._client

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
        self._client = self._new_udp_client(host)
        return self._client

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

    def _record_success(self) -> None:
        self._polling_policy.record_success()

    def _record_failure(self) -> None:
        interval = self._polling_policy.record_failure()
        _LOGGER.debug(
            "zM1 UDP update failed %s time(s); next poll in %s seconds",
            self._polling_policy.failures,
            interval,
        )


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
        self.mqtt_base_topic = entry.data.get(
            CONF_MQTT_BASE_TOPIC, DEFAULT_MQTT_BASE_TOPIC
        )
        self._on_message = on_message
        self._mqtt_unsubs: list[CALLBACK_TYPE] = []
        self._udp_maintenance = ZM1UdpTransport(hass, entry, mac=mac)

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
            try:
                payload = decode_payload(msg.payload)
            except ValueError as err:
                _LOGGER.debug(
                    "Ignoring invalid zM1 MQTT payload on %s: %s", msg.topic, err
                )
                return
            if payload.get("mac", self.mac) != self.mac:
                return
            self._on_message(payload)

        try:
            await mqtt.async_wait_for_mqtt_client(self.hass)
            topics = build_mqtt_topics(self.mac, self.mqtt_base_topic)
            for topic in (topics.state, topics.sensor):
                result = mqtt.async_subscribe(self.hass, topic, handle_message, qos=0)
                unsub = await result if inspect.isawaitable(result) else result
                self._mqtt_unsubs.append(unsub)
        except Exception as err:
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

    async def async_update_data(
        self,
        *,
        current_data: dict[str, Any],
        device_name: str,
    ) -> dict[str, Any] | None:
        return None

    async def async_send_command(self, values: dict[str, Any]) -> dict[str, Any]:
        await self._async_publish_mqtt(values)
        return dict(values)

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
        payload = encode_payload({"mac": self.mac, **values}).decode()
        result = mqtt.async_publish(
            self.hass, topics.command, payload, qos=0, retain=False
        )
        if inspect.isawaitable(result):
            await result
