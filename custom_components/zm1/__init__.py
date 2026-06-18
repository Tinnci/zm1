"""Home Assistant integration for zM1 devices."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_MQTT_PASSWORD,
    ATTR_MQTT_PORT,
    ATTR_MQTT_URI,
    ATTR_MQTT_USER,
    ATTR_OTA_URL,
    ATTR_PAYLOAD,
    CONF_MAC,
    CONF_TRANSPORT,
    DOMAIN,
    PLATFORMS,
    SERVICE_CONFIGURE_MQTT,
    SERVICE_OTA_UPDATE,
    SERVICE_SEND_COMMAND,
    TRANSPORT_UDP,
)
from .coordinator import ZM1Coordinator
from .protocol import normalize_mac
from .repairs import async_create_udp_response_issue

_LOGGER = logging.getLogger(__name__)

ZM1ConfigEntry = ConfigEntry[ZM1Coordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up zM1 services."""
    await _async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ZM1ConfigEntry) -> bool:
    """Set up zM1 from a config entry."""
    coordinator = ZM1Coordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        if coordinator.transport != TRANSPORT_UDP:
            raise
        _LOGGER.warning(
            "zM1 %s was discovered, but the first UDP state query timed out. "
            "The device entry will be created and regular polling will retry. "
            "If it stays unavailable, ensure Home Assistant can receive UDP port 10181",
            coordinator.device_name,
        )
        async_create_udp_response_issue(
            hass,
            entry_id=entry.entry_id,
            device_name=coordinator.device_name,
            response_port=coordinator.response_port,
        )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZM1ConfigEntry) -> bool:
    """Unload a zM1 config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: ZM1Coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    return unload_ok


async def _async_update_options(hass: HomeAssistant, entry: ZM1ConfigEntry) -> None:
    """Reload zM1 when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_setup_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):
        return

    async def handle_send_command(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call)
        response = await coordinator.async_send_command(dict(call.data[ATTR_PAYLOAD]))
        return (response or {}) if call.return_response else None

    async def handle_configure_mqtt(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call)
        response = await coordinator.async_configure_mqtt(
            mqtt_uri=call.data[ATTR_MQTT_URI],
            mqtt_port=call.data.get(ATTR_MQTT_PORT, 1883),
            mqtt_user=call.data.get(ATTR_MQTT_USER),
            mqtt_password=call.data.get(ATTR_MQTT_PASSWORD),
        )
        return (response or {}) if call.return_response else None

    async def handle_ota_update(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call)
        response = await coordinator.async_start_ota(call.data[ATTR_OTA_URL])
        return (response or {}) if call.return_response else None

    selector_schema = {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(CONF_MAC): cv.string,
    }

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        handle_send_command,
        schema=vol.Schema({**selector_schema, vol.Required(ATTR_PAYLOAD): dict}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CONFIGURE_MQTT,
        handle_configure_mqtt,
        schema=vol.Schema(
            {
                **selector_schema,
                vol.Required(ATTR_MQTT_URI): cv.string,
                vol.Optional(ATTR_MQTT_PORT, default=1883): cv.port,
                vol.Optional(ATTR_MQTT_USER): cv.string,
                vol.Optional(ATTR_MQTT_PASSWORD): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_OTA_UPDATE,
        handle_ota_update,
        schema=vol.Schema({**selector_schema, vol.Required(ATTR_OTA_URL): cv.url}),
        supports_response=SupportsResponse.OPTIONAL,
    )


def _get_coordinator(hass: HomeAssistant, call: ServiceCall) -> ZM1Coordinator:
    coordinators: dict[str, ZM1Coordinator] = hass.data.get(DOMAIN, {})
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    mac = call.data.get(CONF_MAC)

    if entry_id:
        if entry_id not in coordinators:
            raise HomeAssistantError(f"No zM1 config entry found for {entry_id}")
        return coordinators[entry_id]

    if mac:
        normalized = normalize_mac(mac)
        for coordinator in coordinators.values():
            if coordinator.mac == normalized:
                return coordinator
        raise HomeAssistantError(f"No zM1 device found for MAC {normalized}")

    if len(coordinators) == 1:
        return next(iter(coordinators.values()))

    raise HomeAssistantError("Specify config_entry_id or mac")
