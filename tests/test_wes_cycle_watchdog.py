import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import wes_cycle_watchdog as watchdog

TZ = ZoneInfo("Europe/Warsaw")


class WesCycleWatchdogTests(unittest.TestCase):
    def _write(self, path: Path, payload):
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_healthy_cycle_requires_fresh_preflight_and_postflight(self):
        now = datetime(2026, 10, 5, 13, 0, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = root / "policy.json"
            week = root / "week.json"
            report = root / "report.json"
            self._write(policy, {"wes_cycle_watchdog": {"enabled": True, "max_age_minutes": 25}})
            self._write(week, {
                "market_window": {
                    "entry_target_local": "2026-10-05T08:00:00+02:00",
                    "exit_target_local": "2026-10-09T22:00:00+02:00",
                },
                "wes": {
                    "version": "WES-1.3.1",
                    "last_preflight_at": (now - timedelta(minutes=12)).isoformat(),
                },
            })
            self._write(report, {
                "version": "WES-1.3.1",
                "mode": "postflight",
                "status": "completed",
                "checked_at": (now - timedelta(minutes=11)).isoformat(),
            })
            result = watchdog.evaluate(now, policy_path=policy, weekly_path=week, report_path=report)
            self.assertEqual("healthy", result["status"])
            self.assertEqual([], result["reasons"])

    def test_stale_preflight_is_a_liveness_failure(self):
        now = datetime(2026, 10, 5, 13, 0, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = root / "policy.json"
            week = root / "week.json"
            report = root / "report.json"
            self._write(policy, {"wes_cycle_watchdog": {"enabled": True, "max_age_minutes": 25, "auto_dispatch_recovery": True}})
            self._write(week, {
                "market_window": {
                    "entry_target_local": "2026-10-05T08:00:00+02:00",
                    "exit_target_local": "2026-10-09T22:00:00+02:00",
                },
                "wes": {
                    "version": "WES-1.3.1",
                    "last_preflight_at": (now - timedelta(minutes=31)).isoformat(),
                },
            })
            self._write(report, {
                "version": "WES-1.3.1",
                "mode": "postflight",
                "status": "completed",
                "checked_at": (now - timedelta(minutes=10)).isoformat(),
            })
            result = watchdog.evaluate(now, policy_path=policy, weekly_path=week, report_path=report)
            self.assertEqual("stale", result["status"])
            self.assertIn("wes_preflight_stale", result["reasons"])
            self.assertTrue(result["auto_dispatch_recovery"])

    def test_version_mismatch_fails_closed(self):
        now = datetime(2026, 10, 5, 13, 0, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = root / "policy.json"
            week = root / "week.json"
            report = root / "report.json"
            self._write(policy, {"wes_cycle_watchdog": {"enabled": True, "max_age_minutes": 25}})
            self._write(week, {
                "market_window": {
                    "entry_target_local": "2026-10-05T08:00:00+02:00",
                    "exit_target_local": "2026-10-09T22:00:00+02:00",
                },
                "wes": {"version": "WES-1.2.0", "last_preflight_at": now.isoformat()},
            })
            self._write(report, {"version": "WES-1.2.0", "mode": "postflight", "status": "completed", "checked_at": now.isoformat()})
            result = watchdog.evaluate(now, policy_path=policy, weekly_path=week, report_path=report)
            self.assertEqual("stale", result["status"])
            self.assertIn("wes_state_version_mismatch", result["reasons"])
            self.assertIn("wes_report_version_mismatch", result["reasons"])


if __name__ == "__main__":
    unittest.main()
