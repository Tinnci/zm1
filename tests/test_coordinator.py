"""End-to-end observation and availability semantics inside Home Assistant."""

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.zm1.coordinator import ZM1Coordinator
from custom_components.zm1.light import ZM1Light
from custom_components.zm1.observations import OBSERVATION_TTL
from custom_components.zm1.sensor import SENSORS, ZM1Sensor
from custom_components.zm1.sensor import async_setup_entry as async_setup_sensors
from custom_components.zm1.transport import ZM1MqttTransport
from custom_components.zm1.udp import ZM1TimeoutError

MAC = "b0f89323ad46"


@pytest.fixture
async def coordinator(hass):
    entry = MockConfigEntry(
        domain="zm1",
        title="zM1",
        data={"mac": MAC, "transport": "udp", "host": "127.0.0.1"},
    )
    entry.add_to_hass(hass)
    coordinator = ZM1Coordinator(hass, entry)
    yield coordinator
    await coordinator.async_shutdown()


def sensor(coordinator, key):
    return ZM1Sensor(coordinator, next(description for description in SENSORS if description.key == key))


async def test_cached_poll_does_not_republish_observation_time(coordinator, freezer):
    now = datetime.now(UTC)
    coordinator._handle_transport_message({"temperature": "26.5"}, now, "udp")
    before = coordinator.data
    coordinator._transport.async_update_data = AsyncMock(return_value=before)
    freezer.move_to(now + timedelta(seconds=90))

    result = await coordinator._async_update_data()

    assert result is before
    assert result["_last_seen"] == now
    assert sensor(coordinator, "temperature").extra_state_attributes["observed_at"] == now.isoformat()


async def test_report_during_poll_is_not_overwritten_by_poll_start_snapshot(coordinator):
    now = datetime.now(UTC)
    coordinator._handle_transport_message({"temperature": "26.5"}, now, "udp")
    before = coordinator.data

    async def update(**kwargs):
        coordinator._handle_transport_message({"temperature": "26.6"}, now + timedelta(seconds=1), "udp")
        return kwargs["current_data"]

    coordinator._transport.async_update_data = update
    result = await coordinator._async_update_data()

    assert result["temperature"] == 26.6
    assert before["temperature"] == 26.5


async def test_expiry_ignores_poll_backoff_and_other_field_updates(coordinator, hass, freezer):
    now = datetime.now(UTC)
    coordinator._transport._polling_policy.configured_interval = 3600
    coordinator._transport._polling_policy.record_success()
    coordinator.update_interval = timedelta(seconds=3600)
    notified = Mock()
    unsub = coordinator.async_add_listener(notified)
    coordinator._handle_transport_message({"temperature": "26.5", "humidity": "58.8"}, now, "udp")
    freezer.move_to(now + timedelta(seconds=200))
    coordinator._handle_transport_message({"humidity": "59.0"}, datetime.now(UTC), "udp")
    notified.reset_mock()

    freezer.move_to(now + timedelta(seconds=OBSERVATION_TTL + 1))
    async_fire_time_changed(hass, datetime.now(UTC))
    await hass.async_block_till_done()

    assert notified.called
    assert not sensor(coordinator, "temperature").available
    assert sensor(coordinator, "temperature").native_value is None
    assert sensor(coordinator, "humidity").available
    assert sensor(coordinator, "temperature").extra_state_attributes["observed_at"] == now.isoformat()
    unsub()


async def test_one_timeout_does_not_hide_a_valid_sensor_report(coordinator):
    coordinator._handle_transport_message({"temperature": "26.5"}, datetime.now(UTC), "udp")
    coordinator.last_update_success = False

    assert sensor(coordinator, "temperature").available
    assert sensor(coordinator, "temperature").native_value == 26.5
    assert not sensor(coordinator, "humidity").available
    assert ZM1Light(coordinator).is_on is None


async def test_identical_report_restores_expired_sensor_immediately(coordinator, freezer):
    now = datetime.now(UTC)
    coordinator._handle_transport_message({"temperature": "26.5"}, now, "udp")
    freezer.move_to(now + timedelta(seconds=OBSERVATION_TTL + 1))
    assert not sensor(coordinator, "temperature").available

    coordinator._handle_transport_message({"temperature": "26.5"}, datetime.now(UTC), "udp")

    assert sensor(coordinator, "temperature").available
    assert sensor(coordinator, "temperature").extra_state_attributes["observed_at"] == datetime.now(UTC).isoformat()


async def test_reports_keep_existing_diagnostic_poll_schedule(coordinator):
    with patch.object(coordinator, "_schedule_refresh") as schedule:
        coordinator._handle_transport_message({"temperature": "26.5"}, datetime.now(UTC), "udp")
    schedule.assert_not_called()


async def test_mqtt_dispatch_does_not_change_observed_brightness(coordinator, hass):
    transport = ZM1MqttTransport(hass, coordinator.entry, mac=MAC, on_message=coordinator._handle_transport_message)
    coordinator._transport = transport
    transport._async_publish_mqtt = AsyncMock()
    coordinator._handle_transport_message({"brightness": 0}, datetime.now(UTC), "mqtt")
    before = coordinator.data

    result = await coordinator.async_send_command({"brightness": 4})

    assert result is None
    assert coordinator.data is before
    assert ZM1Light(coordinator).is_on is False
    transport._async_publish_mqtt.assert_awaited_once_with({"brightness": 4})


async def test_mqtt_retained_values_are_not_fresh_measurements(coordinator, hass):
    handlers = []
    transport = ZM1MqttTransport(hass, coordinator.entry, mac=MAC, on_message=coordinator._handle_transport_message)

    async def subscribe(hass, topic, handler, qos):
        handlers.append(handler)
        return Mock()

    with (
        patch("homeassistant.components.mqtt.async_wait_for_mqtt_client", new=AsyncMock()),
        patch("homeassistant.components.mqtt.async_subscribe", side_effect=subscribe),
    ):
        await transport.async_setup()
    message = SimpleNamespace(topic="sensor", payload='{"temperature":"26.5"}', retain=True)
    handlers[0](message)
    assert coordinator.data is None
    message.retain = False
    handlers[0](message)
    assert sensor(coordinator, "temperature").available
    assert sensor(coordinator, "temperature").extra_state_attributes["observation_source"] == "mqtt"
    await transport.async_shutdown()


async def test_udp_query_retry_is_bounded_and_commands_are_not_replayed(coordinator):
    transport = coordinator._transport
    client = Mock(last_received_monotonic=None)
    client.query = AsyncMock(side_effect=ZM1TimeoutError("lost"))
    client.send = AsyncMock(side_effect=ZM1TimeoutError("lost"))
    transport._client = client
    client.async_close = AsyncMock()
    transport._async_get_udp_client = AsyncMock(return_value=client)
    with patch("custom_components.zm1.transport.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(UpdateFailed):
            await transport.async_update_data(current_data={}, device_name="zM1")
    assert client.query.await_count == 2
    assert transport.update_interval == timedelta(seconds=60)

    with pytest.raises(ZM1TimeoutError):
        await transport.async_send_command({"cmd": "restart"})
    client.send.assert_awaited_once_with({"cmd": "restart"})


async def test_sensor_broadcast_keeps_observations_when_state_query_is_lost(coordinator):
    transport = coordinator._transport
    client = Mock(last_received_monotonic=None)
    client.async_close = AsyncMock()

    async def query(*fields):
        coordinator._handle_transport_message({"temperature": "26.5"}, datetime.now(UTC), "udp")
        client.last_received_monotonic = time.monotonic()
        raise ZM1TimeoutError("state reply lost")

    client.query = AsyncMock(side_effect=query)
    transport._client = client
    transport._async_get_udp_client = AsyncMock(return_value=client)

    await transport.async_update_data(current_data={}, device_name="zM1")

    assert client.query.await_count == 1
    assert transport._polling_policy.failures == 0
    assert sensor(coordinator, "temperature").available


async def test_sensor_setup_removes_only_unsupported_entities_owned_by_this_device(coordinator, hass):
    registry = er.async_get(hass)
    ghosts = [
        registry.async_get_or_create(
            "sensor", "zm1", f"{MAC}_{key}", config_entry=coordinator.entry, suggested_object_id=f"renamed_{key}"
        )
        for key in ("co2", "eco2", "tvoc")
    ]
    temperature = registry.async_get_or_create("sensor", "zm1", f"{MAC}_temperature", config_entry=coordinator.entry)
    other_entry = MockConfigEntry(domain="zm1", unique_id="another_device")
    other_entry.add_to_hass(hass)
    other = registry.async_get_or_create("sensor", "zm1", "another_device_co2", config_entry=other_entry)
    coordinator.entry.runtime_data = coordinator
    add_entities = Mock()

    await async_setup_sensors(hass, coordinator.entry, add_entities)

    assert all(registry.async_get(entity.entity_id) is None for entity in ghosts)
    assert registry.async_get(temperature.entity_id) is not None
    assert registry.async_get(other.entity_id) is not None
    keys = {entity.entity_description.key for entity in add_entities.call_args.args[0]}
    assert keys == {"temperature", "humidity", "pm25", "formaldehyde", "version", "ota_progress", "last_seen"}
