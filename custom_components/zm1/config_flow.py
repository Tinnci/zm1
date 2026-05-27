"""Config flow for zM1."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_MAC,
    CONF_MQTT_BASE_TOPIC,
    CONF_TRANSPORT,
    CONF_UDP_COMMAND_PORT,
    CONF_UDP_RESPONSE_PORT,
    DEFAULT_MQTT_BASE_TOPIC,
    DEFAULT_UDP_COMMAND_PORT,
    DEFAULT_UDP_RESPONSE_PORT,
    DOMAIN,
    TRANSPORT_MQTT,
    TRANSPORT_UDP,
    TRANSPORTS,
)
from .protocol import ZM1ProtocolError, normalize_mac
from .udp import ZM1Error, ZM1UDPClient

_LOGGER = logging.getLogger(__name__)


class ZM1ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a zM1 config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data = dict(user_input)
            try:
                data[CONF_MAC] = normalize_mac(data[CONF_MAC])
            except ZM1ProtocolError:
                errors[CONF_MAC] = "invalid_mac"

            host = str(data.get(CONF_HOST) or "").strip()
            data[CONF_HOST] = host
            transport = data[CONF_TRANSPORT]

            if transport == TRANSPORT_UDP and not host:
                errors[CONF_HOST] = "host_required"

            response: dict[str, Any] = {}
            if not errors and transport == TRANSPORT_UDP:
                client = ZM1UDPClient(
                    host,
                    data[CONF_MAC],
                    command_port=data[CONF_UDP_COMMAND_PORT],
                    response_port=data[CONF_UDP_RESPONSE_PORT],
                )
                try:
                    response = await client.query("version", "name", "brightness")
                except ZM1Error as err:
                    _LOGGER.debug("Unable to validate zM1 UDP device", exc_info=err)
                    errors["base"] = "cannot_connect"

            if not errors:
                await self.async_set_unique_id(data[CONF_MAC])
                self._abort_if_unique_id_configured()

                name = data.get(CONF_NAME) or response.get("name") or f"zM1 {data[CONF_MAC][-4:].upper()}"
                data[CONF_NAME] = name
                if not data.get(CONF_MQTT_BASE_TOPIC):
                    data[CONF_MQTT_BASE_TOPIC] = DEFAULT_MQTT_BASE_TOPIC

                return self.async_create_entry(title=name, data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAC): str,
                    vol.Optional(CONF_NAME, default=""): str,
                    vol.Required(CONF_TRANSPORT, default=TRANSPORT_UDP): vol.In(TRANSPORTS),
                    vol.Optional(CONF_HOST, default=""): str,
                    vol.Optional(CONF_UDP_COMMAND_PORT, default=DEFAULT_UDP_COMMAND_PORT): cv.port,
                    vol.Optional(CONF_UDP_RESPONSE_PORT, default=DEFAULT_UDP_RESPONSE_PORT): cv.port,
                    vol.Optional(CONF_MQTT_BASE_TOPIC, default=DEFAULT_MQTT_BASE_TOPIC): str,
                }
            ),
            errors=errors,
        )

