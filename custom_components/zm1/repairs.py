"""Repair issue helpers for zM1."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

ISSUE_MQTT_NOT_READY = "mqtt_not_ready"
ISSUE_UDP_RESPONSE_UNAVAILABLE = "udp_response_unavailable"


def async_create_udp_response_issue(
    hass: HomeAssistant,
    *,
    entry_id: str,
    device_name: str,
    response_port: int,
) -> None:
    """Create a repair issue for missing zM1 UDP responses."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(ISSUE_UDP_RESPONSE_UNAVAILABLE, entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_UDP_RESPONSE_UNAVAILABLE,
        translation_placeholders={
            "device_name": device_name,
            "response_port": str(response_port),
        },
    )


def async_create_mqtt_not_ready_issue(
    hass: HomeAssistant,
    *,
    entry_id: str,
    device_name: str,
) -> None:
    """Create a repair issue when Home Assistant MQTT is not ready."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(ISSUE_MQTT_NOT_READY, entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_MQTT_NOT_READY,
        translation_placeholders={"device_name": device_name},
    )


def async_delete_issue(hass: HomeAssistant, issue_id: str, entry_id: str) -> None:
    """Delete a zM1 repair issue if it exists."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(issue_id, entry_id))


def _issue_id(base_issue_id: str, entry_id: str) -> str:
    return f"{base_issue_id}_{entry_id}"
