from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import autonomous_policy_promotion as ap
from scripts import autonomous_policy_promotion_v2 as v2
from scripts import statistical_promotion_gate_v2 as sg2
from scripts.policy_runtime_overlay import PROMOTION_FREEZE_STATUS, apply_active_policy
from scripts.statistical_policy_materializer import materialize


class PromotionSafetyV2Tests(unittest.TestCase):
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
        self.state.mkdir()
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        ap.ensure_activation(self.state, self.now)
        self.registry = ap.ensure_registry(self.state, self.root, self.now)
        v2._governance(self.registry, self.now)
        ap._atomic_json(self.state / ap.REGISTRY_FILENAME, self.registry)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _row(self, idx: int, when: datetime, *, ret: float = 0.6) -> dict:
        row = {
            "schema_version": ap.SHADOW_SCHEMA,
            "shadow_outcome_id": f"shadow-{idx}-{when.timestamp()}",
            "snapshot_id": f"snap-{idx}",
            "candidate_id": f"cand-{idx}",
            "engine_id": "gpw_daily",
            "decision_at": ap._iso(when),
            "settled_at": ap._iso(when + timedelta(days=3)),
            "symbol": f"SYM{idx % 8}",
            "candidate_score": 71.5,
            "first_blocking_gate": "minimum_composite_score",
            "source_threshold": 72.0,
            "other_hard_gates_passed": True,
            "entry": 100.0,
            "exit_price": 100.0 * (1.0 + ret / 100.0),
            "exit_reason": "two_session_horizon",
            "exit_day": (when + timedelta(days=3)).date().isoformat(),
            "return_percent": ret,
            "r_multiple": 0.45,
            "conservative_same_bar": False,
            "settlement_rule": "test",
        }
        row["row_sha256"] = ap._shadow_hash(row)
        return row

    def _append(self, row: dict) -> None:
        ap._append_line(self.state / ap.SHADOW_FILENAME, row)

    def _candidate(self, validation_start: datetime) -> dict:
        return {
            "candidate_id": "pc-v2",
            "engine_id": "gpw_daily",
            "parameter": "minimum_composite_score",
            "gate": "minimum_composite_score",
            "from_value": 72.0,
            "to_value": 71.0,
            "created_at": ap._iso(validation_start),
            "promotion_methodology_version": 2,
            "validation_target_n": 30,
            "validation_start_at": ap._iso(validation_start),
            "status": "SHADOW_VALIDATION",
            "training": {},
            "validation": ap._summary([]),
            "promotion_gate": {"status": "COLLECTING"},
            "pr35_passed_at": None,
            "confirmation_start_at": None,
        }

    def test_pr35_waits_for_exact_fixed_n_and_never_changes_champion(self) -> None:
        candidate = self._candidate(self.now)
        self.registry["candidates"][candidate["candidate_id"]] = candidate
        for i in range(29):
            self._append(self._row(i, self.now + timedelta(days=i + 1)))

        passed, rejected = v2.update_candidates_fixed_n(self.registry, self.state, self.now + timedelta(days=31))
        self.assertEqual((passed, rejected), (0, 0))
        self.assertEqual(candidate["promotion_gate"]["status"], "COLLECTING")
        self.assertFalse(candidate["promotion_gate"]["formal_test_performed"])
        self.assertEqual(self.registry["engines"]["gpw_daily"]["revision"], 0)

        self._append(self._row(29, self.now + timedelta(days=30)))
        passed, rejected = v2.update_candidates_fixed_n(self.registry, self.state, self.now + timedelta(days=32))
        self.assertEqual((passed, rejected), (1, 0))
        self.assertEqual(candidate["status"], "PR36_HOLDOUT")
        self.assertEqual(candidate["promotion_gate"]["formal_sample_n"], 30)
        self.assertEqual(len(candidate["promotion_gate"]["sample_shadow_outcome_ids"]), 30)
        self.assertEqual(self.registry["engines"]["gpw_daily"]["revision"], 0)
        self.assertEqual(self.registry["engines"]["gpw_daily"]["overrides"], {})

        locked_ids = list(candidate["promotion_gate"]["sample_shadow_outcome_ids"])
        self._append(self._row(30, self.now + timedelta(days=33)))
        passed, rejected = v2.update_candidates_fixed_n(self.registry, self.state, self.now + timedelta(days=34))
        self.assertEqual((passed, rejected), (0, 0))
        self.assertEqual(candidate["promotion_gate"]["sample_shadow_outcome_ids"], locked_ids)

    def test_pr36_uses_only_fresh_post_pr35_holdout_and_pass_is_frozen(self) -> None:
        candidate = self._candidate(self.now - timedelta(days=40))
        candidate.update({
            "status": "PR36_HOLDOUT",
            "pr35_passed_at": ap._iso(self.now),
            "confirmation_start_at": ap._iso(self.now),
            "confirmation_sample_reuse_allowed": False,
            "promotion_gate": {"status": "PASS", "formal_sample_n": 30},
        })
        self.registry["candidates"][candidate["candidate_id"]] = candidate
        ap._atomic_json(self.state / ap.REGISTRY_FILENAME, self.registry)

        self._append(self._row(999, self.now - timedelta(minutes=1)))
        for i in range(30):
            self._append(self._row(i, self.now + timedelta(days=i + 1)))

        report = sg2.run(self.state, self.root, now=self.now + timedelta(days=35))
        registry = json.loads((self.state / ap.REGISTRY_FILENAME).read_text())
        candidate = registry["candidates"]["pc-v2"]
        self.assertEqual(report["production_promotion_enabled"], False)
        self.assertEqual(candidate["status"], "PROMOTION_ELIGIBLE_BUT_FROZEN")
        self.assertTrue(candidate["statistical_gate"]["fresh_holdout"])
        self.assertEqual(candidate["statistical_gate"]["formal_sample_n"], 30)
        ids = candidate["statistical_gate"]["sample_shadow_outcome_ids"]
        self.assertEqual(len(ids), 30)
        self.assertNotIn("shadow-999-" + str((self.now - timedelta(minutes=1)).timestamp()), ids)
        self.assertEqual(registry["engines"]["gpw_daily"]["revision"], 0)
        self.assertEqual(registry["engines"]["gpw_daily"]["overrides"], {})

    def test_runtime_overlay_and_materializer_fail_closed_during_freeze(self) -> None:
        state = self.registry["engines"]["gpw_daily"]
        state.update({
            "revision": 1,
            "policy_id": "policy-1",
            "effective_policy_version": "gpw-base-v1+auto1",
            "overrides": {"minimum_composite_score": 71.0},
            "activated_at": ap._iso(self.now),
            "source_candidate_id": "candidate-1",
        })
        ap._atomic_json(self.state / ap.REGISTRY_FILENAME, self.registry)
        baseline = {"policy_version": "gpw-base-v1", "minimum_composite_score": 72}
        effective = apply_active_policy("gpw_daily", baseline, registry_path=self.state / ap.REGISTRY_FILENAME)
        self.assertEqual(effective["minimum_composite_score"], 72)
        self.assertEqual(effective["policy_version"], "gpw-base-v1")
        self.assertEqual(effective["_autonomous_policy"]["status"], PROMOTION_FREEZE_STATUS)

        auth = self.state / "statistical_policy_authorizations.json"
        auth.write_text(json.dumps({
            "schema_version": "briefrooms-statistical-policy-authorizations-v1",
            "updated_at": None,
            "authorizations": {},
            "controls": {},
            "authorizations_sha256": "invalid-but-unused-while-frozen"
        }), encoding="utf-8")
        result = materialize(self.state / ap.REGISTRY_FILENAME, auth, self.root)
        self.assertFalse(result["changed"])
        self.assertEqual(result["status"], PROMOTION_FREEZE_STATUS)
        checked = json.loads((self.root / "data/investments/gpw_daily_pick_config.json").read_text())
        self.assertEqual(checked["minimum_composite_score"], 72)


if __name__ == "__main__":
    unittest.main()
