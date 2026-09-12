"""Field observations detached from dispatch, polling and publication time."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any

OBSERVATION_TTL = 300
SENSOR_FIELDS = frozenset({"temperature", "humidity", "formaldehyde", "pm25"})
EXPIRING_FIELDS = SENSOR_FIELDS | {"brightness", "ota_progress"}
_ALIASES = {"PM25": "pm25"}


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def merge_report(
    current: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
    source: str,
) -> Mapping[str, Any]:
    """Stamp only fields actually received, retaining prior valid samples on errors."""
    fields = {key: value for key, value in payload.items() if not key.startswith("_")}
    if not fields:
        return current
    data = dict(current)
    timestamps = dict(current.get("_observed_at", {}))
    sources = dict(current.get("_observation_sources", {}))
    for key, value in fields.items():
        key = _ALIASES.get(key, key)
        previous = timestamps.get(key)
        if previous is not None and previous > received_at:
            continue
        if key in EXPIRING_FIELDS:
            value = _numeric(value, key)
        if value is None:
            continue
        data[key] = value
        timestamps[key] = received_at
        sources[key] = source
    data["_observed_at"] = timestamps
    data["_observation_sources"] = sources
    data["_last_seen"] = max(received_at, current.get("_last_seen", received_at))
    return _freeze(data)


def field_is_fresh(data: Mapping[str, Any], field: str, *, now: datetime) -> bool:
    """Polling success or another field's report cannot renew a measurement."""
    observed_at = data.get("_observed_at", {}).get(field)
    return observed_at is not None and 0 <= (now - observed_at).total_seconds() < OBSERVATION_TTL


def _numeric(value: Any, field: str) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if field != "temperature" and number < 0:
        return None
    if field in {"humidity", "ota_progress"} and number > 100:
        return None
    if field == "brightness" and (number > 4 or not number.is_integer()):
        return None
    return number
