import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import stock_trading_session_calendar as calendar


class SessionCalendarTests(unittest.TestCase):
    def test_official_holidays_early_close_and_dst(self):
        now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        gpw = calendar.build_receipt("GPW", now=now)
        us = calendar.build_receipt("US", now=now)
        dates = {s["date"] for s in gpw["sessions"]}
        self.assertNotIn("2026-11-11", dates)
        self.assertNotIn("2026-12-31", dates)
        self.assertNotIn("2027-01-06", dates)
        self.assertGreaterEqual(len(dates), 60)
        self.assertEqual(gpw["sessions"][0]["regular_end"], "2026-10-28T16:05:00Z")
        by_date = {s["date"]: s for s in us["sessions"]}
        self.assertEqual(by_date["2026-10-28"]["regular_start"], "2026-10-28T13:30:00Z")
        self.assertEqual(by_date["2026-11-02"]["regular_start"], "2026-11-02T14:30:00Z")
        self.assertEqual(by_date["2026-11-27"]["regular_end"], "2026-11-27T18:00:00Z")
        self.assertNotIn("2026-11-26", by_date)

    def test_repeated_collection_keeps_bytes_and_future_is_not_loaded(self):
        now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calendar.collect(now=now, root=root)
            original = {p.name: p.read_bytes() for p in root.glob("*.json")}
            calendar.collect(now=now + timedelta(hours=1), root=root)
            self.assertEqual(original, {p.name: p.read_bytes() for p in root.glob("*.json")})
            self.assertEqual(calendar.load_receipts(now=now - timedelta(seconds=1), root=root), {})
            self.assertEqual(set(calendar.load_receipts(now=now, root=root)), {"GPW", "US"})
            target = next(root.glob("*.json"))
            payload = json.loads(target.read_text())
            payload["sessions"].pop()
            target.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "integrity"):
                calendar.load_receipts(now=now, root=root)

    def test_unverified_horizon_is_pending(self):
        with self.assertRaisesRegex(ValueError, "unverified"):
            calendar.build_receipt("GPW", now=datetime(2028, 1, 1, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
