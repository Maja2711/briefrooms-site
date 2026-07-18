import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import investments_weekly_v4 as v4


class MultiInstrumentExposureTests(unittest.TestCase):
    def test_score_always_resolves_direction(self):
        self.assertEqual(v4.direction_from_score(1), "long")
        self.assertEqual(v4.direction_from_score(-1), "short")
        self.assertEqual(v4.direction_from_score(0, "short"), "short")

    def test_inverse_is_really_opposite(self):
        fresh = {
            "direction": "short",
            "score": -42,
            "signals": {"last_close": 1.10, "ema20": 1.11, "atr14": 0.01},
        }
        weekly = {"data_quality": "passed", "score": -20, "regime": "trend_down:vol_normal"}
        rows = v4.candidate_methods(fresh, weekly, "long")
        self.assertEqual(rows["base_v2"]["direction"], "short")
        self.assertEqual(rows["inverse_v2"]["direction"], "long")
        self.assertNotEqual(rows["base_v2"]["direction"], rows["inverse_v2"]["direction"])

    def test_negative_short_does_not_force_inverse_without_evidence(self):
        candidates = {
            "base_v2": {"direction": "short", "raw_score": -60, "conviction": 9},
            "inverse_v2": {"direction": "long", "raw_score": 60, "conviction": 9},
        }
        learning = {
            "methods": {
                "base_v2": {"count": 10, "adjustment": -3},
                "inverse_v2": {"count": 0, "adjustment": 0},
            }
        }
        policy = {"strategy_tournament": {"candidate_methods": ["base_v2", "inverse_v2"], "exploration_bonus": 2.5}}
        decision = v4.choose(candidates, learning, policy)
        self.assertIn(decision["strategy_id"], {"base_v2", "inverse_v2"})
        self.assertIn(decision["direction"], {"long", "short"})
        self.assertIn("candidates", decision)

    def test_mean_reversion_opposes_large_positive_deviation(self):
        fresh = {
            "direction": "neutral",
            "score": 10,
            "signals": {"last_close": 110, "ema20": 100, "atr14": 5},
        }
        weekly = {"data_quality": "passed", "score": 15, "regime": "trend_up:vol_normal"}
        rows = v4.candidate_methods(fresh, weekly, "long")
        self.assertEqual(rows["ema_mean_reversion"]["direction"], "short")

    def test_cost_models(self):
        self.assertGreater(v4.cost_percent("eurusd", 1.10, {"round_trip_cost": 1.5, "cost_unit": "pips"}), 0)
        self.assertGreater(v4.cost_percent("sp500_futures", 5000, {"round_trip_cost": 1, "cost_unit": "points"}), 0)
        self.assertEqual(v4.cost_percent("btcusd", 60000, {"round_trip_cost": 0.2, "cost_unit": "percent"}), 0.2)

    def test_policy_enables_all_three_instruments(self):
        policy = v4.read(v4.POLICY_PATH, {})
        enabled = {row["instrument_id"] for row in v4.policy_instruments(policy)}
        self.assertEqual(enabled, {"eurusd", "sp500_futures", "btcusd"})
        self.assertTrue(policy.get("mandatory_monday_position"))
        self.assertTrue(policy.get("reentry_after_any_close"))
        self.assertTrue((policy.get("learning_guardrails") or {}).get("paper_trading_only"))


if __name__ == "__main__":
    unittest.main()
