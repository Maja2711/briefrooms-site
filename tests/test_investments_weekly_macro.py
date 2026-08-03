import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import investments_weekly_macro as macro


class WeeklyMacroContextTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "score_cap": 30.0,
            "candidate_alignment_bonus_max": 6.0,
            "weights": {"oil_1w": 10.0, "oil_4w": 5.0, "us10y_1w": 10.0, "us10y_4w": 5.0},
            "normalization": {"oil_1w_percent": 8.0, "oil_4w_percent": 20.0, "us10y_1w_bps": 20.0, "us10y_4w_bps": 50.0},
            "blend_weights": {"daily": 0.35, "weekly": 0.40, "macro": 0.25},
        }

    def test_falling_oil_and_yields_support_eurusd(self):
        result = macro.score_from_observations(-8.0, -20.0, -20.0, -50.0, self.cfg)
        self.assertGreater(result["score"], 0)
        self.assertLessEqual(result["score"], 30.0)

    def test_rising_oil_and_yields_support_dollar(self):
        result = macro.score_from_observations(8.0, 20.0, 20.0, 50.0, self.cfg)
        self.assertLess(result["score"], 0)
        self.assertGreaterEqual(result["score"], -30.0)

    def test_macro_alignment_rewards_matching_candidate(self):
        policy = {
            "macro_context": self.cfg,
            "instruments": [{"instrument_id": "eurusd", "default_tie_direction": "long"}],
        }
        candidates = {
            "long_case": {"direction": "long", "raw_score": 30.0, "conviction": 4.5},
            "short_case": {"direction": "short", "raw_score": -30.0, "conviction": 4.5},
        }
        context = {"data_quality": "passed", "score": 20.0, "score_cap": 30.0}
        rows = macro.apply_to_candidates(
            "eurusd",
            candidates,
            {"score": 20.0},
            {"data_quality": "passed", "score": 10.0},
            context,
            policy,
        )
        self.assertGreater(rows["long_case"]["conviction"], 4.5)
        self.assertLess(rows["short_case"]["conviction"], 4.5)
        self.assertIn("macro_weekly_blend", rows)

    def test_context_is_not_applied_to_other_instruments(self):
        candidates = {"base": {"direction": "long", "raw_score": 20.0, "conviction": 3.0}}
        rows = macro.apply_to_candidates(
            "btcusd",
            candidates,
            {"score": 20.0},
            {"data_quality": "passed", "score": 10.0},
            {"data_quality": "passed", "score": 30.0, "score_cap": 30.0},
            {"macro_context": self.cfg},
        )
        self.assertEqual(rows["base"]["conviction"], 3.0)
        self.assertNotIn("macro_weekly_blend", rows)


if __name__ == "__main__":
    unittest.main()
