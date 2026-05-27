"""mDNS helpers for zM1."""

from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import dataclass
from typing import Any

from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf

from .const import DEFAULT_TIMEOUT, ZM1_ZEROCONF_TYPE
from .protocol import normalize_mac


@dataclass(frozen=True)
class ZM1MdnsInfo:
    """zM1 mDNS service information."""

    host: str
    port: int
    name: str
    properties: dict[str, Any]


async def discover_mdns(mac: str, *, timeout: float = DEFAULT_TIMEOUT) -> ZM1MdnsInfo | None:
    """Find a zM1 device by mDNS TXT mac property."""
    return await asyncio.to_thread(_discover_mdns_sync, normalize_mac(mac), timeout)


def _discover_mdns_sync(mac: str, timeout: float) -> ZM1MdnsInfo | None:
    zeroconf = Zeroconf()
    result: ZM1MdnsInfo | None = None
    deadline = time.monotonic() + timeout

    def on_service(**kwargs: Any) -> None:
        nonlocal result
        if result is not None:
            return
        if kwargs.get("state_change") is not ServiceStateChange.Added:
            return

        service_type = kwargs.get("service_type")
        name = kwargs.get("name")
        if not service_type or not name:
            return

        info = zeroconf.get_service_info(service_type, name, timeout=1500)
        if info is None:
            return

        properties = _decode_properties(info.properties)
        try:
            service_mac = normalize_mac(str(properties.get("mac", "")))
        except ValueError:
            return
        if service_mac != mac:
            return

        addresses: list[str] = []
        for raw in info.addresses:
            try:
                addresses.append(socket.inet_ntoa(raw))
            except OSError:
                continue
        if not addresses:
            return

        result = ZM1MdnsInfo(
            host=addresses[0],
            port=info.port,
            name=str(name),
            properties=properties,
        )

    try:
        ServiceBrowser(zeroconf, ZM1_ZEROCONF_TYPE, handlers=[on_service])
        while result is None and time.monotonic() < deadline:
            time.sleep(0.05)
        return result
    finally:
        zeroconf.close()


def _decode_properties(properties: dict[bytes, bytes | None]) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in properties.items():
        decoded_key = key.decode(errors="replace") if isinstance(key, bytes) else str(key)
        if value is None:
            decoded[decoded_key] = None
        elif isinstance(value, bytes):
            decoded[decoded_key] = value.decode(errors="replace")
        else:
            decoded[decoded_key] = value
    return decoded
