from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import autonomous_policy_closed_loop as loop
from scripts import autonomous_policy_promotion as ap
from scripts import autonomous_policy_promotion_v2 as pr35v2


class AutonomousPolicyClosedLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        inv = self.root / "data/investments"
        inv.mkdir(parents=True)
        (inv / "gpw_daily_pick_history").mkdir()
        (inv / "us_daily_stock_history").mkdir()
        (inv / "gpw_daily_pick_config.json").write_text(json.dumps({
            "policy_version": "gpw-base-v1",
            "minimum_composite_score": 72,
        }), encoding="utf-8")
        (inv / "us_daily_stock_config.json").write_text(json.dumps({
            "policy_version": "us-base-v1",
            "target_score": 72,
        }), encoding="utf-8")
        (inv / "statistical_promotion_gate_v2_config.json").write_text(json.dumps({
            "schema_version": "briefrooms-statistical-promotion-gate-config-v2",
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
                "us_daily": {"round_trip_cost_stress_percent": 0.1},
            },
        }), encoding="utf-8")
        (inv / "autonomous_policy_closed_loop_config.json").write_text(json.dumps({
            "schema_version": loop.CONFIG_SCHEMA,
            "closed_loop_enabled": True,
            "automatic_materialization_enabled": True,
            "automatic_rollback_enabled": True,
            "manual_approval_required": False,
            "require_pr35_pass": True,
            "require_pr36_pass": True,
            "require_disjoint_validation_samples": True,
            "require_hash_committed_validation_epochs": True,
            "required_research_methodology_version": 2,
            "maximum_promotions_per_engine_per_run": 1,
            "code_mutation_allowed": False,
            "arbitrary_parameter_mutation_allowed": False,
            "hard_safety_gate_mutation_allowed": False,
            "trade_execution_allowed": False,
        }), encoding="utf-8")

        self.research = self.root / "research"
        self.research.mkdir()
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        ap.ensure_activation(self.research, self.now)
        registry = ap.ensure_registry(self.research, self.root, self.now)
        pr35v2._governance(registry, self.now)
        self.candidate = {
            "candidate_id": "policycand-test",
            "engine_id": "gpw_daily",
            "parameter": "minimum_composite_score",
            "gate": "minimum_composite_score",
            "from_value": 72.0,
            "to_value": 71.0,
            "created_at": ap._iso(self.now - timedelta(days=70)),
            "promotion_methodology_version": 2,
            "validation_target_n": 30,
            "validation_start_at": ap._iso(self.now - timedelta(days=65)),
            "status": "PROMOTION_ELIGIBLE_BUT_FROZEN",
            "promotion_gate": {
                "status": "PASS",
                "formal_test_performed": True,
                "formal_sample_n": 30,
                "sample_shadow_outcome_ids": [f"pr35-{i}" for i in range(30)],
            },
            "pr35_passed_at": ap._iso(self.now - timedelta(days=35)),
            "confirmation_start_at": ap._iso(self.now - timedelta(days=34)),
            "confirmation_sample_reuse_allowed": False,
            "statistical_gate": {
                "status": "PASS",
                "blocking_reasons": [],
                "fresh_holdout": True,
                "formal_test_performed": True,
                "formal_sample_n": 30,
                "sample_shadow_outcome_ids": [f"pr36-{i}" for i in range(30)],
            },
            "promotion_eligible_at": ap._iso(self.now - timedelta(days=1)),
            "production_promotion_enabled": False,
            "validation_epochs": {
                "pr35": {"epoch_id": "e35"},
                "pr36": {"epoch_id": "e36"},
            },
        }
        registry["candidates"][self.candidate["candidate_id"]] = self.candidate
        ap._atomic_json(self.research / ap.REGISTRY_FILENAME, registry)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _epoch_side_effect(self, state_dir, candidate, *, stage, reference):
        if stage == "PR35":
            return {"committed_at": ap._iso(self.now - timedelta(days=65))}
        return {"committed_at": ap._iso(self.now - timedelta(days=34))}

    def _apply(self, when: datetime | None = None):
        with patch.object(loop.pli, "verify", return_value={"production_promotion_enabled": False}), \
             patch.object(loop.ve, "verify_epoch_reference", side_effect=self._epoch_side_effect):
            return loop.apply_closed_loop(self.research, self.root, now=when or self.now)

    def test_formal_pr35_pr36_pass_is_materialized_without_manual_approval(self) -> None:
        report = self._apply()
        self.assertEqual(report["promotions"][0]["status"], "PROMOTED")
        self.assertEqual(report["safety"]["manual_approval_required_after_formal_gates"], False)

        gpw = json.loads((self.root / "data/investments/gpw_daily_pick_config.json").read_text())
        self.assertEqual(gpw["minimum_composite_score"], 71)
        self.assertEqual(gpw["policy_version"], "gpw-base-v1+auto1")

        state = json.loads((self.root / loop.STATE_PATH).read_text())
        self.assertEqual(state["engines"]["gpw_daily"]["revision"], 1)
        self.assertEqual(state["engines"]["gpw_daily"]["value"], 71.0)
        self.assertEqual(state["candidate_outcomes"]["policycand-test"]["status"], "PROMOTED")
        loop.verify_state(self.root, state)

        registry = json.loads((self.research / ap.REGISTRY_FILENAME).read_text())
        self.assertEqual(registry["candidates"]["policycand-test"]["status"], loop.TERMINAL_PROMOTED)

    def test_overlapping_pr35_pr36_samples_fail_closed(self) -> None:
        registry = json.loads((self.research / ap.REGISTRY_FILENAME).read_text())
        registry["candidates"]["policycand-test"]["statistical_gate"]["sample_shadow_outcome_ids"][0] = "pr35-0"
        ap._atomic_json(self.research / ap.REGISTRY_FILENAME, registry)
        with self.assertRaisesRegex(ValueError, "overlap"):
            self._apply()
        gpw = json.loads((self.root / "data/investments/gpw_daily_pick_config.json").read_text())
        self.assertEqual(gpw["minimum_composite_score"], 72)
        self.assertFalse((self.root / loop.STATE_PATH).exists())

    def test_unallowlisted_parameter_fails_closed(self) -> None:
        registry = json.loads((self.research / ap.REGISTRY_FILENAME).read_text())
        registry["candidates"]["policycand-test"]["parameter"] = "maximum_risk_percent"
        ap._atomic_json(self.research / ap.REGISTRY_FILENAME, registry)
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            self._apply()
        gpw = json.loads((self.root / "data/investments/gpw_daily_pick_config.json").read_text())
        self.assertEqual(gpw["minimum_composite_score"], 72)

    def test_retired_stock_production_authority_never_materializes(self) -> None:
        config_path = self.root / loop.CONFIG_PATH
        cfg = json.loads(config_path.read_text())
        cfg["automatic_materialization_enabled"] = False
        cfg["production_authority"] = "RETIRED_TO_STOCK_TRADING_COMPONENT_PROMOTION"
        config_path.write_text(json.dumps(cfg), encoding="utf-8")

        before_gpw = (self.root / "data/investments/gpw_daily_pick_config.json").read_text()
        before_us = (self.root / "data/investments/us_daily_stock_config.json").read_text()
        report = self._apply()

        self.assertEqual(report["status"], "RESEARCH_ONLY_PRODUCTION_AUTHORITY_RETIRED")
        self.assertEqual(report["promotions"], [])
        self.assertEqual(report["materialized_config_paths"], [])
        self.assertFalse(report["safety"]["production_materialization"])
        self.assertEqual(before_gpw, (self.root / "data/investments/gpw_daily_pick_config.json").read_text())
        self.assertEqual(before_us, (self.root / "data/investments/us_daily_stock_config.json").read_text())

        registry = json.loads((self.research / ap.REGISTRY_FILENAME).read_text())
        self.assertEqual(registry["candidates"]["policycand-test"]["status"], "PROMOTION_ELIGIBLE_BUT_FROZEN")

    def test_bad_live_evidence_rolls_back_parent_and_blocks_transition(self) -> None:
        self._apply()
        history = self.root / "data/investments/gpw_daily_pick_history"
        for i in range(3):
            day = self.now.date() + timedelta(days=i + 1)
            (history / f"{day.isoformat()}.json").write_text(json.dumps({
                "date": day.isoformat(),
                "generated_at": ap._iso(self.now + timedelta(days=i + 1)),
                "policy_version": "gpw-base-v1+auto1",
                "outcome": {
                    "status": "RESOLVED",
                    "r_multiple": -1.0,
                    "return_percent": -1.0,
                },
            }), encoding="utf-8")

        report = self._apply(self.now + timedelta(days=5))
        self.assertEqual(len(report["rollbacks"]), 1)
        gpw = json.loads((self.root / "data/investments/gpw_daily_pick_config.json").read_text())
        self.assertEqual(gpw["minimum_composite_score"], 72)
        self.assertEqual(gpw["policy_version"], "gpw-base-v1")

        state = json.loads((self.root / loop.STATE_PATH).read_text())
        self.assertEqual(state["engines"]["gpw_daily"]["revision"], 0)
        self.assertEqual(state["candidate_outcomes"]["policycand-test"]["status"], "ROLLED_BACK")
        registry = json.loads((self.research / ap.REGISTRY_FILENAME).read_text())
        self.assertEqual(registry["candidates"]["policycand-test"]["status"], loop.TERMINAL_ROLLED_BACK)
        self.assertTrue(any(row.get("reason") == "autonomous_closed_loop_rollback" for row in registry["rejected_transitions"]))


if __name__ == "__main__":
    unittest.main()
