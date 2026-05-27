from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
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
    decode_payload,
    encode_payload,
    normalize_mac,
)
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
        self.assertEqual(json.loads(encode_payload(query)), {"mac": "b0f89323ad46", "version": None})

    def test_discovery_command_has_no_mac(self) -> None:
        self.assertEqual(build_discovery_command(), {"cmd": "device report"})
        self.assertEqual(json.loads(encode_payload(build_discovery_command())), {"cmd": "device report"})

    def test_response_decodes_json_object(self) -> None:
        response = decode_payload(b'{"mac":"B0:F8:93:23:AD:46","brightness":3,"name":"zM1_AD46"}')
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

    def test_discovered_host_matches_mac(self) -> None:
        responses = [
            {"mac": "001122334455", "_addr": "192.168.3.10"},
            {"mac": "b0f89323ad46", "_addr": "192.168.3.181"},
        ]
        self.assertEqual(find_discovered_host(responses, "B0:F8:93:23:AD:46"), "192.168.3.181")


class UDPClientTest(unittest.TestCase):
    def test_udp_client_sends_json_to_command_port_and_waits_on_response_port(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
