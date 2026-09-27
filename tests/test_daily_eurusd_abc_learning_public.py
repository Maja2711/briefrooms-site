from __future__ import annotations

import unittest

from scripts.daily_eurusd_abc_learning_public import build_public_learning, validate


class DailyEURUSDABCLearningPublicTests(unittest.TestCase):
    def test_only_aggregate_learning_evidence_is_published(self):
        report = {
            "generated_at": "2026-09-03T09:00:00Z",
            "shared_contract": "briefrooms-learning-episode-v1",
            "authority": {"decision_influence": False, "automatic_policy_mutation": False},
            "sample": {"episodes": 12},
            "governance": {
                "single_trade_can_change_policy": False,
                "minimum_episodes_for_lesson": 8,
                "minimum_losses_for_error_lesson": 4,
                "minimum_dominant_error_recurrence": 0.6,
                "human_or_promotion_gate_required_before_policy_application": True,
                "automatic_challenger_creation": True,
                "max_active_challengers_per_arm": 1,
            },
            "challengers": [{
                "challenger_id": "abc-ch-test",
                "arm_id": "B",
                "created_at": "2026-09-03T09:00:00Z",
                "status": "SHADOW_CANDIDATE",
                "source_lesson": {
                    "error_pattern": "FOLLOW_THROUGH_FAILURE",
                    "confidence": 0.7,
                    "proposed_action": "test follow-through confirmation before entry; do not change policy automatically",
                },
                "frozen_baseline": {"episode_count": 12, "hit_rate": 0.55, "mean_r": 0.2},
                "shadow_contract": {
                    "automatic_creation": True,
                    "executable_rule_status": "AWAITING_EXPLICIT_RULE_COMPILATION",
                    "prospective_only": True,
                    "historical_backfill": False,
                    "decision_influence": False,
                    "production_execution": False,
                    "automatic_promotion": False,
                    "automatic_policy_mutation": False,
                },
                "prospective_evidence": {
                    "starts_after_episode_count": 12,
                    "challenger_observations": 0,
                    "status": "AWAITING_EXECUTABLE_SHADOW",
                },
            }],
            "arms": {
                arm: {
                    "episode_count": 4,
                    "wins": 2,
                    "losses": 2,
                    "hit_rate": 0.5,
                    "mean_r": 0.1,
                    "mean_mfe_r": 0.8,
                    "mean_mae_r": -0.5,
                    "dominant_error": "FOLLOW_THROUGH_FAILURE",
                    "error_recurrence_rate": 1.0,
                    "recent_vs_prior_mean_r_delta": None,
                    "policy_stability": 1.0,
                    "lesson_candidate": {
                        "eligible": False,
                        "error_pattern": None,
                        "confidence": None,
                        "proposed_action": None,
                        "policy_change_proposed": False,
                        "policy_change_applied": False,
                    },
                }
                for arm in ("A", "B", "C")
            },
        }
        payload = build_public_learning(report)
        validate(payload)
        self.assertEqual(payload["episode_count"], 12)
        self.assertEqual(set(payload["arms"]), {"A", "B", "C"})
        self.assertNotIn("episodes", payload)
        self.assertFalse(payload["automatic_policy_mutation"])
        self.assertFalse(payload["decision_influence"])
        self.assertTrue(payload["automatic_challenger_creation"])
        self.assertEqual(payload["challenger_count"], 1)
        self.assertEqual(payload["challengers"][0]["status"], "SHADOW_CANDIDATE")
        self.assertFalse(payload["challengers"][0]["shadow_contract"]["decision_influence"])


if __name__ == "__main__":
    unittest.main()
