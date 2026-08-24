from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts.gse_publish_learning_review_status import build_status

UTC = timezone.utc


class Tests(unittest.TestCase):
    def test_shadow_review_is_visible_and_transitioned(self):
        state = {
            "readiness": {"status": "shadow_learning", "reasons": ["prospective_paired_n_below_30"]},
            "historical": {"evaluable_walkforward_n": 282},
            "prospective": {"paired_n": 32, "delta_brier_v2_minus_v1": -0.000048, "delta_log_loss_v2_minus_v1": -0.00012, "calibration_bias_v2_regime": 0.3011},
            "controls": {"automatic_tuning_enabled": False},
        }
        proposal = {
            "status": "eligible_for_human_shadow_review",
            "candidate": {"similarity_temperature": 0.5, "prior_strength": 18},
            "holdout_delta_brier_candidate_minus_active": -0.001197,
            "automatically_applied": False,
            "active_policy_unchanged": True,
        }
        status = build_status(state, proposal, {"overall": state["prospective"]}, {}, now=datetime(2026, 8, 24, 20, 0, tzinfo=UTC))
        self.assertTrue(status["review_required"])
        self.assertEqual(status["status"], "human_shadow_review_required")
        self.assertEqual(status["transition"], "entered_human_review")
        self.assertFalse(status["policy_proposal"]["automatically_applied"])

    def test_promotion_review_has_priority(self):
        state = {
            "readiness": {"status": "eligible_for_human_promotion_review", "reasons": []},
            "historical": {},
            "prospective": {"paired_n": 50, "delta_brier_v2_minus_v1": -0.01, "delta_log_loss_v2_minus_v1": -0.02, "calibration_bias_v2_regime": 0.03},
            "controls": {},
        }
        proposal = {"status": "eligible_for_human_shadow_review", "automatically_applied": False, "active_policy_unchanged": True}
        status = build_status(state, proposal, {"overall": {}}, {})
        self.assertEqual(status["status"], "human_promotion_review_required")
        self.assertEqual(status["review_kind"], "promotion")

    def test_unchanged_review_does_not_reenter(self):
        state = {"readiness": {"status": "shadow_learning"}, "historical": {}, "prospective": {}, "controls": {}}
        proposal = {"status": "eligible_for_human_shadow_review", "automatically_applied": False, "active_policy_unchanged": True}
        previous = {"status": "human_shadow_review_required", "review_required": True}
        status = build_status(state, proposal, {"overall": {}}, previous)
        self.assertEqual(status["transition"], "unchanged")


if __name__ == "__main__":
    unittest.main()
