"""Offline private D1 watchdog threshold and recovery safety tests."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import saxo_private_daily_watchdog as watch


class SaxoPrivateDailyLivenessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 28, 14, 45, tzinfo=timezone.utc)

    def row(self, age):
        return [{"generation": 4, "updated_at": (self.now - timedelta(minutes=age)).isoformat()}]

    def test_healthy_19_minutes(self):
        self.assertEqual(watch.evaluate(self.row(19), self.now, market_active=True), "FRESH")

    def test_stale_21_minutes(self):
        self.assertEqual(watch.evaluate(self.row(21), self.now, market_active=True), "STALE")

    def test_closed_market_skips_no_data(self):
        self.assertEqual(watch.evaluate([], self.now, market_active=False), "INACTIVE_FX_MARKET")

    def test_missing_and_uninitialized_state_never_get_recovery_approval(self):
        self.assertEqual(watch.evaluate([], self.now, market_active=True), "MISSING_D1_STATE")
        self.assertEqual(watch.evaluate([{"generation": 0, "updated_at": self.now.isoformat()}], self.now, market_active=True), "NO_COMPLETED_PRIVATE_CYCLE")

    def test_future_timestamp_fails(self):
        self.assertEqual(watch.evaluate(self.row(-5), self.now, market_active=True), "D1_CLOCK_SKEW")

    def test_recovery_refuses_duplicate_active_run(self):
        with patch.object(watch, "_api", return_value={"workflow_runs": [{"id": 1}]}) as api:
            self.assertEqual(watch.recovery("Maja2711/briefrooms-site", "test-only"), "ALREADY_RUNNING")
        self.assertEqual(api.call_count, 1)

    def test_recovery_only_dispatches_one_when_idle(self):
        def fake_api(url, token, *, payload=None):
            if payload is None:
                return {"workflow_runs": []}
            self.assertEqual(payload, {"ref": "main", "inputs": {"operation": "run"}})
            return {}
        with patch.object(watch, "_api", side_effect=fake_api) as api:
            self.assertEqual(watch.recovery("Maja2711/briefrooms-site", "test-only"), "DISPATCHED_ONE_RECOVERY")
        self.assertEqual(api.call_count, 5)

    def test_disabled_watcher_never_reads_private_state(self):
        with patch.dict(watch.os.environ, {"SAXO_PRIVATE_AUTO_ENABLED": "false"}):
            with patch.object(watch, "D1") as d1:
                self.assertEqual(watch.main(), 0)
                d1.assert_not_called()


if __name__ == "__main__":
    unittest.main()
