from __future__ import annotations

import unittest

from scripts import stock_trading_v2_challenger_eval as evaluator
from scripts import stock_trading_v2_policy_learner as learner


PROMOTION = {
    "production_promotion_enabled": False,
    "fixed_paired_n": 30,
    "confidence_level": 0.9,
    "bootstrap_samples": 500,
    "minimum_net_incremental_return_percent": 0.1,
    "minimum_net_positive_rate": 0.55,
    "minimum_unique_symbols": 5,
    "minimum_span_days": 10,
    "maximum_single_positive_contribution_share": 0.5,
}

CHAMPION = {
    "policy_version": "champion-test",
    "forced_trade_allowed": False,
    "markets": {"GPW": {"max_open_positions": 3}, "US": {"max_open_positions": 3}},
}

HYPOTHESIS = {
    "component": "non_positive_conservative_expected_value",
    "horizon_sessions": 20,
    "status": "ELIGIBLE_FOR_CHALLENGER_HOLDOUT",
    "evidence": {"observations": 40, "mean_incremental_r_vs_champion": 0.2},
    "suggested_experiment": {
        "type": "uncertainty_aware_ev_challenger",
        "change": "soft EV band",
        "safety_gates_unchanged": True,
    },
}

REPORT = {
    "generated_at": "2026-09-16T17:00:00Z",
    "report_sha256": "report-sha",
}


class StockTradingV2PolicyLearningTests(unittest.TestCase):
    def test_challenger_starts_future_only_and_never_auto_promotes(self):
        challenger = learner.make_challenger(
            HYPOTHESIS,
            regret_report=REPORT,
            champion_policy=CHAMPION,
            promotion_config=PROMOTION,
        )
        self.assertEqual(challenger["validation_start_at"], REPORT["generated_at"])
        self.assertEqual(challenger["state"], "COLLECTING_FRESH_HOLDOUT")
        self.assertFalse(challenger["governance"]["production_promotion_enabled"])
        self.assertTrue(challenger["holdout"]["historical_evidence_reuse_forbidden"])
        learner.validate_challenger(challenger)

    def test_holdout_does_not_peek_before_fixed_n(self):
        challenger = learner.make_challenger(
            HYPOTHESIS,
            regret_report=REPORT,
            champion_policy=CHAMPION,
            promotion_config=PROMOTION,
        )
        samples = [
            {
                "source_group": f"g{i}",
                "decision_at": f"2026-09-{17 + i // 2:02d}T10:00:00+00:00",
                "symbol": f"S{i % 6}",
                "incremental_net_return_percent": 1.0,
            }
            for i in range(10)
        ]
        result = evaluator.evaluate_samples(challenger, samples, generated_at="2026-09-25T00:00:00Z")
        self.assertEqual(result["state"], "COLLECTING_FRESH_HOLDOUT")
        self.assertEqual(result["metrics"]["paired_n"], 10)
        self.assertNotIn("formal_pass", result["metrics"])

    def test_strong_diversified_fixed_n_sample_can_research_pass(self):
        challenger = learner.make_challenger(
            HYPOTHESIS,
            regret_report=REPORT,
            champion_policy=CHAMPION,
            promotion_config=PROMOTION,
        )
        samples = []
        for i in range(30):
            day = 17 + i // 2
            samples.append(
                {
                    "source_group": f"g{i}",
                    "decision_at": f"2026-09-{day:02d}T10:00:00+00:00" if day <= 30 else f"2026-10-{day - 30:02d}T10:00:00+00:00",
                    "symbol": f"S{i % 10}",
                    "incremental_net_return_percent": 0.8 if i % 5 else 0.2,
                }
            )
        result = evaluator.evaluate_samples(challenger, samples, generated_at="2026-10-05T00:00:00Z")
        self.assertEqual(result["metrics"]["paired_n"], 30)
        self.assertTrue(result["metrics"]["formal_pass"])
        self.assertEqual(result["state"], "RESEARCH_PASS_AWAITING_MANUAL_ENABLEMENT")
        self.assertFalse(result["governance"]["production_promotion_enabled"])

    def test_concentrated_or_weak_sample_fails(self):
        challenger = learner.make_challenger(
            HYPOTHESIS,
            regret_report=REPORT,
            champion_policy=CHAMPION,
            promotion_config=PROMOTION,
        )
        samples = []
        for i in range(30):
            samples.append(
                {
                    "source_group": f"g{i}",
                    "decision_at": f"2026-10-{1 + i // 3:02d}T10:00:00+00:00",
                    "symbol": "ONE" if i < 25 else f"S{i}",
                    "incremental_net_return_percent": 8.0 if i == 0 else -0.1,
                }
            )
        result = evaluator.evaluate_samples(challenger, samples)
        self.assertFalse(result["metrics"]["formal_pass"])
        self.assertEqual(result["state"], "FORMAL_HOLDOUT_FAIL")


if __name__ == "__main__":
    unittest.main()
