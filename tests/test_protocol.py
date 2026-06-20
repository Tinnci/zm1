from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "zm1"))

from protocol import (  # noqa: E402
    ZM1ProtocolError,
    build_command,
    build_discovery_command,
    build_mqtt_topics,
    build_query,
    clamp_zm1_brightness,
    decode_payload,
    encode_payload,
    ha_brightness_to_zm1,
    normalize_mac,
    zm1_brightness_to_ha,
)
from polling import AdaptivePollingPolicy  # noqa: E402
from udp import ZM1UDPClient  # noqa: E402
from udp import find_discovered_host  # noqa: E402


def free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


class ProtocolTest(unittest.TestCase):
    def test_mac_is_lowercase_without_separators(self) -> None:
        self.assertEqual(normalize_mac("B0:F8:93:23:AD:46"), "b0f89323ad46")
        self.assertEqual(normalize_mac("b0-f8-93-23-ad-46"), "b0f89323ad46")

    def test_invalid_mac_is_rejected(self) -> None:
        with self.assertRaises(ZM1ProtocolError):
            normalize_mac("b0f89323ad4")

    def test_command_contains_mac_and_brightness(self) -> None:
        command = build_command("b0f89323ad46", {"brightness": 3})
        self.assertEqual(command, {"mac": "b0f89323ad46", "brightness": 3})

    def test_query_uses_json_null(self) -> None:
        query = build_query("b0f89323ad46", "version")
        self.assertEqual(query, {"mac": "b0f89323ad46", "version": None})
        self.assertEqual(
            json.loads(encode_payload(query)), {"mac": "b0f89323ad46", "version": None}
        )

    def test_discovery_command_has_no_mac(self) -> None:
        self.assertEqual(build_discovery_command(), {"cmd": "device report"})
        self.assertEqual(
            json.loads(encode_payload(build_discovery_command())),
            {"cmd": "device report"},
        )

    def test_response_decodes_json_object(self) -> None:
        response = decode_payload(
            b'{"mac":"B0:F8:93:23:AD:46","brightness":3,"name":"zM1_AD46"}'
        )
        self.assertEqual(response["mac"], "b0f89323ad46")
        self.assertEqual(response["brightness"], 3)
        self.assertEqual(response["name"], "zM1_AD46")

    def test_packet_size_limit_is_1023_bytes(self) -> None:
        with self.assertRaises(ZM1ProtocolError):
            encode_payload({"mac": "b0f89323ad46", "data": "x" * 1024})

    def test_mqtt_topics_match_documented_shape(self) -> None:
        topics = build_mqtt_topics("b0f89323ad46")
        self.assertEqual(topics.command, "device/zm1/b0f89323ad46/set")
        self.assertEqual(topics.state, "device/zm1/b0f89323ad46/state")
        self.assertEqual(topics.sensor, "device/zm1/b0f89323ad46/sensor")

    def test_brightness_maps_full_zm1_range_to_home_assistant(self) -> None:
        self.assertEqual(clamp_zm1_brightness("5"), 4)
        self.assertEqual(zm1_brightness_to_ha(0), 0)
        self.assertEqual(zm1_brightness_to_ha(4), 255)
        self.assertEqual(ha_brightness_to_zm1(1), 1)
        self.assertEqual(ha_brightness_to_zm1(128), 2)
        self.assertEqual(ha_brightness_to_zm1(255), 4)

    def test_discovered_host_matches_mac(self) -> None:
        responses = [
            {"mac": "001122334455", "_addr": "192.168.3.10"},
            {"mac": "b0f89323ad46", "_addr": "192.168.3.181"},
        ]
        self.assertEqual(
            find_discovered_host(responses, "B0:F8:93:23:AD:46"), "192.168.3.181"
        )


class UDPClientTest(unittest.TestCase):
    def test_udp_client_sends_json_to_command_port_and_waits_on_response_port(
        self,
    ) -> None:
        command_port = free_udp_port()
        response_port = free_udp_port()
        received: dict[str, object] = {}
        ready = threading.Event()

        def fake_zm1() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind(("127.0.0.1", command_port))
                ready.set()
                data, addr = sock.recvfrom(1024)
                received.update(json.loads(data.decode()))
                sensor = {
                    "mac": received["mac"],
                    "temperature": "26.5",
                    "humidity": "58.8",
                }
                response = {
                    "mac": received["mac"],
                    "brightness": received["brightness"],
                    "name": "zM1_AD46",
                }
                sock.sendto(json.dumps(sensor).encode(), addr)
                sock.sendto(json.dumps(response).encode(), addr)
            finally:
                sock.close()

        thread = threading.Thread(target=fake_zm1, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(2))

        client = ZM1UDPClient(
            "127.0.0.1",
            "b0f89323ad46",
            command_port=command_port,
            response_port=response_port,
            timeout=2.0,
            bind_host="127.0.0.1",
        )
        response = asyncio.run(client.send({"brightness": 3}))
        thread.join(2)

        self.assertEqual(received, {"mac": "b0f89323ad46", "brightness": 3})
        self.assertEqual(response["mac"], "b0f89323ad46")
        self.assertEqual(response["brightness"], 3)
        self.assertEqual(response["name"], "zM1_AD46")
        self.assertEqual(client.last_sensor_report["temperature"], "26.5")
        self.assertEqual(client.last_sensor_report["humidity"], "58.8")

    def test_sensor_report_read_uses_caller_timeout(self) -> None:
        response_port = free_udp_port()
        client = ZM1UDPClient(
            "127.0.0.1",
            "b0f89323ad46",
            response_port=response_port,
            bind_host="127.0.0.1",
        )

        start = time.monotonic()
        response = asyncio.run(client.read_sensor_report(timeout=0.05))
        elapsed = time.monotonic() - start

        self.assertEqual(response, {})
        self.assertLess(elapsed, 0.5)

    def test_udp_client_serializes_requests_on_shared_response_port(self) -> None:
        command_port = free_udp_port()
        response_port = free_udp_port()
        first_received = threading.Event()
        server_result: dict[str, object] = {}

        def fake_zm1() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind(("127.0.0.1", command_port))
                first_data, first_addr = sock.recvfrom(1024)
                first_payload = json.loads(first_data.decode())
                first_received.set()

                sock.settimeout(0.2)
                try:
                    sock.recvfrom(1024)
                except socket.timeout:
                    server_result["second_arrived_before_first_response"] = False
                else:
                    server_result["second_arrived_before_first_response"] = True

                sock.sendto(
                    json.dumps(
                        {
                            "mac": first_payload["mac"],
                            "brightness": 2,
                            "version": "1.0",
                        }
                    ).encode(),
                    first_addr,
                )

                second_data, second_addr = sock.recvfrom(1024)
                second_payload = json.loads(second_data.decode())
                sock.sendto(
                    json.dumps(
                        {
                            "mac": second_payload["mac"],
                            "brightness": second_payload["brightness"],
                        }
                    ).encode(),
                    second_addr,
                )
            finally:
                sock.close()

        async def run_concurrent_requests() -> tuple[
            dict[str, object], dict[str, object]
        ]:
            client = ZM1UDPClient(
                "127.0.0.1",
                "b0f89323ad46",
                command_port=command_port,
                response_port=response_port,
                timeout=2.0,
                bind_host="127.0.0.1",
            )
            query_task = asyncio.create_task(client.query("brightness", "version"))
            await asyncio.to_thread(first_received.wait, 2)
            send_task = asyncio.create_task(client.send({"brightness": 4}))
            return await query_task, await send_task

        thread = threading.Thread(target=fake_zm1, daemon=True)
        thread.start()

        query_response, send_response = asyncio.run(run_concurrent_requests())
        thread.join(2)

        self.assertFalse(server_result["second_arrived_before_first_response"])
        self.assertEqual(query_response["version"], "1.0")
        self.assertEqual(send_response["brightness"], 4)


class AdaptivePollingPolicyTest(unittest.TestCase):
    def test_polling_interval_is_clamped_to_minimum(self) -> None:
        policy = AdaptivePollingPolicy(5, min_interval=15, max_interval=300)

        self.assertEqual(policy.base_interval, 15)
        self.assertEqual(policy.interval, 15)

    def test_failures_back_off_and_successes_restore_base_interval(self) -> None:
        policy = AdaptivePollingPolicy(
            30,
            min_interval=15,
            max_interval=300,
            recovery_successes=2,
        )

        self.assertEqual(policy.record_failure(), 60)
        self.assertEqual(policy.record_failure(), 120)
        self.assertEqual(policy.record_success(), 120)
        self.assertEqual(policy.record_success(), 30)

    def test_backoff_is_capped(self) -> None:
        policy = AdaptivePollingPolicy(60, min_interval=15, max_interval=180)

        self.assertEqual(policy.record_failure(), 120)
        self.assertEqual(policy.record_failure(), 180)
        self.assertEqual(policy.record_failure(), 180)


class MetadataTest(unittest.TestCase):
    def test_translations_include_options_flow(self) -> None:
        for translation in ("en", "zh-Hans"):
            path = (
                ROOT
                / "custom_components"
                / "zm1"
                / "translations"
                / f"{translation}.json"
            )
            data = json.loads(path.read_text(encoding="utf-8"))
            option_fields = data["options"]["step"]["init"]["data"]
            reconfigure_fields = data["config"]["step"]["reconfigure"]["data"]
            issues = data["issues"]

            self.assertIn("scan_interval", option_fields)
            self.assertIn("transport", reconfigure_fields)
            self.assertIn("host", reconfigure_fields)
            self.assertIn("udp_response_unavailable", issues)
            self.assertIn("mqtt_not_ready", issues)


if __name__ == "__main__":
    unittest.main()
