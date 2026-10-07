import json
import tempfile
import unittest
from pathlib import Path

from scripts import wes_notification_outbox as outbox


class WesNotificationOutboxTests(unittest.TestCase):
    def test_open_and_close_in_same_run_are_both_persisted_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "wes-event-outbox.json"
            item = {
                "instrument_id": "eurusd",
                "label_pl": "EUR/USD",
                "direction": "short",
                "entry_price": 1.12,
                "entry_captured_at": "2026-10-07T16:35:50+02:00",
            }
            opened, created_open = outbox.emit_weekly_event("2026-W41", item, "OPEN", path=path)
            self.assertTrue(created_open)

            item.update({
                "exit_price": 1.124,
                "exit_captured_at": "2026-10-07T16:39:50+02:00",
                "exit_reason": "stop_loss",
            })
            closed, created_close = outbox.emit_weekly_event("2026-W41", item, "CLOSE", path=path)
            self.assertTrue(created_close)
            self.assertNotEqual(opened["event_id"], closed["event_id"])
            self.assertEqual(opened["position_id"], closed["position_id"])

            _, retry_open = outbox.emit_weekly_event("2026-W41", item, "OPEN", path=path)
            _, retry_close = outbox.emit_weekly_event("2026-W41", item, "CLOSE", path=path)
            self.assertFalse(retry_open)
            self.assertFalse(retry_close)

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([row["event_type"] for row in payload["events"]], ["OPEN", "CLOSE"])
            self.assertEqual(len({row["event_id"] for row in payload["events"]}), 2)

    def test_close_without_persisted_close_timestamp_fails_closed(self):
        item = {
            "instrument_id": "btcusd",
            "direction": "long",
            "entry_price": 100.0,
            "entry_captured_at": "2026-10-07T10:00:00+02:00",
            "exit_price": 101.0,
        }
        with self.assertRaises(ValueError):
            outbox.build_weekly_event("2026-W41", item, "CLOSE")


if __name__ == "__main__":
    unittest.main()
