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
                "engine_version": "eurusd-daily-spot-v1.9.1",
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
                "engine_version": "eurusd-daily-spot-v1.9.1",
                "direction": "FLAT",
                "status": "NO_TRADE",
            })
            result = watchdog.evaluate(now, state_path=state, max_age_minutes=15)
            self.assertEqual("stale", result["status"])
            self.assertIn("daily_state_stale", result["reasons"])
            self.assertTrue(result["auto_dispatch_recovery"])

    def test_wrong_runtime_version_is_stale_even_with_fresh_timestamp(self):
        now = datetime(2026, 10, 20, 19, 35, tzinfo=TZ)
        versions = (
            "eurusd-daily-spot-v1.9.0",
            "eurusd-daily-spot-v1.8.0",
            "eurusd-daily-spot-v99.0.0",
            "",
        )
        for version in versions:
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp) / "state.json"
                self._write(state, {
                    "timestamp": (now - timedelta(minutes=2)).isoformat(),
                    "engine_version": version,
                })
                result = watchdog.evaluate(now, state_path=state, max_age_minutes=15)
                self.assertEqual("stale", result["status"])
                self.assertIn("daily_engine_version_mismatch", result["reasons"])

    def test_current_production_state_does_not_raise_sla_or_dispatch_recovery(self):
        # Reproduce the 2026-10-20 false alarm with a fresh v1.9.1 state.
        # Both a live position and a legitimate no-trade result are healthy.
        now = datetime(2026, 10, 20, 19, 35, tzinfo=TZ)
        for status, direction in (("OPEN", "LONG"), ("NO_TRADE", "FLAT")):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp) / "state.json"
                ready = Path(tmp) / "ready.json"
                self._write(state, {
                    "instrument": "EUR/USD",
                    "timestamp": (now - timedelta(minutes=9)).isoformat(),
                    "engine_version": "eurusd-daily-spot-v1.9.1",
                    "direction": direction,
                    "status": status,
                })
                self._write(ready, {"ready": True})
                result = watchdog.evaluate_pipeline(
                    now,
                    state_path=state,
                    belief_created_at=(now - timedelta(minutes=8)).isoformat(),
                    epistemic_created_at=(now - timedelta(minutes=7)).isoformat(),
                    epistemic_readiness_path=ready,
                )
                self.assertEqual("healthy", result["status"])
                self.assertEqual([], result["reasons"])
                self.assertEqual("NONE", result["recovery_stage"])
                self.assertFalse(result["auto_dispatch_recovery"])
                self.assertFalse(result["daily_sla"]["breached"])
                self.assertEqual([], result["daily_sla"]["daily_reasons"])
                self.assertEqual("NONE", result["daily_sla"]["incident_class"])

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


    def test_pipeline_recovers_belief_first(self):
        now = datetime(2026, 10, 5, 14, 0, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            ready = Path(tmp) / "ready.json"
            self._write(state, {
                "timestamp": (now - timedelta(minutes=4)).isoformat(),
                "engine_version": "eurusd-daily-spot-v1.9.1",
            })
            self._write(ready, {"ready": True, "reason": "ok"})
            result = watchdog.evaluate_pipeline(
                now,
                state_path=state,
                belief_created_at=(now - timedelta(minutes=31)).isoformat(),
                epistemic_created_at=(now - timedelta(minutes=5)).isoformat(),
                epistemic_readiness_path=ready,
            )
            self.assertEqual("stale", result["status"])
            self.assertEqual("BELIEF", result["recovery_stage"])

    def test_pipeline_recovers_epistemic_after_fresh_belief(self):
        now = datetime(2026, 10, 5, 14, 0, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            ready = Path(tmp) / "ready.json"
            self._write(state, {
                "timestamp": (now - timedelta(minutes=4)).isoformat(),
                "engine_version": "eurusd-daily-spot-v1.9.1",
            })
            self._write(ready, {"ready": True, "reason": "ok"})
            result = watchdog.evaluate_pipeline(
                now,
                state_path=state,
                belief_created_at=(now - timedelta(minutes=8)).isoformat(),
                epistemic_created_at=(now - timedelta(minutes=21)).isoformat(),
                epistemic_readiness_path=ready,
            )
            self.assertEqual("stale", result["status"])
            self.assertEqual("EPISTEMIC", result["recovery_stage"])

    def test_pipeline_recovers_daily_only_after_ready_epistemic(self):
        now = datetime(2026, 10, 5, 14, 0, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            ready = Path(tmp) / "ready.json"
            self._write(state, {
                "timestamp": (now - timedelta(minutes=16)).isoformat(),
                "engine_version": "eurusd-daily-spot-v1.9.1",
            })
            self._write(ready, {"ready": True, "reason": "authoritative_daily_eurusd_epistemic_projection"})
            result = watchdog.evaluate_pipeline(
                now,
                state_path=state,
                belief_created_at=(now - timedelta(minutes=8)).isoformat(),
                epistemic_created_at=(now - timedelta(minutes=7)).isoformat(),
                epistemic_readiness_path=ready,
            )
            self.assertEqual("stale", result["status"])
            self.assertEqual("DAILY", result["recovery_stage"])

    def test_unqualified_fresh_epistemic_does_not_create_recovery_loop(self):
        now = datetime(2026, 10, 5, 14, 0, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            ready = Path(tmp) / "ready.json"
            self._write(state, {
                "timestamp": (now - timedelta(minutes=45)).isoformat(),
                "engine_version": "eurusd-daily-spot-v1.9.1",
            })
            self._write(ready, {
                "ready": False,
                "reason": "insufficient_fresh_eurusd_epistemic_coverage",
            })
            result = watchdog.evaluate_pipeline(
                now,
                state_path=state,
                belief_created_at=(now - timedelta(minutes=8)).isoformat(),
                epistemic_created_at=(now - timedelta(minutes=7)).isoformat(),
                epistemic_readiness_path=ready,
            )
            self.assertEqual("upstream_unqualified", result["status"])
            self.assertEqual("NONE", result["recovery_stage"])
            self.assertFalse(result["auto_dispatch_recovery"])

    def test_hard_15m_sla_exposed_even_when_belief_artifact_is_stale(self):
        now = datetime(2026, 10, 15, 20, 15, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            self._write(state, {
                "instrument": "EUR/USD",
                "timestamp": (now - timedelta(minutes=22)).isoformat(),
                "engine_version": "eurusd-daily-spot-v1.9.1",
                "status": "OPEN",
            })
            p = watchdog.evaluate_pipeline(
                now, state_path=state,
                belief_created_at=(now - timedelta(minutes=40)).isoformat(),
                epistemic_created_at=(now - timedelta(minutes=22)).isoformat(),
            )
            self.assertEqual("BELIEF", p["recovery_stage"])
            self.assertTrue(p["daily_sla"]["breached"])
            self.assertEqual("UPSTREAM_DATA_STALE", p["daily_sla"]["incident_class"])
            self.assertEqual(22, p["daily_sla"]["daily_age_minutes"])

    def test_hard_15m_sla_distinguishes_recent_workflow_failure_from_data_stale(self):
        now = datetime(2026, 10, 15, 20, 15, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            ready = Path(tmp) / "ready.json"
            health = Path(tmp) / "health.json"
            self._write(state, {
                "instrument": "EUR/USD",
                "timestamp": (now - timedelta(minutes=18)).isoformat(),
                "engine_version": "eurusd-daily-spot-v1.9.1",
                "status": "NO_TRADE",
            })
            self._write(ready, {"ready": True})
            self._write(health, {"workflows": {"daily-eurusd-monitor.yml": [{
                "id": 12345, "status": "completed", "conclusion": "failure",
                "created_at": (now - timedelta(minutes=8)).isoformat(),
            }]}})
            p = watchdog.evaluate_pipeline(
                now, state_path=state,
                belief_created_at=(now - timedelta(minutes=8)).isoformat(),
                epistemic_created_at=(now - timedelta(minutes=7)).isoformat(),
                epistemic_readiness_path=ready, workflow_health_path=health,
            )
            self.assertEqual("DAILY", p["recovery_stage"])
            self.assertEqual("WORKFLOW_FAILURE", p["daily_sla"]["incident_class"])
            self.assertEqual(12345, p["daily_sla"]["workflow_failure"]["run_id"])

    def test_hard_15m_sla_classifies_slow_queued_run_and_does_not_trigger_on_fresh(self):
        now = datetime(2026, 10, 15, 20, 15, tzinfo=TZ)
        health = {"workflows": {"belief-core-shadow-live.yml": [{
            "id": 333, "status": "in_progress", "conclusion": None,
            "created_at": (now - timedelta(minutes=19)).isoformat(),
        }]}}
        stale = watchdog.classify_daily_sla(
            now, {"status": "stale", "state_age_minutes": 20,
                  "max_age_minutes": 15, "reasons": ["daily_state_stale"]},
            recovery_stage="BELIEF", pipeline_status="stale", workflow_health=health,
        )
        self.assertEqual("WORKFLOW_DELAY", stale["incident_class"])
        fresh = watchdog.classify_daily_sla(
            now, {"status": "healthy", "state_age_minutes": 5,
                  "max_age_minutes": 15, "reasons": []},
            recovery_stage="NONE", pipeline_status="healthy", workflow_health=health,
        )
        self.assertFalse(fresh["breached"])
        self.assertEqual("NONE", fresh["incident_class"])

    def test_unqualified_epistemic_still_alarms_on_daily_sla_without_recovery_loop(self):
        now = datetime(2026, 10, 15, 20, 15, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            state, ready = Path(tmp) / "state.json", Path(tmp) / "ready.json"
            self._write(state, {
                "instrument": "EUR/USD", "timestamp": (now - timedelta(minutes=19)).isoformat(),
                "engine_version": "eurusd-daily-spot-v1.9.1", "status": "NO_TRADE",
            })
            self._write(ready, {"ready": False, "reason": "insufficient_coverage"})
            p = watchdog.evaluate_pipeline(
                now, state_path=state,
                belief_created_at=(now - timedelta(minutes=4)).isoformat(),
                epistemic_created_at=(now - timedelta(minutes=4)).isoformat(),
                epistemic_readiness_path=ready,
            )
            self.assertTrue(p["daily_sla"]["breached"])
            self.assertEqual("EPISTEMIC_UNQUALIFIED", p["daily_sla"]["incident_class"])
            self.assertFalse(p["auto_dispatch_recovery"])

    def test_missing_readiness_recovers_epistemic_not_daily(self):
        now = datetime(2026, 10, 5, 14, 0, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            result = watchdog.evaluate_pipeline(
                now,
                state_path=state,
                belief_created_at=(now - timedelta(minutes=8)).isoformat(),
                epistemic_created_at=(now - timedelta(minutes=7)).isoformat(),
                epistemic_readiness_path=Path(tmp) / "missing.json",
            )
            self.assertEqual("stale", result["status"])
            self.assertEqual("EPISTEMIC", result["recovery_stage"])


if __name__ == "__main__":
    unittest.main()
