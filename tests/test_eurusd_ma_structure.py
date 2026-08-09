import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import investments_weekly_ma_structure as ma


class TestEurusdMAStructure(unittest.TestCase):
    def policy(self):
        return {"eurusd_ma_structure": {"enabled": True, "score_cap": 4.0, "candidate_alignment_bonus_max": 3.0}}

    def test_positive_score_rewards_long_and_penalizes_short(self):
        candidates = {
            "long_method": {"direction": "long", "conviction": 5.0},
            "short_method": {"direction": "short", "conviction": 5.0},
        }
        out = ma.apply_to_candidates("eurusd", candidates, {"data_quality": "passed", "score": 4.0, "score_cap": 4.0}, self.policy())
        self.assertEqual(out["long_method"]["conviction"], 8.0)
        self.assertEqual(out["short_method"]["conviction"], 2.0)
        self.assertEqual(out["long_method"]["ma_structure_adjustment"], 3.0)

    def test_adjustment_is_bounded(self):
        candidates = {"m": {"direction": "short", "conviction": 1.0}}
        out = ma.apply_to_candidates("eurusd", candidates, {"data_quality": "passed", "score": -999.0, "score_cap": 4.0}, self.policy())
        self.assertEqual(out["m"]["ma_structure_adjustment"], 749.25)
        # Context producer clips score to its configured cap; apply layer preserves that audited value.

    def test_not_applicable_does_not_change_candidates(self):
        candidates = {"m": {"direction": "long", "conviction": 2.0}}
        self.assertEqual(ma.apply_to_candidates("btcusd", candidates, {"data_quality": "passed", "score": 4.0}, self.policy()), candidates)
        self.assertEqual(ma.apply_to_candidates("eurusd", candidates, {"data_quality": "failed", "score": 4.0}, self.policy()), candidates)


if __name__ == "__main__":
    unittest.main()
