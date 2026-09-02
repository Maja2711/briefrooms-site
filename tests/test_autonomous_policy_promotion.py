from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts import autonomous_policy_promotion as ap
from scripts.policy_runtime_overlay import PROMOTION_FREEZE_STATUS, apply_active_policy, registry_hash


class AutonomousPolicyPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data/investments/gpw_daily_pick_history").mkdir(parents=True)
        (self.root / "data/investments/us_daily_stock_history").mkdir(parents=True)
        gpw = {
            "policy_version": "gpw-base-v1",
            "minimum_composite_score": 72,
        }
        us = {
            "policy_version": "us-base-v1",
            "target_score": 72,
        }
        (self.root / "data/investments/gpw_daily_pick_config.json").write_text(json.dumps(gpw), encoding="utf-8")
        (self.root / "data/investments/us_daily_stock_config.json").write_text(json.dumps(us), encoding="utf-8")
        self.state = self.root / "policy-state"
        self.state.mkdir()
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        ap.ensure_activation(self.state, self.now)
        self.registry = ap.ensure_registry(self.state, self.root, self.now)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _shadow_row(self, idx: int, when: datetime, *, engine: str = "gpw_daily", score: float = 71.5,
                    ret: float = 0.5, r: float = 0.4) -> dict:
        row = {
            "schema_version": ap.SHADOW_SCHEMA,
            "shadow_outcome_id": f"shadow-{engine}-{idx}-{when.timestamp()}",
            "snapshot_id": f"snap-{engine}-{idx}-{when.timestamp()}",
            "candidate_id": f"cand-{engine}-{idx}-{when.timestamp()}",
            "engine_id": engine,
            "decision_at": ap._iso(when),
            "settled_at": ap._iso(when + timedelta(days=3)),
            "symbol": f"SYM{idx}",
            "candidate_score": score,
            "first_blocking_gate": "minimum_composite_score",
            "source_threshold": 72.0,
            "other_hard_gates_passed": True,
            "entry": 100.0,
            "exit_price": 100.5,
            "exit_reason": "two_session_horizon",
            "exit_day": (when.date() + timedelta(days=3)).isoformat(),
            "return_percent": ret,
            "r_multiple": r,
            "conservative_same_bar": False,
            "settlement_rule": "test",
        }
        row["row_sha256"] = ap._shadow_hash(row)
        return row

    def _append_shadow(self, row: dict) -> None:
        ap._append_line(self.state / ap.SHADOW_FILENAME, row)

    def test_settlement_is_conservative_when_stop_and_target_touch_same_bar(self) -> None:
        bars = [
            ap.Bar(date(2026, 9, 2), 100, 104, 96, 102),
            ap.Bar(date(2026, 9, 3), 102, 103, 101, 102),
        ]
        result = ap.settle_long_two_full_sessions(100.0, 97.0, 103.0, bars, date(2026, 9, 1))
        self.assertIsNotNone(result)
        self.assertEqual(result["exit_reason"], "stop")
        self.assertTrue(result["conservative_same_bar"])
        self.assertAlmostEqual(result["r_multiple"], -1.0)

    def test_settlement_uses_second_full_session_close_when_no_barrier_hits(self) -> None:
        bars = [
            ap.Bar(date(2026, 9, 2), 100, 101, 99, 100.5),
            ap.Bar(date(2026, 9, 3), 100.5, 102, 100, 101.0),
        ]
        result = ap.settle_long_two_full_sessions(100.0, 95.0, 110.0, bars, date(2026, 9, 1))
        self.assertEqual(result["exit_reason"], "two_session_horizon")
        self.assertAlmostEqual(result["return_percent"], 1.0)
        self.assertAlmostEqual(result["r_multiple"], 0.2)

    def test_candidate_training_and_validation_are_separate_then_auto_promote(self) -> None:
        for i in range(ap.TRAIN_MIN_N):
            self._append_shadow(self._shadow_row(i, self.now - timedelta(days=40 - i)))
        created = ap.create_candidates(self.registry, self.state, self.root, self.now)
        self.assertEqual(created, 1)
        candidate = next(iter(self.registry["candidates"].values()))
        self.assertEqual(candidate["from_value"], 72.0)
        self.assertEqual(candidate["to_value"], 71.0)
        self.assertEqual(candidate["status"], "SHADOW_VALIDATION")

        promoted, rejected = ap.update_candidates(self.registry, self.state, self.now)
        self.assertEqual((promoted, rejected), (0, 0))
        self.assertEqual(candidate["validation"]["n"], 0)

        for i in range(ap.VALIDATION_MIN_N):
            when = self.now + timedelta(days=i + 1)
            self._append_shadow(self._shadow_row(100 + i, when, ret=0.6, r=0.45))
        later = self.now + timedelta(days=ap.VALIDATION_MIN_N + 2)
        promoted, rejected = ap.update_candidates(self.registry, self.state, later)
        self.assertEqual((promoted, rejected), (1, 0))
        active = self.registry["engines"]["gpw_daily"]
        self.assertEqual(active["revision"], 1)
        self.assertEqual(active["overrides"]["minimum_composite_score"], 71.0)
        self.assertEqual(candidate["promotion_gate"]["status"], "PASS")
        self.assertEqual(candidate["status"], "PROMOTED")

    def test_runtime_overlay_is_frozen_without_explicit_governance_unfreeze(self) -> None:
        engine = self.registry["engines"]["gpw_daily"]
        engine.update({
            "revision": 1,
            "policy_id": "policy-1",
            "effective_policy_version": "gpw-base-v1+auto1",
            "overrides": {"minimum_composite_score": 71.0},
            "activated_at": ap._iso(self.now),
            "source_candidate_id": "candidate-1",
        })
        self.registry["updated_at"] = ap._iso(self.now)
        ap._atomic_json(self.state / ap.REGISTRY_FILENAME, self.registry)
        baseline = {"policy_version": "gpw-base-v1", "minimum_composite_score": 72, "minimum_reward_risk": 1.5}
        frozen = apply_active_policy("gpw_daily", baseline, registry_path=self.state / ap.REGISTRY_FILENAME)
        self.assertEqual(frozen["minimum_composite_score"], 72)
        self.assertEqual(frozen["policy_version"], "gpw-base-v1")
        self.assertEqual(frozen["_autonomous_policy"]["status"], PROMOTION_FREEZE_STATUS)

        raw = json.loads((self.state / ap.REGISTRY_FILENAME).read_text())
        raw["governance"] = {"production_promotion_enabled": True}
        raw.pop("registry_sha256", None)
        raw["registry_sha256"] = registry_hash(raw)
        (self.state / "explicitly-unfrozen.json").write_text(json.dumps(raw), encoding="utf-8")
        effective = apply_active_policy("gpw_daily", baseline, registry_path=self.state / "explicitly-unfrozen.json")
        self.assertEqual(effective["minimum_composite_score"], 71)
        self.assertEqual(effective["minimum_reward_risk"], 1.5)
        self.assertEqual(effective["policy_version"], "gpw-base-v1+auto1")

        tampered = json.loads((self.state / "explicitly-unfrozen.json").read_text())
        tampered["engines"]["gpw_daily"]["overrides"]["minimum_reward_risk"] = 0.1
        (self.state / "tampered.json").write_text(json.dumps(tampered), encoding="utf-8")
        safe = apply_active_policy("gpw_daily", baseline, registry_path=self.state / "tampered.json")
        self.assertEqual(safe["minimum_composite_score"], 72)
        self.assertEqual(safe["policy_version"], "gpw-base-v1")

    def test_bad_live_auto_policy_rolls_back_to_parent(self) -> None:
        candidate = {
            "candidate_id": "pc1", "engine_id": "gpw_daily", "parameter": "minimum_composite_score",
            "gate": "minimum_composite_score", "from_value": 72.0, "to_value": 71.0,
            "created_at": ap._iso(self.now - timedelta(days=40)), "validation_start_at": ap._iso(self.now - timedelta(days=30)),
            "status": "SHADOW_VALIDATION", "training": {}, "validation": {}, "promotion_gate": {"status": "PASS"},
        }
        self.registry["candidates"]["pc1"] = candidate
        ap.promote_candidate(self.registry, candidate, self.state, self.now - timedelta(days=20))
        active_version = self.registry["engines"]["gpw_daily"]["effective_policy_version"]
        for i in range(ap.ROLLBACK_MIN_N):
            day = date(2026, 8, 15) + timedelta(days=i)
            payload = {
                "date": day.isoformat(),
                "generated_at": ap._iso(self.now - timedelta(days=15 - i)),
                "policy_version": active_version,
                "outcome": {"status": "RESOLVED", "return_percent": -0.6, "r_multiple": -0.5},
            }
            (self.root / "data/investments/gpw_daily_pick_history" / f"{day.isoformat()}.json").write_text(json.dumps(payload), encoding="utf-8")
        count = ap.monitor_rollbacks(self.registry, self.state, self.root, self.now)
        self.assertEqual(count, 1)
        restored = self.registry["engines"]["gpw_daily"]
        self.assertEqual(restored["revision"], 0)
        self.assertEqual(restored["overrides"], {})
        self.assertEqual(restored["effective_policy_version"], "gpw-base-v1")
        self.assertEqual(candidate["status"], "ROLLED_BACK")

    def test_bootstrap_run_without_learning_history_is_safe(self) -> None:
        fresh_state = self.root / "fresh-policy"
        learning = self.root / "learning"
        learning.mkdir()
        status = ap.run(fresh_state, learning, self.root, allow_network=False, now=self.now)
        self.assertEqual(status["candidates_created"], 0)
        self.assertEqual(status["candidates_promoted"], 0)
        self.assertEqual(status["automatic_rollbacks"], 0)
        verified = ap.verify_state(fresh_state)
        self.assertEqual(set(verified["engines"]), {"gpw_daily", "us_daily"})


if __name__ == "__main__":
    unittest.main()
