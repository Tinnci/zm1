# zM1 for Home Assistant

Control zM1 devices through local UDP or Home Assistant MQTT.

The integration receives device reports continuously, tracks each field's age,
and retains recent measurements during short UDP outages.

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

One asynchronous listener shares the response port across devices and discovery.
It keeps receiving while no query is active. Requests for the same device are
serialized, and malformed packets do not interrupt reception.

Polling uses these rules:

- The configured interval is from 15 to 3600 seconds.
- Repeated failures increase the interval.
- Stable success restores the configured interval.
- Read-only queries retry at most once. Write commands are sent once.
- Transport failures use the existing backoff and Repairs recovery policy.
- Each sensor and brightness field stays available for 300 seconds after its
  last valid report, independently of failed polls or other fields' updates.
- An independent timer expires old fields even during long polling backoff.
- One new valid report restores that field immediately.

A command timeout still reports failure to the caller. A matching response is
reported state, not proof of physical application. The firmware has no request
sequence field, so an identical delayed response cannot be tied to one command.

温湿度与亮度按各自的报告时间过期。状态查询成功不会刷新旧温度，短暂超时也不会立即
隐藏仍有效的测量；持续断联达到 300 秒后，旧读数停止作为可用状态。

## MQTT behavior

MQTT mode requires a working Home Assistant MQTT integration and broker. The
official Mosquitto Broker add-on is one option.

The integration creates a Repairs issue when the Home Assistant MQTT client is
not ready. It removes the issue after recovery.

Publishing a command does not update observed state. Only incoming reports do.
Retained messages have no sampling timestamp, so they cannot establish freshness;
the integration waits for a live report.

## Sensors

Observed zM1 packets include:

- temperature,
- humidity,
- formaldehyde,
- PM2.5.

M1 does not provide physical TVOC, CO2 or eCO2 measurements. These former reserved
entities are no longer created. Setup removes only their old registry entries
owned by this integration and device, including renamed entries.

Measurement attributes expose `observed_at`, `observation_source` and
`observation_max_age_s`. The timestamp is when the integration received that
field, not the device's sampling clock. Partial reports leave other fields' times
unchanged. State snapshots are deeply immutable.

Distinct values, source changes and availability transitions publish immediately.
Repeated identical reports are published at most once per 60 seconds, with a final
pending report retained when the device falls silent. Internal observations keep
their full precision and reporting rate. Version, OTA progress and last-seen
diagnostics are disabled by default for new entities; existing choices are kept.

See [Protocol and observation behavior](docs/protocol-and-observations.md).

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

Check the field's `observed_at` attribute. A recent brightness or version reply
does not establish that the temperature sensor is still reporting.

On a separate LAN computer, collect passive observations without sending commands:

```sh
uv run python scripts/observe_udp.py --host <device-ipv4> --mac <device-mac> --duration 45
```

On the HA host, use passive packet capture instead of opening a competing socket
on its response port.

### MQTT mode does not start

Confirm that Home Assistant has a loaded MQTT integration and a connected
broker.

## Development

Use `uv` for tests. The development group selects Python 3.14 and the supported
Home Assistant test environment; integration code remains compatible with Python 3.12 syntax.

```bash
uv run pytest
uv run ruff check
git diff --check
```

CI also runs Home Assistant `hassfest` and HACS validation.

## Release

1. Set the same semantic version in `manifest.json` and `pyproject.toml`.
2. Create a `vX.Y.Z` tag.
3. Push the tag.

The release workflow checks out the selected tag and runs the same pytest,
Ruff, Hassfest and HACS jobs as CI. It builds `zm1.zip` with `manifest.json`
at the archive root and retains the existing SHA256 companion file.
Extract the archive directly into `/config/custom_components/zm1`.

See [Release preparation / 发布准备](docs/releasing.md) for version 0.3.0.

## Documentation style

This README applies practical rules from ASD-STE100 Simplified Technical
English, Issue 9. It uses active voice, short sentences, and consistent terms.

This use is not an ASD-STE100 compliance certification. Project-specific terms
remain necessary.

Reference: ASD STEMG. [ASD-STE100 Simplified Technical English](https://www.asd-ste100.org/), Issue 9, 2025.
