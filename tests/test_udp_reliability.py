"""Exercise real loopback datagrams, including reports between requests."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

# Fixtures run after both socket plugins' setup hooks, regardless of load order.
pytestmark = pytest.mark.usefixtures("socket_enabled")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "zm1"))

from udp import ZM1Error, ZM1TimeoutError, ZM1UDPClient, discover  # noqa: E402

MAC = "b0f89323ad46"
OTHER_MAC = "001122334455"


class DeviceProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.requests: asyncio.Queue = asyncio.Queue()

    def datagram_received(self, data, addr) -> None:
        self.requests.put_nowait((json.loads(data), addr))


class UDPReliabilityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.transport, self.device = await asyncio.get_running_loop().create_datagram_endpoint(
            DeviceProtocol, local_addr=("127.0.0.1", 0)
        )
        self.command_port = self.transport.get_extra_info("sockname")[1]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", 0))
            self.response_port = sock.getsockname()[1]
        self.clients = []

    async def asyncTearDown(self) -> None:
        for client in self.clients:
            await client.async_close()
        self.transport.close()
        await asyncio.sleep(0)

    def client(self, mac=MAC):
        client = ZM1UDPClient(
            "127.0.0.1",
            mac,
            command_port=self.command_port,
            response_port=self.response_port,
            bind_host="127.0.0.1",
            timeout=0.15,
        )
        self.clients.append(client)
        return client

    def report(self, payload, *, mac=MAC):
        self.transport.sendto(json.dumps({"mac": mac, **payload}).encode(), ("127.0.0.1", self.response_port))

    async def request(self):
        return await asyncio.wait_for(self.device.requests.get(), 1)

    async def test_sensor_report_between_queries_is_observed(self):
        client = self.client()
        task = asyncio.create_task(client.query("version"))
        await self.request()
        self.report({"version": "v0.1.4"})
        await task

        self.report({"temperature": "26.5", "humidity": "58.8"})
        await asyncio.sleep(0.02)

        self.assertEqual(client.last_sensor_report.get("temperature"), "26.5")

    async def test_two_devices_share_response_port_without_stealing_replies(self):
        first, second = self.client(), self.client(OTHER_MAC)
        tasks = [asyncio.create_task(client.query("version")) for client in (first, second)]
        await self.request()
        await self.request()
        self.report({"version": "second"}, mac=OTHER_MAC)
        self.report({"version": "first"})

        results = await asyncio.gather(*tasks, return_exceptions=True)

        self.assertEqual(
            [result.get("version") if isinstance(result, dict) else str(result) for result in results],
            ["first", "second"],
        )

    async def test_malformed_and_oversized_packets_do_not_abort_request(self):
        client = self.client()
        task = asyncio.create_task(client.query("version"))
        await self.request()
        for raw in (b"not json", b"\xff", b"[]", b'{"mac":"bad"}'):
            self.transport.sendto(raw, ("127.0.0.1", self.response_port))
        self.report({"version": "too large", "padding": "x" * 1100})
        self.report({"version": "wrong device"}, mac=OTHER_MAC)
        self.report({"version": "v0.1.4"})

        self.assertEqual((await task)["version"], "v0.1.4")

    async def test_command_waits_for_all_matching_fields(self):
        client = self.client()
        task = asyncio.create_task(client.send({"brightness": 0, "interval": 15}))
        await self.request()
        self.report({"brightness": 4, "interval": 15})
        await asyncio.sleep(0.02)
        self.assertFalse(task.done(), "An unrelated or older brightness report cannot acknowledge this request")
        self.report({"brightness": 0})

        response = await task
        self.assertEqual(response["brightness"], 0)
        self.assertEqual(response["interval"], 15)

    async def test_setting_command_does_not_accept_a_sensor_report(self):
        client = self.client()
        setting = {"mqtt_uri": "broker.local", "mqtt_port": 1883}
        task = asyncio.create_task(client.send({"setting": setting}))
        await self.request()
        self.report({"temperature": "26.5"})
        await asyncio.sleep(0.02)
        self.assertFalse(task.done())
        self.report({"setting": setting})
        self.assertEqual((await task)["setting"], setting)

    async def test_setting_response_may_include_additional_fields(self):
        client = self.client()
        task = asyncio.create_task(client.send({"setting": {"mqtt_uri": "broker.local"}}))
        await self.request()
        self.report({"setting": {"mqtt_uri": "broker.local", "mqtt_port": 1883}})
        self.assertEqual((await task)["setting"]["mqtt_uri"], "broker.local")

    async def test_dns_failure_uses_transport_error_and_releases_request(self):
        client = self.client()
        client.host = "device.invalid"
        with patch.object(asyncio.get_running_loop(), "getaddrinfo", side_effect=socket.gaierror("not found")):
            with self.assertRaises(ZM1Error):
                await client.query("version")
        client.host = "127.0.0.1"
        task = asyncio.create_task(client.query("version"))
        await self.request()
        self.report({"version": "v0.1.4"})
        self.assertEqual((await task)["version"], "v0.1.4")

    async def test_late_report_is_observed_after_request_timeout(self):
        client = self.client()
        task = asyncio.create_task(client.query("version"))
        await self.request()
        with self.assertRaises(ZM1TimeoutError):
            await task
        self.report({"temperature": "26.5"})
        await asyncio.sleep(0.02)
        self.assertEqual(client.last_sensor_report.get("temperature"), "26.5")

    async def test_cancelled_request_does_not_close_listener_or_block_next_request(self):
        client = self.client()
        task = asyncio.create_task(client.query("version"))
        await self.request()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        next_task = asyncio.create_task(client.query("brightness"))
        await self.request()
        self.report({"version": "late"})
        self.report({"temperature": "26.5"})
        self.report({"brightness": 2})

        self.assertEqual((await next_task)["brightness"], 2)
        self.assertEqual(client.last_sensor_report["temperature"], "26.5")

    async def test_closing_one_device_preserves_other_listener_and_final_close_releases_port(self):
        first, second = self.client(), self.client(OTHER_MAC)
        await asyncio.gather(first.async_start(), second.async_start())
        await first.async_close()
        task = asyncio.create_task(second.query("version"))
        await self.request()
        self.report({"version": "v0.1.4"}, mac=OTHER_MAC)
        self.assertEqual((await task)["version"], "v0.1.4")
        await second.async_close()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", self.response_port))
        with self.assertRaises(ZM1Error):
            await first.query("version")

    async def test_cancelled_unload_does_not_poison_listener_for_reload(self):
        client = self.client()
        await client.async_start()
        closing = asyncio.create_task(client.async_close())
        asyncio.get_running_loop().call_soon(closing.cancel)
        with self.assertRaises(asyncio.CancelledError):
            await closing
        await asyncio.sleep(0)
        replacement = self.client()
        await replacement.async_start()
        self.report({"temperature": "26.5"})
        await asyncio.sleep(0.02)
        self.assertEqual(replacement.last_sensor_report["temperature"], "26.5")

    async def test_request_values_are_detached_before_waiting_for_previous_request(self):
        client = self.client()
        previous = asyncio.create_task(client.query("version"))
        await self.request()
        values = {"brightness": 2}
        task = asyncio.create_task(client.send(values))
        await asyncio.sleep(0)
        values["brightness"] = 4
        self.report({"version": "v0.1.4"})
        await previous
        request, _ = await self.request()
        self.assertEqual(request["brightness"], 2)
        self.report({"brightness": 2})
        self.assertEqual((await task)["brightness"], 2)

    async def test_newer_mismatched_report_clears_previously_matching_field(self):
        client = self.client()
        task = asyncio.create_task(client.send({"brightness": 0, "interval": 15}))
        await self.request()
        self.report({"brightness": 0})
        self.report({"brightness": 4})
        self.report({"interval": 15})
        await asyncio.sleep(0.02)
        self.assertFalse(task.done())
        self.report({"brightness": 0})
        self.assertEqual((await task)["brightness"], 0)

    async def test_discovery_ends_on_absolute_deadline_despite_other_reports(self):
        task = asyncio.create_task(
            discover(
                broadcast_address="127.0.0.1",
                command_port=self.command_port,
                response_port=self.response_port,
                timeout=0.08,
            )
        )
        await self.request()

        async def chatter():
            for _ in range(20):
                self.report({"temperature": "26.5"})
                await asyncio.sleep(0.01)

        chat = asyncio.create_task(chatter())
        try:
            await asyncio.wait_for(task, 0.15)
        finally:
            await chat
