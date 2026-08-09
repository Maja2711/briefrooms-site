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
            "strategy_tournament": {"rolling_closed_legs": 120},
            "contextual_learning": {
                "enabled": True,
                "scope": ["eurusd"],
                "tracked_contexts": ["against_aligned_up_momentum", "against_aligned_down_momentum"],
                "minimum_absolute_momentum_pct": 0.15,
                "minimum_observations_before_adjustment": 6,
                "prior_observations": 5,
                "performance_weight": 18.0,
                "maximum_adjustment": 10.0,
                "apply_to_methods": ["ema_mean_reversion"],
            },
        }

    def fresh_up(self):
        return {"signals": {"ret5_pct": 1.1, "ret20_pct": 0.8}}

    def test_short_against_aligned_up_momentum_is_classified(self):
        self.assertEqual(
            "against_aligned_up_momentum",
            v5.momentum_context("short", self.fresh_up(), self.policy()),
        )

    def test_single_loss_is_recorded_but_does_not_change_weight(self):
        leg = {
            "strategy_id": "ema_mean_reversion",
            "direction": "short",
            "net_result_percent": -0.325211,
            "entry_decision": {"fresh_v2_signal": self.fresh_up()},
        }
        candidates = {"ema_mean_reversion": {"direction": "short", "conviction": 6.0, "raw_score": -40.0}}
        with patch.object(v5.v4, "iter_legs", return_value=[leg]):
            adjusted, stats = v5.apply_contextual_learning("eurusd", candidates, self.fresh_up(), self.policy())
        row = adjusted["ema_mean_reversion"]
        self.assertEqual(1, row["contextual_learning_count"])
        self.assertAlmostEqual(-0.325211, row["contextual_learning_mean_net_percent"], places=6)
        self.assertEqual(0.0, row["contextual_learning_adjustment"])
        self.assertEqual(6.0, row["conviction"])
        self.assertEqual(1, stats["methods"]["ema_mean_reversion"]["count"])

    def test_six_context_losses_penalize_only_same_context(self):
        legs = [
            {
                "strategy_id": "ema_mean_reversion",
                "direction": "short",
                "net_result_percent": -0.30,
                "entry_decision": {"fresh_v2_signal": self.fresh_up()},
            }
            for _ in range(6)
        ]
        candidates = {"ema_mean_reversion": {"direction": "short", "conviction": 6.0, "raw_score": -40.0}}
        with patch.object(v5.v4, "iter_legs", return_value=legs):
            adjusted, _ = v5.apply_contextual_learning("eurusd", candidates, self.fresh_up(), self.policy())
        self.assertLess(adjusted["ema_mean_reversion"]["contextual_learning_adjustment"], 0.0)
        self.assertLess(adjusted["ema_mean_reversion"]["conviction"], 6.0)


if __name__ == "__main__":
    unittest.main()
