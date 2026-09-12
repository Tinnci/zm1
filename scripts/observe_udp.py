"""Listen to zM1 reports without sending discovery, queries or device commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "zm1"))

from observations import SENSOR_FIELDS, field_is_fresh, merge_report  # noqa: E402
from protocol import SENSOR_REPORT_FIELDS  # noqa: E402
from udp import ZM1UDPClient  # noqa: E402


async def observe(args: argparse.Namespace) -> dict:
    state = {}
    reports = sensor_reports = 0
    first_sensor = last_sensor = None
    max_gap = None

    def received(payload, received_at):
        nonlocal state, reports, sensor_reports, first_sensor, last_sensor, max_gap
        reports += 1
        state = merge_report(state, payload, received_at=received_at, source="udp")
        if SENSOR_REPORT_FIELDS.intersection(payload):
            sensor_reports += 1
            first_sensor = first_sensor or received_at
            if last_sensor is not None:
                max_gap = max(max_gap or 0, (received_at - last_sensor).total_seconds())
            last_sensor = received_at

    client = ZM1UDPClient(args.host, args.mac, response_port=args.port, on_report=received)
    start = datetime.now(UTC)
    try:
        await client.async_start()
        await asyncio.sleep(args.duration)
    finally:
        await client.async_close()
    end = datetime.now(UTC)
    timestamps = state.get("_observed_at", {})
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "commands_sent": 0,
        "reports": reports,
        "sensor_reports": sensor_reports,
        "first_sensor_report": first_sensor.isoformat() if first_sensor else None,
        "last_sensor_report": last_sensor.isoformat() if last_sensor else None,
        "max_sensor_gap_s": max_gap,
        "fields": {
            key: {
                "value": state.get(key),
                "observed_at": timestamps[key].isoformat() if key in timestamps else None,
                "fresh": field_is_fresh(state, key, now=end),
            }
            for key in sorted(SENSOR_FIELDS | {"brightness", "version"})
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Device IPv4 address")
    parser.add_argument("--mac", required=True)
    parser.add_argument("--port", type=int, default=10181, help="Local report port")
    parser.add_argument("--duration", type=float, default=45, help="Seconds to listen")
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("duration must be positive")
    print(json.dumps(asyncio.run(observe(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
