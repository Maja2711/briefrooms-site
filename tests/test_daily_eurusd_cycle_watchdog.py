import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import daily_eurusd_cycle_watchdog as watchdog

TZ = ZoneInfo("Europe/Warsaw")


class DailyEURUSDCycleWatchdogTests(unittest.TestCase):
    def _write(self, path: Path, payload):
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_fresh_v19_state_is_healthy_during_active_market(self):
        now = datetime(2026, 10, 5, 14, 0, tzinfo=TZ)  # Monday
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            self._write(state, {
                "instrument": "EUR/USD",
                "timestamp": (now - timedelta(minutes=6)).isoformat(),
                "engine_version": "eurusd-daily-spot-v1.9.0",
                "direction": "SHORT",
                "status": "OPEN",
            })
            result = watchdog.evaluate(now, state_path=state, max_age_minutes=15)
            self.assertEqual("healthy", result["status"])
            self.assertEqual([], result["reasons"])
            self.assertLess(result["state_age_minutes"], 15)

    def test_stale_state_fails_closed_and_requests_recovery(self):
        now = datetime(2026, 10, 5, 14, 0, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            self._write(state, {
                "instrument": "EUR/USD",
                "timestamp": (now - timedelta(minutes=16)).isoformat(),
                "engine_version": "eurusd-daily-spot-v1.9.0",
                "direction": "FLAT",
                "status": "NO_TRADE",
            })
            result = watchdog.evaluate(now, state_path=state, max_age_minutes=15)
            self.assertEqual("stale", result["status"])
            self.assertIn("daily_state_stale", result["reasons"])
            self.assertTrue(result["auto_dispatch_recovery"])

    def test_wrong_runtime_version_is_stale_even_with_fresh_timestamp(self):
        now = datetime(2026, 10, 5, 14, 0, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            self._write(state, {
                "timestamp": (now - timedelta(minutes=2)).isoformat(),
                "engine_version": "eurusd-daily-spot-v1.8.0",
            })
            result = watchdog.evaluate(now, state_path=state, max_age_minutes=15)
            self.assertEqual("stale", result["status"])
            self.assertIn("daily_engine_version_mismatch", result["reasons"])

    def test_weekend_does_not_raise_false_alarm(self):
        now = datetime(2026, 10, 10, 14, 0, tzinfo=TZ)  # Saturday
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "missing.json"
            result = watchdog.evaluate(now, state_path=state, max_age_minutes=15)
            self.assertEqual("inactive_window", result["status"])
            self.assertFalse(result["market_active"])

    def test_sunday_evening_reactivates_watchdog(self):
        before_open = datetime(2026, 10, 11, 22, 59, tzinfo=TZ)
        after_open = datetime(2026, 10, 11, 23, 1, tzinfo=TZ)
        self.assertFalse(watchdog.fx_market_active(before_open))
        self.assertTrue(watchdog.fx_market_active(after_open))


if __name__ == "__main__":
    unittest.main()
