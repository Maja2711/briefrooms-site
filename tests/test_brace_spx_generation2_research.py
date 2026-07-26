from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_spx_generation2_research as gen2


class Generation2Tests(unittest.TestCase):
    def test_candidate_universe_is_predeclared_and_unique(self):
        pool = gen2.candidate_pool()
        self.assertEqual(len(pool), 288)
        self.assertEqual(len({candidate.candidate_id() for candidate in pool}), 288)
        self.assertTrue(all(candidate.params.get("generation") == "spx-sealed-v2" for candidate in pool))

    def test_generation_paths_are_separate_from_v1(self):
        gen2.configure_engine()
        self.assertEqual(gen2.engine.GENERATION_ID, "spx-sealed-v2")
        self.assertIn("generation2", gen2.engine.OUTPUT_PATH.name)
        self.assertIn("generation2", gen2.engine.LEDGER_PATH.name)
        self.assertIn("generation2", gen2.engine.MANIFEST_PATH.name)

    def test_new_feature_sets_are_nonempty(self):
        import pandas as pd
        columns = {
            "spy_ma_gap_50": [0.0], "spy_ma_gap_100": [0.0], "spy_ma_gap_200": [0.0],
            "spy_momentum_63": [0.0], "spy_momentum_126": [0.0], "spy_momentum_252": [0.0],
            "spy_vol_20": [0.1], "spy_vol_60": [0.1], "spy_drawdown_126": [0.0], "spy_drawdown_252": [0.0],
            "breadth_above_ma50": [0.5], "breadth_above_ma200": [0.5], "sector_momentum_mean_63": [0.0],
            "sector_momentum_dispersion_63": [0.0], "credit_ratio_63": [0.0], "equal_weight_relative_63": [0.0],
            "vix_level": [20.0], "vix_change_21": [0.0], "tnx_level": [4.0], "tnx_change_63": [0.0],
            "tlt_momentum_63": [0.0], "dollar_momentum_63": [0.0],
        }
        frame = pd.DataFrame(columns)
        for feature_set in ("trend_compact", "breadth_credit", "cross_asset", "balanced_compact"):
            self.assertGreater(len(gen2.feature_columns(frame, feature_set)), 0)


if __name__ == "__main__":
    unittest.main()
