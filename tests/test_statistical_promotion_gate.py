from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import autonomous_policy_promotion as ap
from scripts import statistical_promotion_gate as sg
from scripts.statistical_policy_materializer import assert_statistical_authorization


class StatisticalPromotionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data/investments").mkdir(parents=True)
        (self.root / "data/investments/gpw_daily_pick_config.json").write_text(json.dumps({
            "policy_version": "gpw-base-v1", "minimum_composite_score": 72
        }), encoding="utf-8")
        (self.root / "data/investments/us_daily_stock_config.json").write_text(json.dumps({
            "policy_version": "us-base-v1", "target_score": 72
        }), encoding="utf-8")
        (self.root / "data/investments/autonomous_policy_baselines.json").write_text(json.dumps({
            "schema_version": "briefrooms-autonomous-policy-baselines-v1",
            "engines": {
                "gpw_daily": {"config_path": "data/investments/gpw_daily_pick_config.json", "baseline_policy_version": "gpw-base-v1", "parameter": "minimum_composite_score", "baseline_value": 72, "minimum_allowed": 68, "maximum_allowed": 76},
                "us_daily": {"config_path": "data/investments/us_daily_stock_config.json", "baseline_policy_version": "us-base-v1", "parameter": "target_score", "baseline_value": 72, "minimum_allowed": 68, "maximum_allowed": 76}
            }
        }), encoding="utf-8")
        (self.root / sg.CONFIG_PATH).write_text(json.dumps({
            "schema_version": sg.CONFIG_SCHEMA,
            "minimum_paired_n": 25,
            "maximum_paired_n_before_reject": 50,
            "confidence_level": 0.9,
            "bootstrap_samples": 1200,
            "minimum_net_incremental_return_percent": 0.1,
            "minimum_net_positive_rate": 0.55,
            "minimum_unique_symbols": 5,
            "minimum_span_days": 10,
            "maximum_single_positive_contribution_share": 0.5,
            "engines": {
                "gpw_daily": {"round_trip_cost_stress_percent": 0.2},
                "us_daily": {"round_trip_cost_stress_percent": 0.1}
            }
        }), encoding="utf-8")
        self.state = self.root / "state"
        self.state.mkdir()
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        ap.ensure_activation(self.state, self.now - timedelta(days=60))
        self.registry = ap.ensure_registry(self.state, self.root, self.now - timedelta(days=60))
        self.candidate = {
            "candidate_id": "pc-gpw-72-71",
            "engine_id": "gpw_daily",
            "parameter": "minimum_composite_score",
            "gate": "minimum_composite_score",
            "from_value": 72.0,
            "to_value": 71.0,
            "created_at": ap._iso(self.now - timedelta(days=40)),
            "validation_start_at": ap._iso(self.now - timedelta(days=35)),
            "status": "SHADOW_VALIDATION",
            "training": {},
            "validation": {},
            "promotion_gate": {"status": "PASS"},
        }
        self.registry["candidates"][self.candidate["candidate_id"]] = self.candidate
        ap.promote_candidate(self.registry, self.candidate, self.state, self.now - timedelta(days=30))
        self.registry["updated_at"] = ap._iso(self.now - timedelta(days=30))
        ap._atomic_json(self.state / ap.REGISTRY_FILENAME, self.registry)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _append_rows(self, n: int, *, return_percent: float = 0.55, start_days_ago: int = 29) -> None:
        for i in range(n):
            when = self.now - timedelta(days=start_days_ago - i)
            row = {
                "schema_version": ap.SHADOW_SCHEMA,
                "shadow_outcome_id": f"shadow-{i}-{return_percent}",
                "snapshot_id": f"snap-{i}",
                "candidate_id": f"cand-{i}",
                "engine_id": "gpw_daily",
                "decision_at": ap._iso(when),
                "settled_at": ap._iso(when + timedelta(days=3)),
                "symbol": f"SYM{i % 8}",
                "candidate_score": 71.5,
                "first_blocking_gate": "minimum_composite_score",
                "source_threshold": 72.0,
                "other_hard_gates_passed": True,
                "entry": 100.0,
                "exit_price": 100.0 * (1.0 + return_percent / 100.0),
                "exit_reason": "two_session_horizon",
                "exit_day": (when + timedelta(days=3)).date().isoformat(),
                "return_percent": return_percent,
                "r_multiple": return_percent / 2.0,
                "conservative_same_bar": False,
                "settlement_rule": "test",
            }
            row["row_sha256"] = ap._shadow_hash(row)
            ap._append_line(self.state / ap.SHADOW_FILENAME, row)

    def test_paired_metrics_are_net_of_cost_and_bootstrap_positive(self) -> None:
        self._append_rows(30, return_percent=0.55)
        rows = sg._paired_rows(self.state, self.candidate)
        metrics = sg.paired_metrics(rows, candidate_id=self.candidate["candidate_id"], cost_stress_percent=0.2, bootstrap_samples=1200, confidence_level=0.9)
        self.assertEqual(metrics["n"], 30)
        self.assertAlmostEqual(metrics["champion_mean_return_percent"], 0.0)
        self.assertAlmostEqual(metrics["challenger_net_mean_return_percent"], 0.35)
        self.assertGreater(metrics["bootstrap_ci_low_percent"], 0.0)
        status, reasons = sg.statistical_gate(metrics, sg.load_config(self.root))
        self.assertEqual((status, reasons), ("PASS", []))

    def test_under_sample_holds_and_restores_champion(self) -> None:
        self._append_rows(20, return_percent=0.55)
        report = sg.run(self.state, self.root, now=self.now)
        registry = json.loads((self.state / ap.REGISTRY_FILENAME).read_text())
        candidate = registry["candidates"][self.candidate["candidate_id"]]
        self.assertEqual(candidate["status"], "SHADOW_VALIDATION")
        self.assertEqual(candidate["statistical_gate"]["status"], "COLLECTING")
        self.assertEqual(registry["engines"]["gpw_daily"]["revision"], 0)
        self.assertFalse(report["active_policies"]["gpw_daily"]["revision"])

    def test_pass_keeps_promotion_and_creates_exact_authorization(self) -> None:
        self._append_rows(30, return_percent=0.55)
        sg.run(self.state, self.root, now=self.now)
        registry = json.loads((self.state / ap.REGISTRY_FILENAME).read_text())
        candidate = registry["candidates"][self.candidate["candidate_id"]]
        state = registry["engines"]["gpw_daily"]
        self.assertEqual(candidate["status"], "PROMOTED")
        self.assertEqual(candidate["statistical_gate"]["status"], "PASS")
        self.assertEqual(state["revision"], 1)
        auth = sg._load_authorizations(self.state / sg.AUTH_FILENAME)
        row = auth["authorizations"][state["policy_id"]]
        self.assertEqual(row["candidate_id"], self.candidate["candidate_id"])
        self.assertEqual(row["effective_policy_version"], state["effective_policy_version"])
        checked = assert_statistical_authorization(self.state / ap.REGISTRY_FILENAME, self.state / sg.AUTH_FILENAME)
        self.assertEqual(checked["authorized_nonbaseline_engines"], ["gpw_daily"])

    def test_materializer_guard_refuses_promoted_policy_without_pass(self) -> None:
        sg.save_authorizations(self.state / sg.AUTH_FILENAME, sg._load_authorizations(self.state / sg.AUTH_FILENAME))
        with self.assertRaises(RuntimeError):
            assert_statistical_authorization(self.state / ap.REGISTRY_FILENAME, self.state / sg.AUTH_FILENAME)

    def test_bad_large_sample_is_statistically_rejected_and_blocked(self) -> None:
        self.candidate["validation_start_at"] = ap._iso(self.now - timedelta(days=60))
        self.registry["candidates"][self.candidate["candidate_id"]]["validation_start_at"] = self.candidate["validation_start_at"]
        ap._atomic_json(self.state / ap.REGISTRY_FILENAME, self.registry)
        self._append_rows(50, return_percent=0.05, start_days_ago=49)
        sg.run(self.state, self.root, now=self.now)
        registry = json.loads((self.state / ap.REGISTRY_FILENAME).read_text())
        candidate = registry["candidates"][self.candidate["candidate_id"]]
        self.assertEqual(candidate["status"], "STATISTICAL_REJECTED")
        self.assertEqual(candidate["statistical_gate"]["status"], "FAIL")
        self.assertEqual(registry["engines"]["gpw_daily"]["revision"], 0)
        self.assertTrue(any(row.get("reason") == "PR36_STATISTICAL_GATE_FAIL" for row in registry["rejected_transitions"]))


if __name__ == "__main__":
    unittest.main()
