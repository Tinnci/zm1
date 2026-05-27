"""zM1 JSON protocol helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

DEFAULT_MQTT_BASE_TOPIC = "device/zm1"
MAX_PACKET_BYTES = 1023
_MAC_RE = re.compile(r"^[0-9a-f]{12}$")


class ZM1ProtocolError(ValueError):
    """Raised when a zM1 packet is not valid."""


@dataclass(frozen=True)
class ZM1MqttTopics:
    """MQTT topic set used by zM1."""

    command: str
    state: str
    sensor: str


def normalize_mac(mac: str) -> str:
    """Normalize a MAC address to lowercase without separators."""
    normalized = mac.strip().lower().replace(":", "").replace("-", "")
    if not _MAC_RE.fullmatch(normalized):
        raise ZM1ProtocolError("MAC must contain 12 hexadecimal characters")
    return normalized


def build_command(mac: str, values: dict[str, Any]) -> dict[str, Any]:
    """Build a zM1 command payload."""
    payload = {"mac": normalize_mac(mac)}
    payload.update(values)
    return payload


def build_query(mac: str, *fields: str) -> dict[str, Any]:
    """Build a query payload where queried fields are set to JSON null."""
    if not fields:
        raise ZM1ProtocolError("At least one query field is required")
    return build_command(mac, {field: None for field in fields})


def encode_payload(payload: dict[str, Any]) -> bytes:
    """Encode a zM1 JSON payload, enforcing the documented packet limit."""
    if "cmd" not in payload:
        if "mac" not in payload:
            raise ZM1ProtocolError("zM1 payloads must include a mac field")
        payload = dict(payload)
        payload["mac"] = normalize_mac(str(payload["mac"]))

    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > MAX_PACKET_BYTES:
        raise ZM1ProtocolError("zM1 payload exceeds 1023 bytes")
    return encoded


def decode_payload(data: bytes | str) -> dict[str, Any]:
    """Decode a zM1 JSON response."""
    if isinstance(data, bytes):
        text = data.decode()
    else:
        text = data
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as err:
        raise ZM1ProtocolError("zM1 response is not valid JSON") from err

    if not isinstance(decoded, dict):
        raise ZM1ProtocolError("zM1 response must be a JSON object")
    if "mac" in decoded:
        decoded = dict(decoded)
        decoded["mac"] = normalize_mac(str(decoded["mac"]))
    return decoded


def build_discovery_command() -> dict[str, str]:
    """Build the documented UDP broadcast discovery command."""
    return {"cmd": "device report"}


def build_mqtt_topics(mac: str, base_topic: str = DEFAULT_MQTT_BASE_TOPIC) -> ZM1MqttTopics:
    """Build zM1 MQTT command, state, and sensor topics."""
    mac = normalize_mac(mac)
    base = base_topic.strip().strip("/")
    if not base:
        raise ZM1ProtocolError("MQTT base topic must not be empty")
    root = f"{base}/{mac}"
    return ZM1MqttTopics(
        command=f"{root}/set",
        state=f"{root}/state",
        sensor=f"{root}/sensor",
    )

