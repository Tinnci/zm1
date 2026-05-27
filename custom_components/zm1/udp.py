"""UDP transport for zM1."""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

try:
    from .const import SENSOR_REPORT_FIELDS
    from .protocol import build_command, build_discovery_command, build_query, decode_payload, encode_payload, normalize_mac
except ImportError:  # Allows direct unittest imports without Home Assistant installed.
    SENSOR_REPORT_FIELDS = {
        "temperature",
        "humidity",
        "formaldehyde",
        "PM25",
        "pm25",
        "TVOC",
        "tvoc",
        "CO2",
        "co2",
        "eCO2",
        "eco2",
    }
    from protocol import build_command, build_discovery_command, build_query, decode_payload, encode_payload, normalize_mac


class ZM1Error(Exception):
    """Base zM1 transport error."""


class ZM1TimeoutError(ZM1Error):
    """Raised when the zM1 device does not respond in time."""


class ZM1UDPClient:
    """Small UDP client for the zM1 JSON protocol."""

    def __init__(
        self,
        host: str,
        mac: str,
        *,
        command_port: int = 10182,
        response_port: int = 10181,
        timeout: float = 3.0,
        bind_host: str = "0.0.0.0",
    ) -> None:
        self.host = host
        self.mac = mac
        self.command_port = command_port
        self.response_port = response_port
        self.timeout = timeout
        self.bind_host = bind_host
        self.last_sensor_report: dict[str, Any] = {}

    async def send(self, values: dict[str, Any]) -> dict[str, Any]:
        """Send a zM1 command and wait for the JSON response."""
        payload = build_command(self.mac, values)
        expected_fields = {field for field in values if field != "setting"}
        return await asyncio.to_thread(
            self._send_sync,
            payload,
            self.host,
            self.command_port,
            expected_fields,
        )

    async def query(self, *fields: str) -> dict[str, Any]:
        """Query one or more zM1 fields."""
        payload = build_query(self.mac, *fields)
        return await asyncio.to_thread(
            self._send_sync,
            payload,
            self.host,
            self.command_port,
            set(fields),
        )

    async def configure_mqtt(
        self,
        *,
        mqtt_uri: str,
        mqtt_port: int = 1883,
        mqtt_user: str | None = None,
        mqtt_password: str | None = None,
    ) -> dict[str, Any]:
        """Configure device-side MQTT settings through UDP."""
        setting: dict[str, Any] = {
            "mqtt_uri": mqtt_uri,
            "mqtt_port": mqtt_port,
        }
        if mqtt_user is not None:
            setting["mqtt_user"] = mqtt_user
        if mqtt_password is not None:
            setting["mqtt_password"] = mqtt_password
        return await self.send({"setting": setting})

    async def start_ota(self, ota_url: str) -> dict[str, Any]:
        """Start a device OTA update through UDP."""
        return await self.send({"setting": {"ota": ota_url}})

    async def read_sensor_report(self, *, timeout: float = 5.5) -> dict[str, Any]:
        """Wait for an unsolicited zM1 sensor report."""
        return await asyncio.to_thread(self._read_sensor_report_sync, timeout)

    def _send_sync(
        self,
        payload: dict[str, Any],
        host: str,
        port: int,
        expected_fields: set[str],
    ) -> dict[str, Any]:
        data = encode_payload(payload)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            deadline = time.monotonic() + self.timeout
            sock.settimeout(self.timeout)
            sock.bind((self.bind_host, self.response_port))
            sock.sendto(data, (host, port))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout
                sock.settimeout(remaining)
                response, _addr = sock.recvfrom(1024)
                payload = decode_payload(response)
                if payload.get("mac") != self.mac:
                    continue
                self._record_sensor_report(payload)
                if not expected_fields or expected_fields.intersection(payload):
                    return payload
        except socket.timeout as err:
            raise ZM1TimeoutError("Timed out waiting for zM1 response") from err
        except OSError as err:
            raise ZM1Error(str(err)) from err
        finally:
            sock.close()

    def _read_sensor_report_sync(self, timeout: float) -> dict[str, Any]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.settimeout(timeout)
            sock.bind((self.bind_host, self.response_port))
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout
                sock.settimeout(remaining)
                response, _addr = sock.recvfrom(1024)
                payload = decode_payload(response)
                if payload.get("mac") != self.mac:
                    continue
                if self._record_sensor_report(payload):
                    return payload
        except socket.timeout:
            return {}
        except OSError as err:
            raise ZM1Error(str(err)) from err
        finally:
            sock.close()

    def _record_sensor_report(self, payload: dict[str, Any]) -> bool:
        if not SENSOR_REPORT_FIELDS.intersection(payload):
            return False
        self.last_sensor_report = payload
        return True


async def discover(
    *,
    broadcast_address: str = "255.255.255.255",
    command_port: int = 10182,
    response_port: int = 10181,
    timeout: float = 3.0,
) -> list[dict[str, Any]]:
    """Broadcast the documented discovery command and collect responses."""
    return await asyncio.to_thread(
        _discover_sync,
        broadcast_address,
        command_port,
        response_port,
        timeout,
    )


def find_discovered_host(responses: list[dict[str, Any]], mac: str) -> str | None:
    """Return the source address for a discovered zM1 device."""
    normalized_mac = normalize_mac(mac)
    for response in responses:
        if response.get("mac") != normalized_mac:
            continue
        host = response.get("_addr")
        if isinstance(host, str) and host:
            return host
    return None


def _discover_sync(
    broadcast_address: str,
    command_port: int,
    response_port: int,
    timeout: float,
) -> list[dict[str, Any]]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    responses: list[dict[str, Any]] = []
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(timeout)
        sock.bind(("0.0.0.0", response_port))
        sock.sendto(encode_payload(build_discovery_command()), (broadcast_address, command_port))
        while True:
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                break
            payload = decode_payload(data)
            payload["_addr"] = addr[0]
            responses.append(payload)
    finally:
        sock.close()
    return responses
