"""Constants for the zM1 integration."""

from homeassistant.const import Platform

DOMAIN = "zm1"
PLATFORMS = [Platform.LIGHT, Platform.SENSOR]

CONF_MAC = "mac"
CONF_TRANSPORT = "transport"
CONF_UDP_COMMAND_PORT = "udp_command_port"
CONF_UDP_RESPONSE_PORT = "udp_response_port"
CONF_MQTT_BASE_TOPIC = "mqtt_base_topic"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ZEROCONF_NAME = "zeroconf_name"
CONF_LAST_HOST = "last_host"

TRANSPORT_UDP = "udp"
TRANSPORT_MQTT = "mqtt"
TRANSPORTS = [TRANSPORT_UDP, TRANSPORT_MQTT]

DEFAULT_UDP_COMMAND_PORT = 10182
DEFAULT_UDP_RESPONSE_PORT = 10181
DEFAULT_TIMEOUT = 3.0
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_SENSOR_REPORT_TIMEOUT = 0.2
DEFAULT_MQTT_BASE_TOPIC = "device/zm1"
MAX_PACKET_BYTES = 1023
ZM1_ZEROCONF_TYPE = "_zcontrol._tcp.local."
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

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_PAYLOAD = "payload"
ATTR_OTA_URL = "ota_url"
ATTR_MQTT_URI = "mqtt_uri"
ATTR_MQTT_PORT = "mqtt_port"
ATTR_MQTT_USER = "mqtt_user"
ATTR_MQTT_PASSWORD = "mqtt_password"

SERVICE_SEND_COMMAND = "send_command"
SERVICE_CONFIGURE_MQTT = "configure_mqtt"
SERVICE_OTA_UPDATE = "ota_update"
