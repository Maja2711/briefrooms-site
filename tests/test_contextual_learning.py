import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import investments_weekly_v5 as v5


class ContextualLearningTests(unittest.TestCase):
    def policy(self):
        return {
            "strategy_tournament": {
                "rolling_closed_legs": 120,
                "candidate_methods": ["base_v2", "inverse_v2", "weekly_trend", "daily_weekly_blend", "ema_mean_reversion", "macro_weekly_blend"],
            },
            "contextual_learning": {
                "enabled": True,
                "scope": ["eurusd", "sp500_futures", "btcusd"],
                "minimum_absolute_momentum_pct": 0.15,
                "minimum_observations_before_adjustment": 6,
                "prior_observations": 5,
                "performance_weight": 6.0,
                "maximum_adjustment": 2.0,
                "apply_to_methods": ["base_v2", "inverse_v2", "weekly_trend", "daily_weekly_blend", "ema_mean_reversion", "macro_weekly_blend"],
            },
        }

    def fresh_up(self):
        return {"data_quality": "passed", "signals": {"ret5_pct": 1.1, "ret20_pct": 0.8}}

    def fresh_down(self):
        return {"data_quality": "passed", "signals": {"ret5_pct": -1.1, "ret20_pct": -0.8}}

    def weekly(self, regime="trend_up:vol_normal"):
        return {"data_quality": "passed", "regime": regime}

    def macro(self, direction="short", ma_direction="long"):
        return {
            "data_quality": "passed",
            "direction": direction,
            "ma_structure": {"data_quality": "passed", "direction": ma_direction},
        }

    def leg(self, method_id, direction, net, fresh=None, regime="trend_up:vol_normal", macro_direction="short", ma_direction="long"):
        fresh = fresh or self.fresh_up()
        return {
            "strategy_id": method_id,
            "direction": direction,
            "net_result_percent": net,
            "entry_decision": {
                "direction": direction,
                "fresh_v2_signal": fresh,
                "weekly_features": self.weekly(regime),
                "macro_context": self.macro(macro_direction, ma_direction),
                "candidates": {method_id: {"direction": direction}},
            },
            "candidate_outcomes": {
                method_id: {"direction": direction, "net_result_percent": net}
            },
        }

    def test_short_against_aligned_up_momentum_is_classified(self):
        self.assertEqual(
            "against_aligned_up_momentum",
            v5.momentum_context("short", self.fresh_up(), self.policy()),
        )

    def test_single_observation_is_recorded_but_does_not_change_weight(self):
        leg = self.leg("ema_mean_reversion", "short", -0.325211)
        candidates = {"ema_mean_reversion": {"direction": "short", "conviction": 6.0, "raw_score": -40.0}}
        with patch.object(v5.v4, "iter_legs", return_value=[leg]):
            adjusted, stats = v5.apply_contextual_learning(
                "eurusd", candidates, self.fresh_up(), self.policy(),
                weekly=self.weekly(), macro_context=self.macro(),
            )
        row = adjusted["ema_mean_reversion"]
        self.assertEqual(1, row["contextual_learning_count"])
        self.assertAlmostEqual(-0.325211, row["contextual_learning_mean_net_percent"], places=6)
        self.assertEqual(0.0, row["contextual_learning_adjustment"])
        self.assertEqual(6.0, row["conviction"])
        self.assertEqual(1, stats["methods"]["ema_mean_reversion"]["candidate_observation_count"])

    def test_six_same_context_losses_penalize_candidate(self):
        legs = [self.leg("ema_mean_reversion", "short", -0.30) for _ in range(6)]
        candidates = {"ema_mean_reversion": {"direction": "short", "conviction": 6.0, "raw_score": -40.0}}
        with patch.object(v5.v4, "iter_legs", return_value=legs):
            adjusted, _ = v5.apply_contextual_learning(
                "eurusd", candidates, self.fresh_up(), self.policy(),
                weekly=self.weekly(), macro_context=self.macro(),
            )
        self.assertLess(adjusted["ema_mean_reversion"]["contextual_learning_adjustment"], 0.0)
        self.assertLess(adjusted["ema_mean_reversion"]["conviction"], 6.0)

    def test_six_same_context_wins_reward_candidate(self):
        legs = [self.leg("ema_mean_reversion", "short", 0.30) for _ in range(6)]
        candidates = {"ema_mean_reversion": {"direction": "short", "conviction": 6.0, "raw_score": -40.0}}
        with patch.object(v5.v4, "iter_legs", return_value=legs):
            adjusted, _ = v5.apply_contextual_learning(
                "eurusd", candidates, self.fresh_up(), self.policy(),
                weekly=self.weekly(), macro_context=self.macro(),
            )
        self.assertGreater(adjusted["ema_mean_reversion"]["contextual_learning_adjustment"], 0.0)
        self.assertGreater(adjusted["ema_mean_reversion"]["conviction"], 6.0)

    def test_contextual_adjustment_is_hard_capped(self):
        legs = [self.leg("ema_mean_reversion", "short", 25.0) for _ in range(6)]
        candidates = {"ema_mean_reversion": {"direction": "short", "conviction": 6.0, "raw_score": -40.0}}
        with patch.object(v5.v4, "iter_legs", return_value=legs):
            adjusted, _ = v5.apply_contextual_learning(
                "eurusd", candidates, self.fresh_up(), self.policy(),
                weekly=self.weekly(), macro_context=self.macro(),
            )
        self.assertEqual(2.0, adjusted["ema_mean_reversion"]["contextual_learning_adjustment"])
        self.assertEqual(8.0, adjusted["ema_mean_reversion"]["conviction"])

    def test_different_regime_does_not_leak_into_current_context(self):
        legs = [self.leg("ema_mean_reversion", "short", -2.0, regime="trend_down:vol_high") for _ in range(10)]
        candidates = {"ema_mean_reversion": {"direction": "short", "conviction": 6.0, "raw_score": -40.0}}
        with patch.object(v5.v4, "iter_legs", return_value=legs):
            adjusted, _ = v5.apply_contextual_learning(
                "eurusd", candidates, self.fresh_up(), self.policy(),
                weekly=self.weekly("trend_up:vol_normal"), macro_context=self.macro(),
            )
        self.assertEqual(0, adjusted["ema_mean_reversion"]["contextual_learning_count"])
        self.assertEqual(0.0, adjusted["ema_mean_reversion"]["contextual_learning_adjustment"])

    def test_counterfactual_candidate_is_learned_even_when_other_strategy_won(self):
        legs = []
        for _ in range(6):
            legs.append({
                "strategy_id": "base_v2",
                "direction": "long",
                "entry_price": 100.0,
                "exit_price": 90.0,
                "estimated_round_trip_cost_percent": 0.0,
                "net_result_percent": -10.0,
                "entry_decision": {
                    "direction": "long",
                    "fresh_v2_signal": self.fresh_up(),
                    "weekly_features": self.weekly(),
                    "macro_context": self.macro(),
                    "candidates": {
                        "base_v2": {"direction": "long"},
                        "ema_mean_reversion": {"direction": "short"},
                    },
                },
            })
        candidates = {"ema_mean_reversion": {"direction": "short", "conviction": 6.0, "raw_score": -40.0}}
        with patch.object(v5.v4, "iter_legs", return_value=legs):
            adjusted, stats = v5.apply_contextual_learning(
                "eurusd", candidates, self.fresh_up(), self.policy(),
                weekly=self.weekly(), macro_context=self.macro(),
            )
        self.assertEqual(6, stats["methods"]["ema_mean_reversion"]["candidate_observation_count"])
        self.assertGreater(adjusted["ema_mean_reversion"]["contextual_learning_mean_net_percent"], 0.0)
        self.assertGreater(adjusted["ema_mean_reversion"]["contextual_learning_adjustment"], 0.0)

    def test_counterfactual_observations_reduce_exploration_count_only(self):
        learning = {
            "instrument_id": "eurusd",
            "regime": "trend_up:vol_normal",
            "methods": {"ema_mean_reversion": {"count": 1, "adjustment": -0.2}},
        }
        contextual = {
            "methods": {"ema_mean_reversion": {"candidate_observation_count": 9}}
        }
        merged = v5.learning_with_candidate_observations(learning, contextual)
        row = merged["methods"]["ema_mean_reversion"]
        self.assertEqual(9, row["count"])
        self.assertEqual(1, row["selected_leg_count"])
        self.assertEqual(-0.2, row["adjustment"])
        self.assertEqual("counterfactual_candidate_observations_for_exploration_only", row["count_source"])

    def test_archived_leg_gets_auditable_outcomes_for_all_entry_candidates(self):
        item = {
            "continuous_last_closed_leg_id": "abc",
            "position_legs": [{
                "leg_id": "abc",
                "entry_price": 100.0,
                "exit_price": 110.0,
                "estimated_round_trip_cost_percent": 0.2,
                "entry_decision": {
                    "candidates": {
                        "base_v2": {"direction": "long"},
                        "inverse_v2": {"direction": "short"},
                    }
                },
            }],
        }
        v5._enrich_latest_archived_leg(item)
        outcomes = item["position_legs"][0]["candidate_outcomes"]
        self.assertAlmostEqual(9.8, outcomes["base_v2"]["net_result_percent"], places=6)
        self.assertAlmostEqual(-10.2, outcomes["inverse_v2"]["net_result_percent"], places=6)
        self.assertEqual("2.0", item["position_legs"][0]["contextual_learning_schema"])


if __name__ == "__main__":
    unittest.main()
