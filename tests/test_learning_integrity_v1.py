from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import anytime_valid_inference as avi
from scripts import autonomous_policy_promotion as ap
from scripts import autonomous_policy_promotion_v2 as v2
from scripts import learning_cases
from scripts import promotion_learning_integrity as pli
from scripts import statistical_promotion_gate_v2 as sg2
from scripts import validation_epoch as ve


class ValidationEpochAndAnytimeTests(unittest.TestCase):
    def _candidate(self) -> dict:
        return {
            "candidate_id": "pc-integrity",
            "engine_id": "gpw_daily",
            "parameter": "minimum_composite_score",
            "gate": "minimum_composite_score",
            "from_value": 72.0,
            "to_value": 71.0,
            "promotion_methodology_version": 2,
            "validation_target_n": 30,
        }

    def test_epoch_hash_chain_freezes_definition_and_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 9, 4, 8, tzinfo=timezone.utc)
            shadow = [{"shadow_outcome_id": "old", "row_sha256": "abc", "decision_at": "2026-09-03T08:00:00Z"}]
            ref = ve.commit_epoch(
                root,
                self._candidate(),
                stage="PR35",
                committed_at=now,
                primary_inference_plan={"method": "fixed_n", "fixed_n": 30},
                shadow_anytime_plan=avi.plan(),
                shadow_rows=shadow,
            )
            event = ve.verify_epoch_reference(root, self._candidate(), stage="PR35", reference=ref)
            self.assertEqual(event["evidence_boundary"]["existing_shadow_count"], 1)
            self.assertEqual(event["evidence_boundary"]["strict_decision_at_after"], "2026-09-04T08:00:00Z")
            self.assertFalse(event["evidence_boundary"]["older_decisions_formally_eligible"])
            self.assertTrue(ve.verify_chain(root / ve.LEDGER_FILENAME)["ok"])
            changed = self._candidate()
            changed["to_value"] = 70.0
            with self.assertRaises(ValueError):
                ve.verify_epoch_reference(root, changed, stage="PR35", reference=ref)

    def test_anytime_confidence_sequence_is_shadow_only_and_time_uniform_path(self) -> None:
        result = avi.confidence_sequence([5.0] * 30)
        self.assertEqual(result["authority"], "shadow_only")
        self.assertFalse(result["formal_promotion_decision"])
        self.assertEqual(result["current"]["n"], 30)
        self.assertEqual(result["current"]["status"], "POSITIVE")
        self.assertGreater(result["current"]["lower"], 0.0)
        self.assertIsNotNone(result["first_positive_n"])
        self.assertEqual(result["plan"]["alpha_spending"], "alpha_n=alpha/(n*(n+1))")

    def test_winsorization_is_explicit_estimand_not_silent_raw_mean_claim(self) -> None:
        result = avi.confidence_sequence([50.0, -50.0, 1.0])
        self.assertEqual(result["winsorized_observation_count"], 2)
        self.assertIn("winsorized", result["plan"]["estimand"])


class PromotionLearningIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        investments = self.root / "data/investments"
        investments.mkdir(parents=True)
        (investments / "gpw_daily_pick_history").mkdir()
        (investments / "us_daily_stock_history").mkdir()
        (investments / "gpw_daily_pick_config.json").write_text(json.dumps({
            "policy_version": "gpw-base-v1", "minimum_composite_score": 72
        }), encoding="utf-8")
        (investments / "us_daily_stock_config.json").write_text(json.dumps({
            "policy_version": "us-base-v1", "target_score": 72
        }), encoding="utf-8")
        (self.root / sg2.CONFIG_PATH).write_text(json.dumps({
            "schema_version": sg2.CONFIG_SCHEMA,
            "promotion_methodology_version": 2,
            "production_promotion_enabled": False,
            "fixed_paired_n": 30,
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
        self.learning = self.root / "learning"
        self.state.mkdir()
        self.learning.mkdir()
        self.now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
        ap.ensure_activation(self.state, self.now - timedelta(days=10))
        registry = ap.ensure_registry(self.state, self.root, self.now - timedelta(days=10))
        v2._governance(registry, self.now - timedelta(days=10))
        candidate = {
            "candidate_id": "pc-existing",
            "engine_id": "gpw_daily",
            "parameter": "minimum_composite_score",
            "gate": "minimum_composite_score",
            "from_value": 72.0,
            "to_value": 71.0,
            "created_at": ap._iso(self.now - timedelta(days=9)),
            "promotion_methodology_version": 2,
            "validation_target_n": 30,
            "validation_start_at": ap._iso(self.now - timedelta(days=9)),
            "status": "SHADOW_VALIDATION",
            "training": {},
            "validation": ap._summary([]),
            "promotion_gate": {"status": "COLLECTING"},
            "pr35_passed_at": None,
            "confirmation_start_at": None,
        }
        registry["candidates"][candidate["candidate_id"]] = candidate
        ap._atomic_json(self.state / ap.REGISTRY_FILENAME, registry)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _row(self, idx: int, when: datetime, ret: float = 0.6) -> dict:
        row = {
            "schema_version": ap.SHADOW_SCHEMA,
            "shadow_outcome_id": f"shadow-{idx}",
            "snapshot_id": f"snap-{idx}",
            "candidate_id": f"underlying-{idx}",
            "engine_id": "gpw_daily",
            "decision_at": ap._iso(when),
            "settled_at": ap._iso(when + timedelta(days=3)),
            "symbol": f"SYM{idx % 8}",
            "candidate_score": 71.5,
            "first_blocking_gate": "minimum_composite_score",
            "source_threshold": 72.0,
            "other_hard_gates_passed": True,
            "entry": 100.0,
            "exit_price": 100.6,
            "exit_reason": "two_session_horizon",
            "exit_day": (when + timedelta(days=3)).date().isoformat(),
            "return_percent": ret,
            "r_multiple": 0.45,
            "conservative_same_bar": False,
            "settlement_rule": "test",
        }
        row["row_sha256"] = ap._shadow_hash(row)
        return row

    def test_migration_commits_epoch_and_forbids_old_sample_reuse_then_pr35_pass_commits_pr36(self) -> None:
        ap._append_line(self.state / ap.SHADOW_FILENAME, self._row(999, self.now - timedelta(days=1)))
        result = pli.run_pr35(
            self.state, self.learning, self.root, allow_network=False, now=self.now
        )
        registry = json.loads((self.state / ap.REGISTRY_FILENAME).read_text())
        candidate = registry["candidates"]["pc-existing"]
        self.assertEqual(result["validation_epochs_before_run"]["migrated_candidates"], 1)
        self.assertEqual(candidate["promotion_gate"]["observed_n"], 0)
        self.assertEqual(candidate["anytime_valid_shadow"]["pr35"]["current"]["n"], 0)
        self.assertEqual(
            candidate["validation_start_at"],
            candidate["validation_epochs"]["pr35"]["committed_at"],
        )
        self.assertEqual(registry["engines"]["gpw_daily"]["revision"], 0)

        for i in range(30):
            ap._append_line(
                self.state / ap.SHADOW_FILENAME,
                self._row(i, self.now + timedelta(days=i + 1)),
            )
        pli.run_pr35(
            self.state,
            self.learning,
            self.root,
            allow_network=False,
            now=self.now + timedelta(days=40),
        )
        registry = json.loads((self.state / ap.REGISTRY_FILENAME).read_text())
        candidate = registry["candidates"]["pc-existing"]
        self.assertEqual(candidate["status"], "PR36_HOLDOUT")
        self.assertEqual(candidate["promotion_gate"]["formal_sample_n"], 30)
        self.assertEqual(candidate["anytime_valid_shadow"]["pr35"]["current"]["n"], 30)
        self.assertIn("pr36", candidate["validation_epochs"])
        self.assertEqual(
            candidate["confirmation_start_at"],
            candidate["validation_epochs"]["pr36"]["committed_at"],
        )
        self.assertEqual(candidate["anytime_valid_shadow"]["pr36"]["current"]["n"], 0)
        self.assertEqual(registry["engines"]["gpw_daily"]["revision"], 0)
        verified = pli.verify(self.state, self.root)
        self.assertTrue(verified["fixed_n_primary_gate_unchanged"])
        self.assertFalse(verified["production_promotion_enabled"])


class LearningCaseTests(unittest.TestCase):
    def _settled(self) -> dict:
        return {
            "experience_id": "exp-1",
            "decision_event_id": "d1",
            "outcome_event_id": "o1",
            "decision_at": "2026-09-01T08:00:00Z",
            "engine": "us",
            "engine_version": "v1",
            "instrument": "ABC",
            "action": "LONG",
            "confidence": 0.9,
            "entry": 100.0,
            "stop_loss": 98.0,
            "take_profit": 104.0,
            "expected_return": 0.02,
            "market_snapshot_id": None,
            "epistemic_state_id": None,
            "decision_envelope_id": "de-1",
            "status": "SETTLED",
            "outcome": {
                "settled_at": "2026-09-02T08:00:00Z",
                "net_return_fraction": -0.001,
                "gross_return_fraction": 0.001,
                "cost_fraction": 0.002,
                "r_multiple": -1.0,
                "mae_fraction": -0.02,
                "mfe_fraction": 0.01,
                "benchmark_return_fraction": 0.003,
                "exit_reason": "stop",
            },
        }

    def test_structured_reasoning_is_deterministic_noncausal_and_zero_authority(self) -> None:
        first = learning_cases.build_learning_case(self._settled())
        second = learning_cases.build_learning_case(self._settled())
        self.assertEqual(first["learning_case_id"], second["learning_case_id"])
        self.assertEqual(first["case_hash"], second["case_hash"])
        self.assertFalse(first["causal_identification_claimed"])
        self.assertFalse(first["private_chain_of_thought_required"])
        self.assertTrue(all(value is False for value in first["authority"].values()))
        by_code = {row["code"]: row for row in first["error_attribution"]}
        self.assertEqual(by_code["execution_or_cost"]["status"], "SUPPORTED")
        self.assertEqual(by_code["confidence_calibration"]["status"], "SUSPECTED")
        self.assertEqual(by_code["epistemic_context"]["status"], "NOT_EVALUABLE")
        self.assertEqual(by_code["data_quality"]["status"], "NOT_EVALUABLE")
        flat = next(row for row in first["counterfactual_checks"] if row["counterfactual"] == "FLAT")
        self.assertTrue(flat["available"])
        self.assertAlmostEqual(flat["increment_vs_observed_fraction"], 0.001)

    def test_pending_is_skipped_and_future_leakage_fails_closed(self) -> None:
        pending = dict(self._settled())
        pending["status"] = "PENDING"
        pending["outcome"] = None
        cases = learning_cases.build_learning_cases([pending, self._settled()])
        self.assertEqual(len(cases), 1)

        bad = self._settled()
        bad["outcome"] = dict(bad["outcome"])
        bad["outcome"]["settled_at"] = bad["decision_at"]
        with self.assertRaises(ValueError):
            learning_cases.build_learning_case(bad)


if __name__ == "__main__":
    unittest.main()
