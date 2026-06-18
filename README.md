# zM1 Home Assistant integration

Custom Home Assistant integration for zM1 devices using the JSON UDP protocol and the device MQTT topics.

## Install

Copy `custom_components/zm1` into your Home Assistant `/config/custom_components/` directory and restart Home Assistant.

Then add the integration from **Settings > Devices & services > Add integration > zM1**.

Minimum target version: Home Assistant `2026.2.0`. `hacs.json` declares this minimum version for HACS users.

## Supported transports

- UDP direct control: discovers the device with mDNS service `_zcontrol._tcp.local.` first, falls back to the documented UDP broadcast command, sends JSON commands to device UDP port `10182`, and listens for replies on local UDP port `10181`.
- MQTT control: publishes commands to `device/zm1/<mac>/set` and subscribes to `device/zm1/<mac>/state` plus `device/zm1/<mac>/sensor`.

UDP mode does not require MQTT, Docker, add-ons, or any other service. zM1 advertises `_zcontrol._tcp.local.` over mDNS; Home Assistant can discover it automatically and stores the last resolved host as a fallback. The host field is optional and only acts as a manual override. If mDNS is unavailable, the integration falls back to broadcasting `{"cmd":"device report"}` and matching the returned MAC. MQTT mode is optional and requires Home Assistant's MQTT integration to be configured with a working MQTT broker, such as the official Mosquitto Broker add-on or an existing broker on your network.

zM1 sends UDP replies and unsolicited sensor packets to local UDP port `10181`. Home Assistant OS and Supervised normally run on the host network. If you run Home Assistant Container with bridge networking, publish or host-network UDP `10181`, otherwise state queries can time out even when mDNS discovery works. In that case the integration still creates the device entry and keeps retrying.

When Home Assistant cannot receive zM1 UDP replies, the integration creates a Repair issue with the affected device name and response port. When MQTT mode is selected but Home Assistant's MQTT client is not ready, the integration creates a Repair issue that points to the missing MQTT broker/client setup. These issues are cleared automatically after the integration recovers.

Sensor packets currently observed from zM1 include temperature, humidity, formaldehyde, and PM2.5. The integration also exposes reserved TVOC, CO2, and eCO2 sensors so newer firmware fields appear automatically when reported.

The MAC must be lowercase without separators, for example `b0f89323ad46`. The config flow accepts `b0:f8:93:23:ad:46` and normalizes it.

## Reconfigure and options

After a device is added, use **Reconfigure** to change connection settings:

- transport (`udp` or `mqtt`)
- host override
- UDP command and response ports
- MQTT base topic

Use **Options** to change the polling interval in seconds. Changing either
connection settings or options reloads the integration entry automatically.

## Services

- `zm1.send_command`: send any JSON payload supported by zM1.
- `zm1.configure_mqtt`: write the device MQTT settings through UDP.
- `zm1.ota_update`: start OTA by sending a firmware URL through UDP.

Examples:

```yaml
action: zm1.send_command
data:
  mac: b0f89323ad46
  payload:
    brightness: 3
```

```yaml
action: zm1.configure_mqtt
data:
  mac: b0f89323ad46
  mqtt_uri: 192.168.3.10
  mqtt_port: 1883
  mqtt_user: homeassistant
  mqtt_password: password
```

```yaml
action: zm1.ota_update
data:
  mac: b0f89323ad46
  ota_url: http://192.168.3.10/zM1_firmware.bin
```

## Protocol test

Run the local protocol checks with:

```powershell
uv run python -m unittest discover -s tests
```

## Release

CI runs protocol tests with `uv`, plus Home Assistant `hassfest` and HACS validation. To publish a HACS zip release:

1. Update `custom_components/zm1/manifest.json` and `pyproject.toml` to the same semantic version.
2. Tag the commit as `vX.Y.Z`.
3. Push the tag. GitHub Actions builds `zm1.zip`, verifies the tag matches the manifest version, and attaches the zip plus a SHA256 file to the release.
