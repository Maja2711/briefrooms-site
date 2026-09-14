import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import weekly_freshness_guard as guard

TZ = ZoneInfo("Europe/Warsaw")


class WeeklyFreshnessGuardTests(unittest.TestCase):
    def test_public_target_is_current_week_monday_through_saturday(self):
        monday = datetime(2026, 9, 14, 12, tzinfo=TZ)
        friday = datetime(2026, 9, 11, 12, tzinfo=TZ)
        saturday = datetime(2026, 9, 12, 12, tzinfo=TZ)
        self.assertEqual(guard.public_target_week(monday), "2026-W38")
        self.assertEqual(guard.public_target_week(friday), "2026-W37")
        self.assertEqual(guard.public_target_week(saturday), "2026-W37")

    def test_sunday_targets_following_trading_week(self):
        sunday = datetime(2026, 9, 13, 12, tzinfo=TZ)
        self.assertEqual(guard.public_target_week(sunday), "2026-W38")

    def test_missing_file_becomes_explicit_missing_decision_not_no_trade(self):
        now = datetime(2026, 9, 14, 12, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = guard.ensure_formal_state(root, now)
            self.assertEqual(result["status"], guard.MISSING_DECISION)
            data = json.loads((root / "2026-W38.json").read_text(encoding="utf-8"))
            self.assertEqual(data["decision_state"], guard.MISSING_DECISION)
            self.assertEqual(data["instruments"], [])
            self.assertNotIn("trade_status", data)
            self.assertNotIn("result", data)

    def test_real_neutral_forecast_is_ready_not_missing(self):
        now = datetime(2026, 9, 14, 12, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "2026-W38.json"
            path.write_text(json.dumps({
                "week_id": "2026-W38",
                "forecast_created_at": "2026-09-13T12:00:00+02:00",
                "instruments": [{"instrument_id": "eurusd", "direction": "neutral", "trade_status": "no_trade"}],
            }), encoding="utf-8")
            self.assertEqual(guard.state(root, now)["status"], "READY")
            before = path.read_text(encoding="utf-8")
            guard.ensure_formal_state(root, now)
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_indeterminate_placeholder_is_normalized_to_explicit_missing(self):
        now = datetime(2026, 9, 14, 12, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "2026-W38.json"
            path.write_text(json.dumps({"week_id": "2026-W38", "forecast_status": "scheduled", "instruments": []}), encoding="utf-8")
            result = guard.ensure_formal_state(root, now)
            self.assertEqual(result["status"], guard.MISSING_DECISION)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["decision_state"], guard.MISSING_DECISION)

    def test_clear_missing_never_removes_real_decision(self):
        now = datetime(2026, 9, 14, 12, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "2026-W38.json"
            path.write_text(json.dumps({
                "week_id": "2026-W38",
                "forecast_created_at": "2026-09-13T12:00:00+02:00",
                "instruments": [{"instrument_id": "eurusd"}],
            }), encoding="utf-8")
            self.assertFalse(guard.clear_missing(root, now))
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
