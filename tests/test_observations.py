"""Regression coverage for measurement provenance and immutable publication."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "zm1"))

from observations import OBSERVATION_TTL, field_is_fresh, merge_report  # noqa: E402


class ObservationTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 12, 12, tzinfo=UTC)

    def merge(self, data, payload, seconds=0):
        return merge_report(data, payload, received_at=self.now + timedelta(seconds=seconds), source="udp")

    def test_partial_report_does_not_refresh_other_sensor_fields(self):
        first = self.merge({}, {"temperature": "26.5", "humidity": "58.8"})
        second = self.merge(first, {"brightness": 2, "humidity": "59.0"}, seconds=200)
        self.assertEqual(second["_observed_at"]["temperature"], self.now)
        self.assertEqual(second["_observed_at"]["humidity"], self.now + timedelta(seconds=200))
        later = self.now + timedelta(seconds=OBSERVATION_TTL)
        self.assertFalse(field_is_fresh(second, "temperature", now=later))
        self.assertTrue(field_is_fresh(second, "humidity", now=later))

    def test_last_seen_only_moves_with_a_real_report(self):
        first = self.merge({}, {"temperature": "26.5"})
        second = self.merge(first, {}, seconds=100)
        self.assertIs(second, first)
        self.assertEqual(second["_last_seen"], self.now)

    def test_snapshot_detaches_nested_values_from_packet_and_previous_state(self):
        payload = {"temperature": "26.5", "task_0": {"hour": 1, "days": [1, 2]}}
        first = self.merge({}, payload)
        payload["task_0"]["days"].append(3)
        second = self.merge(first, {"task_0": {"hour": 2}}, seconds=1)
        self.assertEqual(first["task_0"]["days"], (1, 2))
        self.assertEqual(second["task_0"]["hour"], 2)
        with self.assertRaises(TypeError):
            first["task_0"]["hour"] = 4
        with self.assertRaises(TypeError):
            second["_observed_at"]["temperature"] = self.now

    def test_invalid_numbers_do_not_renew_valid_measurements(self):
        first = self.merge({}, {"temperature": "26.5", "humidity": "58.8"})
        for value in (None, True, "nan", "inf", "-inf", "bad", []):
            with self.subTest(value=value):
                next_state = self.merge(first, {"temperature": value}, seconds=100)
                self.assertEqual(next_state["temperature"], 26.5)
                self.assertEqual(next_state["_observed_at"]["temperature"], self.now)

    def test_aliases_share_one_field_timestamp(self):
        first = self.merge({}, {"PM25": "12.0"})
        second = self.merge(first, {"pm25": "13.0"}, seconds=100)
        self.assertEqual(second["pm25"], 13.0)
        self.assertNotIn("PM25", second)
        self.assertEqual(second["_observed_at"]["pm25"], self.now + timedelta(seconds=100))

    def test_identical_new_report_restores_freshness_immediately(self):
        first = self.merge({}, {"temperature": "26.5"})
        later = self.now + timedelta(seconds=OBSERVATION_TTL + 1)
        self.assertFalse(field_is_fresh(first, "temperature", now=later))
        second = self.merge(first, {"temperature": "26.5"}, seconds=OBSERVATION_TTL + 1)
        self.assertTrue(field_is_fresh(second, "temperature", now=later))

    def test_older_callback_cannot_replace_newer_field_observation(self):
        first = self.merge({}, {"temperature": "26.5"}, seconds=30)
        second = self.merge(first, {"temperature": "18.0"}, seconds=10)
        self.assertEqual(second["temperature"], 26.5)
        self.assertEqual(second["_last_seen"], first["_last_seen"])

    def test_network_payload_cannot_supply_internal_freshness(self):
        first = self.merge({}, {"temperature": "26.5"})
        second = self.merge(first, {"_observed_at": {"temperature": self.now + timedelta(days=1)}}, seconds=10)
        self.assertEqual(second["_observed_at"]["temperature"], self.now)
