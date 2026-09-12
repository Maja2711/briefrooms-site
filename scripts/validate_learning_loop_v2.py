#!/usr/bin/env python3
from __future__ import annotations

import unittest
import learning_loop_v2 as ll


class LearningLoopV2Tests(unittest.TestCase):
    def test_decision_quality_is_ex_ante_only(self):
        result = ll.assess_decision_quality({"data_quality": 1.0, "risk_plan_quality": 0.8})
        self.assertEqual(result["status"], "ASSESSED")
        self.assertTrue(result["outcome_independent"])
        with self.assertRaises(ValueError):
            ll.assess_decision_quality({"realized_return": 0.2})

    def test_shadow_freeze_is_prospective_and_hashed(self):
        row = ll.freeze_shadow_candidate(
            candidate_id="gpw:2026-09-12:XYZ",
            decision_at="2026-09-12T08:00:00Z",
            status="REJECTED",
            evidence={"score_source": "frozen"},
            plan={"entry_zone": [10, 11], "stop": 9, "target": 13},
            score=78,
            threshold=80,
            near_miss_band=3,
        )
        self.assertTrue(row["near_miss"]["is_near_miss"])
        self.assertTrue(row["prospective_only"])
        unhashed = {key: value for key, value in row.items() if key != "state_sha256"}
        self.assertEqual(row["state_sha256"], ll.sha256_payload(unhashed))

    def test_mfe_mae_long(self):
        result = ll.compute_mfe_mae(100, [
            {"timestamp": "t1", "high": 103, "low": 98},
            {"timestamp": "t2", "high": 110, "low": 95},
        ])
        self.assertEqual(result["mfe_percent"], 10.0)
        self.assertEqual(result["mae_percent"], -5.0)

    def test_confidence_low_sample_never_promotes(self):
        result = ll.confidence_calibration([
            {"confidence": 80, "outcome": 1},
            {"confidence": 60, "outcome": 0},
        ], min_samples=30)
        self.assertEqual(result["status"], "INSUFFICIENT_SAMPLE")
        self.assertFalse(result["production_writeback_allowed"])
        self.assertIsNotNone(result["brier_score"])

    def test_model_ablation(self):
        result = ll.model_marginal_contribution({"a": 0.9, "b": 0.6, "c": 0.4}, outcome=1)
        self.assertEqual(result["status"], "ASSESSED")
        self.assertIn("brier_improvement_vs_without_model", result["models"]["a"])

    def test_evidence_delta_ignores_timestamps(self):
        result = ll.evidence_delta(
            {"score": 60, "generated_at": "x"},
            {"score": 70, "generated_at": "y", "new": True},
        )
        self.assertEqual(result["change_count"], 2)
        self.assertIn("score", result["changed"])
        self.assertNotIn("generated_at", result["changed"])

    def test_challenger_low_sample_is_no_change(self):
        result = ll.live_challenger_evaluation(
            input_hash_champion="abc", input_hash_challenger="abc",
            champion={"n": 10}, challenger={"n": 10},
        )
        self.assertEqual(result["decision"], "NO_CHANGE")
        self.assertFalse(result["production_writeback_allowed"])

    def test_challenger_can_only_be_candidate(self):
        result = ll.live_challenger_evaluation(
            input_hash_champion="abc", input_hash_challenger="abc",
            champion={"n": 40, "excess_return": 0.02, "max_drawdown": -0.10, "brier_score": 0.22},
            challenger={"n": 40, "excess_return": 0.04, "max_drawdown": -0.09, "brier_score": 0.18},
        )
        self.assertEqual(result["decision"], "PROMOTION_CANDIDATE")
        self.assertFalse(result["production_writeback_allowed"])
        self.assertTrue(result["requires_existing_governed_promotion_gate"])

    def test_drawdown_compares_severity_not_sign(self):
        result = ll.live_challenger_evaluation(
            input_hash_champion="abc", input_hash_challenger="abc",
            champion={"n": 40, "excess_return": 0.02, "max_drawdown": -0.10, "brier_score": 0.22},
            challenger={"n": 40, "excess_return": 0.04, "max_drawdown": -0.11, "brier_score": 0.18},
        )
        self.assertEqual(result["decision"], "NO_CHANGE")
        self.assertGreater(result["drawdown_worsening"], 0)


if __name__ == "__main__":
    unittest.main()
