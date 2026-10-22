from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts.audit_trading_push_delivery_sla import audit_one, parse_time


class TradingPushSlaTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 22, 6, 20, tzinfo=timezone.utc)
        self.event = {
            "event_id": "cc15f7da07b386d5d3563c7b",
            "engine": "daily",
            "event_type": "OPEN",
            "opened_at": "2026-10-22T03:52:02Z",
        }
        self.record = {
            "ok": True,
            "recipients_initial_count": 4,
            "first_provider_accepted_at": "2026-10-22T03:52:13.034Z",
            "first_device_ack_at": "2026-10-22T05:50:01.927Z",
        }

    def test_flags_118_minute_device_delay_not_provider_delay(self):
        notes = audit_one(self.event, self.record, self.now)
        self.assertEqual(len(notes), 1)
        self.assertIn("PUSH_DEVICE_ACK_LATE", notes[0])
        self.assertNotIn("PUSH_PROVIDER_LATE", notes[0])

    def test_detects_provider_delay_separately(self):
        record = {**self.record,
                  "first_provider_accepted_at": "2026-10-22T04:20:00Z",
                  "first_device_ack_at": "2026-10-22T04:20:15Z"}
        notes = audit_one(self.event, record, self.now)
        self.assertTrue(any("PUSH_PROVIDER_LATE" in x for x in notes))
        self.assertFalse(any("PUSH_DEVICE_ACK_LATE" in x for x in notes))

    def test_missing_ack_is_not_falsely_treated_as_failed_delivery(self):
        record = {**self.record, "first_device_ack_at": None}
        notes = audit_one(self.event, record, self.now)
        self.assertTrue(any("PUSH_DEVICE_ACK_UNKNOWN" in x for x in notes))
        self.assertFalse(any("PUSH_NOT_ACCEPTED" in x for x in notes))

    def test_identifies_no_eligible_device(self):
        record = {**self.record, "recipients_initial_count": 0,
                  "first_provider_accepted_at": None, "first_device_ack_at": None}
        self.assertTrue(any("PUSH_NO_ELIGIBLE_RECIPIENTS" in x
                            for x in audit_one(self.event, record, self.now)))

    def test_parses_utc_correctly_and_rejects_naive_timestamp(self):
        self.assertEqual(parse_time("2026-10-22T03:52:02Z").utcoffset().total_seconds(), 0)
        self.assertIsNone(parse_time("2026-10-22T03:52:02"))


if __name__ == "__main__":
    unittest.main()
