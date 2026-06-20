"""Config flow for zM1."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    CONF_MAC,
    CONF_LAST_HOST,
    CONF_MQTT_BASE_TOPIC,
    CONF_SCAN_INTERVAL,
    CONF_TRANSPORT,
    CONF_UDP_COMMAND_PORT,
    CONF_UDP_RESPONSE_PORT,
    CONF_ZEROCONF_NAME,
    DEFAULT_MQTT_BASE_TOPIC,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UDP_COMMAND_PORT,
    DEFAULT_UDP_RESPONSE_PORT,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    TRANSPORT_UDP,
    TRANSPORTS,
    ZM1_ZEROCONF_TYPE,
)
from .protocol import ZM1ProtocolError, normalize_mac
from .udp import ZM1Error, ZM1UDPClient, discover, find_discovered_host

_LOGGER = logging.getLogger(__name__)


class ZM1ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a zM1 config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_data: dict[str, Any] = {}
        self._discovered_title = ""

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return ZM1OptionsFlow(config_entry)

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> FlowResult:
        """Handle zM1 mDNS discovery."""
        try:
            mac = normalize_mac(str(discovery_info.properties["mac"]))
        except (KeyError, ZM1ProtocolError):
            return self.async_abort(reason="invalid_mac")

        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured(
            updates={CONF_ZEROCONF_NAME: discovery_info.name}
        )

        port = discovery_info.port or DEFAULT_UDP_COMMAND_PORT
        title = _discovery_title(discovery_info.name, mac)
        self._discovered_title = title
        self._discovered_data = {
            CONF_MAC: mac,
            CONF_NAME: title,
            CONF_TRANSPORT: TRANSPORT_UDP,
            CONF_HOST: "",
            CONF_LAST_HOST: str(discovery_info.ip_address),
            CONF_UDP_COMMAND_PORT: port,
            CONF_UDP_RESPONSE_PORT: DEFAULT_UDP_RESPONSE_PORT,
            CONF_MQTT_BASE_TOPIC: DEFAULT_MQTT_BASE_TOPIC,
            CONF_ZEROCONF_NAME: discovery_info.name,
        }
        self.context["title_placeholders"] = {"name": title}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm zM1 mDNS discovery."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered_title,
                data=self._discovered_data,
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"name": self._discovered_title},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
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

            response: dict[str, Any] = {}
            if not errors and transport == TRANSPORT_UDP:
                validation_host = host
                if not validation_host:
                    responses = await discover(
                        command_port=data[CONF_UDP_COMMAND_PORT],
                        response_port=data[CONF_UDP_RESPONSE_PORT],
                    )
                    validation_host = (
                        find_discovered_host(responses, data[CONF_MAC]) or ""
                    )
                    response = next(
                        (
                            item
                            for item in responses
                            if item.get("mac") == data[CONF_MAC]
                        ),
                        {},
                    )

                if not validation_host:
                    errors["base"] = "cannot_connect"
                    return self.async_show_form(
                        step_id="user",
                        data_schema=_config_schema(),
                        errors=errors,
                    )

                client = ZM1UDPClient(
                    validation_host,
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

                name = (
                    data.get(CONF_NAME)
                    or response.get("name")
                    or f"zM1 {data[CONF_MAC][-4:].upper()}"
                )
                data[CONF_NAME] = name
                if not data.get(CONF_MQTT_BASE_TOPIC):
                    data[CONF_MQTT_BASE_TOPIC] = DEFAULT_MQTT_BASE_TOPIC
                data.setdefault(CONF_ZEROCONF_NAME, "")
                data.setdefault(CONF_LAST_HOST, "")

                return self.async_create_entry(title=name, data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=_config_schema(),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration of connection settings."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {
                **entry.data,
                **user_input,
                CONF_MAC: entry.data[CONF_MAC],
                CONF_NAME: entry.data.get(CONF_NAME, entry.title),
            }
            data[CONF_HOST] = str(data.get(CONF_HOST) or "").strip()
            if not data.get(CONF_MQTT_BASE_TOPIC):
                data[CONF_MQTT_BASE_TOPIC] = DEFAULT_MQTT_BASE_TOPIC

            if data[CONF_TRANSPORT] == TRANSPORT_UDP:
                validation_host = data[CONF_HOST]
                if not validation_host:
                    responses = await discover(
                        command_port=data[CONF_UDP_COMMAND_PORT],
                        response_port=data[CONF_UDP_RESPONSE_PORT],
                    )
                    validation_host = (
                        find_discovered_host(responses, data[CONF_MAC]) or ""
                    )

                if not validation_host:
                    errors["base"] = "cannot_connect"
                else:
                    client = ZM1UDPClient(
                        validation_host,
                        data[CONF_MAC],
                        command_port=data[CONF_UDP_COMMAND_PORT],
                        response_port=data[CONF_UDP_RESPONSE_PORT],
                    )
                    try:
                        await client.query("version", "name", "brightness")
                    except ZM1Error as err:
                        _LOGGER.debug("Unable to validate zM1 UDP device", exc_info=err)
                        errors["base"] = "cannot_connect"

            if not errors:
                await self.async_set_unique_id(entry.unique_id or data[CONF_MAC])
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_TRANSPORT: data[CONF_TRANSPORT],
                        CONF_HOST: data[CONF_HOST],
                        CONF_UDP_COMMAND_PORT: data[CONF_UDP_COMMAND_PORT],
                        CONF_UDP_RESPONSE_PORT: data[CONF_UDP_RESPONSE_PORT],
                        CONF_MQTT_BASE_TOPIC: data[CONF_MQTT_BASE_TOPIC],
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_reconfigure_schema(entry),
            errors=errors,
        )


class ZM1OptionsFlow(config_entries.OptionsFlow):
    """Handle zM1 options."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage zM1 options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=dict(user_input))

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(self.entry),
        )


def _config_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_MAC): str,
            vol.Optional(CONF_NAME, default=""): str,
            vol.Required(CONF_TRANSPORT, default=TRANSPORT_UDP): vol.In(TRANSPORTS),
            vol.Optional(CONF_HOST, default=""): str,
            vol.Optional(
                CONF_UDP_COMMAND_PORT, default=DEFAULT_UDP_COMMAND_PORT
            ): cv.port,
            vol.Optional(
                CONF_UDP_RESPONSE_PORT, default=DEFAULT_UDP_RESPONSE_PORT
            ): cv.port,
            vol.Optional(CONF_MQTT_BASE_TOPIC, default=DEFAULT_MQTT_BASE_TOPIC): str,
        }
    )


def _options_schema(entry: config_entries.ConfigEntry) -> vol.Schema:
    options = entry.options
    scan_interval = max(
        MIN_SCAN_INTERVAL,
        min(options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL), MAX_SCAN_INTERVAL),
    )
    return vol.Schema(
        {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=scan_interval,
            ): vol.All(
                cv.positive_int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
            ),
        }
    )


def _reconfigure_schema(entry: config_entries.ConfigEntry) -> vol.Schema:
    data = entry.data
    return vol.Schema(
        {
            vol.Required(
                CONF_TRANSPORT,
                default=data.get(CONF_TRANSPORT, TRANSPORT_UDP),
            ): vol.In(TRANSPORTS),
            vol.Optional(CONF_HOST, default=data.get(CONF_HOST, "")): str,
            vol.Optional(
                CONF_UDP_COMMAND_PORT,
                default=data.get(CONF_UDP_COMMAND_PORT, DEFAULT_UDP_COMMAND_PORT),
            ): cv.port,
            vol.Optional(
                CONF_UDP_RESPONSE_PORT,
                default=data.get(CONF_UDP_RESPONSE_PORT, DEFAULT_UDP_RESPONSE_PORT),
            ): cv.port,
            vol.Optional(
                CONF_MQTT_BASE_TOPIC,
                default=data.get(CONF_MQTT_BASE_TOPIC, DEFAULT_MQTT_BASE_TOPIC),
            ): str,
        }
    )


def _discovery_title(service_name: str, mac: str) -> str:
    suffix = f".{ZM1_ZEROCONF_TYPE}"
    if service_name.endswith(suffix):
        return service_name.removesuffix(suffix)
    return f"zM1 {mac[-4:].upper()}"
