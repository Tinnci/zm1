# Protocol and observations / 协议与观测

The installed v0.1.4 firmware reports JSON over UDP broadcast and MQTT. The
[upstream protocol](https://github.com/a2633063/zM1/wiki/通信协议) documents
device port 10182, report destination 10181, a 1023-byte limit, MAC identity and
JSON `null` for queries. State replies and periodic sensor reports are separate.
There is no documented request ID or sensor sampling timestamp.

## Reception and requests

One event-loop listener owns each local response address. Clients and discovery
share it, avoiding socket reuse that can send one device's response to another
client. Only reports from the configured MAC and host reach that client. Discovery
collects identities for a fixed window; unrelated traffic cannot extend it.

Reports reach the coordinator at reception, including packets received between
queries or after a timeout. The client serializes requests per MAC, retains
matching fields from split replies, and requires every requested field. Nested
settings may contain additional fields. A newer conflicting value clears an older
match. Invalid datagrams do not terminate a request.

Only read-only polling is retried: two attempts at most, with a 0.2-second delay.
Existing adaptive polling backoff remains. OTA, restart and arbitrary writes are
sent once. Query timeouts do not invalidate freshly received sensor reports.

匹配回报仍不能证明本次请求引起了物理变化。协议没有事务号，完全相同的迟到响应无法
严格关联；MQTT 发布完成也只表达派发，不得直接写入亮度或传感器状态。

## Observation lifetime

| HA attribute | Meaning |
| --- | --- |
| `observed_at` | UTC host receipt time of this field's last valid report |
| `observation_source` | `udp` or `mqtt` |
| `observation_max_age_s` | 300 seconds |

The receive path copies each field and its provenance into an immutable snapshot.
It does not re-observe cached data after polling or command completion. A
brightness report therefore cannot refresh temperature or humidity. Invalid or
missing numeric fields keep their previous valid value and original timestamp.

Availability expires per field after 300 seconds. Expiry uses its own callback,
so it works when polling has backed off to an hour. A new valid report, even with
the same value, restores the field immediately. Transport Repairs recovery still
requires stable traffic; this does not delay publication of a real measurement.
Metadata such as firmware version and last-seen time can remain visible as history.

MQTT retained values lack a reliable age and are ignored for current observations.
A broker connection or publish acknowledgement cannot establish a fresh sample.

M1 exposes four environmental measurements: temperature, humidity, PM2.5 and
formaldehyde. It does not have CO2, eCO2 or TVOC sensors. Their former placeholder
definitions and current-device registry entries are retired during sensor setup;
other devices and integrations retain their entries.

温湿度在短暂通信中断期间可继续使用，但期限从真实报告接收时刻起算。
“设备仍回复状态查询”与“该温度仍有效”是两件不同的事。

## Physical limits and diagnostics

Household passive captures on 2026-09-12 confirmed both short-lived receiver gaps
in the old integration and periods without sensor broadcasts despite state replies.
The new receiver removes its own gaps; it cannot recover packets never delivered
to the host or determine whether the firmware reused an old internal sensor sample.

The public firmware contains UDP, MQTT and UART thread/queue names, but these
strings do not establish a heap leak, power-saving mode, task priority or sensor
sampling cadence. Those require device logs and radio/bus measurements.

`scripts/observe_udp.py` is a passive listener for a separate LAN computer. It
reports traffic counts, measurement values and field receipt times while sending
no packets. Missing measurements remain unknown. Use `tcpdump` on an HA host
that already has the integration listening to avoid competing for its port.

Validation covers real loopback UDP, expiry during polling backoff, immutable
updates, retained MQTT messages, dispatch without feedback, and targeted removal
of obsolete registry entries. Run `uv run pytest` and `uv run ruff check`.
