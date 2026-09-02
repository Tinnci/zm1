# zM1 for Home Assistant

Control zM1 devices through local UDP or Home Assistant MQTT.

The integration discovers local devices, tracks transport health, and limits
availability changes during short UDP outages.

Minimum Home Assistant version: `2026.2.0`.

## Transport selection

| Transport | Command path | State path | Requirement |
| --- | --- | --- | --- |
| UDP | JSON to device port `10182` | Replies on local port `10181` | Local network access |
| MQTT | `device/zm1/<mac>/set` | State and sensor topics | Home Assistant MQTT |

UDP does not require MQTT, Docker, or an add-on.

## UDP behavior

The integration first resolves `_zcontrol._tcp.local.` with mDNS. It stores
the last host as a fallback.

If mDNS fails, the integration broadcasts `{"cmd":"device report"}`. It uses
the response that matches the configured MAC.

The host option is a manual override. It is not required for normal discovery.

zM1 sends replies and sensor reports to local UDP port `10181`. Home Assistant
OS and Supervised normally use host networking.

Home Assistant Container must publish this UDP port or use host networking.
Otherwise, discovery can work while state requests time out.

The integration serializes UDP requests for each device. Commands, state
requests, and sensor requests share one response port.

Polling uses these rules:

- The configured interval is from 15 to 3600 seconds.
- Repeated failures increase the interval.
- Stable success restores the configured interval.
- Two failed polls keep the last valid state.
- The third failed poll marks the device unavailable.
- Three successful polls restore availability.

A direct user command still reports its failure immediately. The polling rules
only reduce availability and log flapping.

## MQTT behavior

MQTT mode requires a working Home Assistant MQTT integration and broker. The
official Mosquitto Broker add-on is one option.

The integration creates a Repairs issue when the Home Assistant MQTT client is
not ready. It removes the issue after recovery.

## Sensors

Observed zM1 packets include:

- temperature,
- humidity,
- formaldehyde,
- PM2.5.

Reserved TVOC, CO2, and eCO2 sensors accept fields from newer firmware.

## Installation

1. Copy `custom_components/zm1` to `/config/custom_components/zm1`.
2. Restart Home Assistant.
3. Open **Settings > Devices & services**.
4. Add **zM1**.

The MAC must use lowercase characters without separators. Example:
`b0f89323ad46`.

The config flow also accepts `b0:f8:93:23:ad:46` and converts it.

## Configuration

Use **Reconfigure** to change:

- UDP or MQTT transport,
- host override,
- UDP command and response ports,
- MQTT base topic.

Use **Options** to change the polling interval.

The integration reloads the config entry after either change.

## Services

### Send a device command

```yaml
action: zm1.send_command
data:
  mac: b0f89323ad46
  payload:
    brightness: 3
```

### Configure device MQTT

```yaml
action: zm1.configure_mqtt
data:
  mac: b0f89323ad46
  mqtt_uri: 192.168.3.10
  mqtt_port: 1883
  mqtt_user: homeassistant
  mqtt_password: password
```

### Start an OTA update

```yaml
action: zm1.ota_update
data:
  mac: b0f89323ad46
  ota_url: http://192.168.3.10/zM1_firmware.bin
```

Only use firmware that matches the device. Keep the firmware URL on a trusted
network.

## Troubleshooting

### UDP discovery works, but state requests fail

Confirm that Home Assistant can receive UDP port `10181`. Check container
networking and firewall rules.

### The device becomes unavailable

Open the Home Assistant Repairs page. The issue identifies the response port
and clears after stable recovery.

One or two failed polls do not change availability. Check for at least three
consecutive failures.

### MQTT mode does not start

Confirm that Home Assistant has a loaded MQTT integration and a connected
broker.

## Development

Use `uv` for tests.

```bash
uv run python -m unittest discover -s tests
git diff --check
```

CI also runs Home Assistant `hassfest` and HACS validation.

## Release

1. Set the same semantic version in `manifest.json` and `pyproject.toml`.
2. Create a `vX.Y.Z` tag.
3. Push the tag.

The release workflow builds `zm1.zip`. It verifies the version and adds a
SHA256 file to the release.

## Documentation style

This README applies practical rules from ASD-STE100 Simplified Technical
English, Issue 9. It uses active voice, short sentences, and consistent terms.

This use is not an ASD-STE100 compliance certification. Project-specific terms
remain necessary.

Reference: ASD STEMG. [ASD-STE100 Simplified Technical English](https://www.asd-ste100.org/), Issue 9, 2025.
