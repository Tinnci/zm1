"""Persistent, shared UDP reception for the zM1 broadcast protocol."""

from __future__ import annotations

import asyncio
import copy
import logging
import socket
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

try:
    from .protocol import (
        SENSOR_REPORT_FIELDS,
        build_command,
        build_discovery_command,
        build_query,
        decode_payload,
        encode_payload,
        normalize_mac,
    )
except ImportError:  # Allows direct unittest imports without Home Assistant.
    from protocol import (
        SENSOR_REPORT_FIELDS,
        build_command,
        build_discovery_command,
        build_query,
        decode_payload,
        encode_payload,
        normalize_mac,
    )

_LOGGER = logging.getLogger(__name__)
ReportHandler = Callable[[dict[str, Any], datetime], None]
DatagramHandler = Callable[[dict[str, Any], tuple[str, int], datetime], None]


class ZM1Error(Exception):
    """Base zM1 transport error."""


class ZM1TimeoutError(ZM1Error):
    """A request had no matching report before its deadline."""


class _UDPListener(asyncio.DatagramProtocol):
    """One socket per event loop and local address, shared by all devices."""

    _listeners: dict[tuple[asyncio.AbstractEventLoop, str, int], _UDPListener] = {}

    def __init__(self, key: tuple[asyncio.AbstractEventLoop, str, int]) -> None:
        self.key = key
        self.transport: asyncio.DatagramTransport | None = None
        self.handlers: set[DatagramHandler] = set()
        self.request_locks: dict[str, asyncio.Lock] = {}
        self._users = 0
        self._open_lock = asyncio.Lock()
        self._closed: asyncio.Future[None] = key[0].create_future()
        self._closing = False

    @classmethod
    async def acquire(cls, bind_host: str, response_port: int) -> _UDPListener:
        loop = asyncio.get_running_loop()
        key = (loop, bind_host, response_port)
        listener = cls._listeners.get(key)
        if listener is not None and listener._closing:
            await asyncio.shield(listener._closed)
            return await cls.acquire(bind_host, response_port)
        if listener is None:
            listener = cls(key)
            cls._listeners[key] = listener
        listener._users += 1
        try:
            async with listener._open_lock:
                if listener.transport is None:
                    await loop.create_datagram_endpoint(
                        lambda: listener,
                        local_addr=(bind_host, response_port),
                        family=socket.AF_INET,
                        allow_broadcast=True,
                    )
        except BaseException:
            await listener.release()
            raise
        return listener

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = cast(asyncio.DatagramTransport, transport)

    def connection_lost(self, exc: Exception | None) -> None:
        # Socket cleanup must complete even if the last owner's unload was cancelled.
        if self._listeners.get(self.key) is self:
            del self._listeners[self.key]
        self.handlers.clear()
        if not self._closed.done():
            self._closed.set_result(None)

    def error_received(self, exc: Exception) -> None:
        # Unconnected UDP errors cannot be assigned to a particular request.
        _LOGGER.debug("zM1 UDP socket error: %s", exc)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        received_at = datetime.now(UTC)
        try:
            payload = decode_payload(data)
        except (ValueError, UnicodeError):
            return
        if "mac" not in payload:
            return
        for handler in tuple(self.handlers):
            handler(copy.deepcopy(payload), addr, received_at)

    def send(self, data: bytes, host: str, port: int) -> None:
        if self.transport is None or self._closing:
            raise ZM1Error("zM1 UDP listener is closed")
        self.transport.sendto(data, (host, port))

    async def release(self) -> None:
        self._users -= 1
        if self._users:
            return
        self._closing = True
        if self.transport is not None:
            self.transport.close()
            await asyncio.shield(self._closed)
        elif not self._closed.done():
            self._closed.set_result(None)
        if self._listeners.get(self.key) is self:
            del self._listeners[self.key]
        self.handlers.clear()


class ZM1UDPClient:
    """Observe every report while serializing requests for one device."""

    def __init__(
        self,
        host: str,
        mac: str,
        *,
        command_port: int = 10182,
        response_port: int = 10181,
        timeout: float = 3.0,
        bind_host: str = "0.0.0.0",
        on_report: ReportHandler | None = None,
    ) -> None:
        self.host = host
        self.mac = normalize_mac(mac)
        self.command_port = command_port
        self.response_port = response_port
        self.timeout = timeout
        self.bind_host = bind_host
        self.last_received_monotonic: float | None = None
        self._last_sensor_report: dict[str, Any] = {}
        self._on_report = on_report
        self._listener: _UDPListener | None = None
        self._start_lock = asyncio.Lock()
        self._closed = False
        self._pending: asyncio.Future[dict[str, Any]] | None = None
        self._expected: dict[str, Any] = {}
        self._response: dict[str, Any] = {}
        self._sensor_waiters: set[asyncio.Future[dict[str, Any]]] = set()

    @property
    def last_sensor_report(self) -> dict[str, Any]:
        """Return a detached copy; this cache never constitutes a new report."""
        return copy.deepcopy(self._last_sensor_report)

    async def async_start(self) -> None:
        """Keep the response socket open until this client is closed."""
        async with self._start_lock:
            if self._closed:
                raise ZM1Error("zM1 UDP client is closed")
            if self._listener is not None:
                return
            try:
                self._listener = await _UDPListener.acquire(self.bind_host, self.response_port)
            except OSError as err:
                raise ZM1Error(str(err)) from err
            self._listener.handlers.add(self._handle_report)

    async def async_close(self) -> None:
        """Release this device without interrupting other devices on the port."""
        self._closed = True
        async with self._start_lock:
            if self._pending is not None and not self._pending.done():
                self._pending.set_exception(ZM1Error("zM1 UDP client closed"))
            for waiter in self._sensor_waiters:
                if not waiter.done():
                    waiter.set_result({})
            if self._listener is not None:
                listener, self._listener = self._listener, None
                listener.handlers.discard(self._handle_report)
                await listener.release()

    async def send(self, values: dict[str, Any]) -> dict[str, Any]:
        """Dispatch once and await matching reported values, never physical proof."""
        return await self._request(build_command(self.mac, values), values)

    async def query(self, *fields: str) -> dict[str, Any]:
        """Await all requested fields, allowing reports in separate packets."""
        return await self._request(build_query(self.mac, *fields), dict.fromkeys(fields))

    async def _request(self, payload: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
        expected = copy.deepcopy(expected)
        data = encode_payload(payload)
        if not expected or "mac" in expected:
            raise ZM1Error("A request must contain fields other than mac")
        await self.async_start()
        listener = self._listener
        assert listener is not None
        if not self.host:
            raise ZM1Error("No zM1 UDP host is known")
        lock = listener.request_locks.setdefault(self.mac, asyncio.Lock())
        async with lock:
            self._expected = expected
            self._response = {"mac": self.mac}
            try:
                async with asyncio.timeout(self.timeout):
                    try:
                        socket.inet_aton(self.host)
                    except OSError:
                        addresses = await asyncio.get_running_loop().getaddrinfo(
                            self.host, self.command_port, family=socket.AF_INET, type=socket.SOCK_DGRAM
                        )
                        self.host = addresses[0][4][0]
                    self._pending = asyncio.get_running_loop().create_future()
                    listener.send(data, self.host, self.command_port)
                    return await self._pending
            except TimeoutError as err:
                raise ZM1TimeoutError("Timed out waiting for matching zM1 fields") from err
            except OSError as err:
                raise ZM1Error(str(err)) from err
            finally:
                self._pending = None
                self._expected = {}
                self._response = {}

    def _handle_report(self, payload: dict[str, Any], addr: tuple[str, int], received_at: datetime) -> None:
        if payload.get("mac") != self.mac or (self.host and addr[0] != self.host):
            return
        self.host = addr[0]
        self.last_received_monotonic = time.monotonic()
        if SENSOR_REPORT_FIELDS.intersection(payload):
            self._last_sensor_report = copy.deepcopy(payload)
            for waiter in self._sensor_waiters:
                if not waiter.done():
                    waiter.set_result(copy.deepcopy(payload))
        if self._on_report is not None:
            self._on_report(copy.deepcopy(payload), received_at)
        if self._pending is None or self._pending.done():
            return

        for field, value in payload.items():
            if field not in self._expected:
                self._response[field] = copy.deepcopy(value)
            elif _matches_reported_value(value, self._expected[field]):
                self._response[field] = copy.deepcopy(value)
            else:
                self._response.pop(field, None)
        if self._expected.keys() <= self._response.keys():
            self._pending.set_result(copy.deepcopy(self._response))

    async def read_sensor_report(self, *, timeout: float = 5.5) -> dict[str, Any]:
        """Wait for the next report without opening another socket."""
        await self.async_start()
        waiter: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._sensor_waiters.add(waiter)
        try:
            return await asyncio.wait_for(waiter, timeout)
        except TimeoutError:
            return {}
        finally:
            self._sensor_waiters.discard(waiter)

    async def configure_mqtt(
        self,
        *,
        mqtt_uri: str,
        mqtt_port: int = 1883,
        mqtt_user: str | None = None,
        mqtt_password: str | None = None,
    ) -> dict[str, Any]:
        """Configure device-side MQTT settings through UDP."""
        setting: dict[str, Any] = {"mqtt_uri": mqtt_uri, "mqtt_port": mqtt_port}
        if mqtt_user is not None:
            setting["mqtt_user"] = mqtt_user
        if mqtt_password is not None:
            setting["mqtt_password"] = mqtt_password
        return await self.send({"setting": setting})

    async def start_ota(self, ota_url: str) -> dict[str, Any]:
        """Send an OTA request once; its echo does not prove update completion."""
        return await self.send({"setting": {"ota": ota_url}})


def _matches_reported_value(value: Any, expected: Any) -> bool:
    """Match requested settings without rejecting additional reported settings."""
    if expected is None:
        return value is not None
    if isinstance(expected, dict):
        return isinstance(value, dict) and all(
            key in value and _matches_reported_value(value[key], item) for key, item in expected.items()
        )
    if isinstance(value, bool) or isinstance(expected, bool):
        return type(value) is type(expected) and value == expected
    return bool(value == expected)


async def discover(
    *,
    broadcast_address: str = "255.255.255.255",
    command_port: int = 10182,
    response_port: int = 10181,
    timeout: float = 3.0,
    bind_host: str = "0.0.0.0",
) -> list[dict[str, Any]]:
    """Collect discovery reports on the shared listener for one fixed window."""
    responses: dict[str, dict[str, Any]] = {}

    def on_report(payload: dict[str, Any], addr: tuple[str, int], received_at: datetime) -> None:
        if "type" in payload or "type_name" in payload or "name" in payload:
            responses[payload["mac"]] = {**payload, "_addr": addr[0]}

    try:
        listener = await _UDPListener.acquire(bind_host, response_port)
        listener.handlers.add(on_report)
        try:
            listener.send(encode_payload(build_discovery_command()), broadcast_address, command_port)
            await asyncio.sleep(timeout)
        finally:
            listener.handlers.discard(on_report)
            await listener.release()
    except OSError as err:
        raise ZM1Error(str(err)) from err
    return list(responses.values())


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
