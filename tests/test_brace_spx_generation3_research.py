from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_spx_generation2_research as gen2
import brace_spx_generation3_research as gen3


class Generation3Tests(unittest.TestCase):
    def test_candidate_universe_is_focused_predeclared_and_unique(self):
        pool = gen3.candidate_pool()
        self.assertEqual(len(pool), 48)
        self.assertEqual(len({candidate.candidate_id() for candidate in pool}), 48)
        self.assertTrue(all(candidate.params.get("generation") == "spx-focused-v3" for candidate in pool))
        self.assertEqual({candidate.family for candidate in pool}, {"elastic_logistic_focus"})
        self.assertEqual(
            {candidate.feature_set for candidate in pool},
            {"breadth_credit_focus", "cross_asset_focus"},
        )

    def test_generation3_does_not_repeat_generation2_candidate_ids(self):
        gen2_ids = {candidate.candidate_id() for candidate in gen2.candidate_pool()}
        gen3_ids = {candidate.candidate_id() for candidate in gen3.candidate_pool()}
        self.assertTrue(gen2_ids.isdisjoint(gen3_ids))

    def test_generation_paths_are_separate_and_holdout_engine_is_reused(self):
        gen3.configure_engine()
        self.assertEqual(gen3.engine.GENERATION_ID, "spx-focused-v3")
        self.assertIn("generation3", gen3.engine.OUTPUT_PATH.name)
        self.assertIn("generation3", gen3.engine.LEDGER_PATH.name)
        self.assertIn("generation3", gen3.engine.MANIFEST_PATH.name)

    def test_focused_feature_sets_are_nonempty(self):
        frame = pd.DataFrame({
            "breadth_above_ma50": [0.5],
            "breadth_above_ma200": [0.5],
            "sector_momentum_mean_63": [0.0],
            "credit_ratio_63": [0.0],
            "equal_weight_relative_63": [0.0],
            "vix_change_21": [0.0],
            "vix_level": [20.0],
            "tnx_change_63": [0.0],
            "tlt_momentum_63": [0.0],
            "dollar_momentum_63": [0.0],
            "spy_momentum_126": [0.0],
            "spy_vol_20": [0.1],
        })
        for feature_set in ("breadth_credit_focus", "cross_asset_focus"):
            columns = gen3.feature_columns(frame, feature_set)
            self.assertGreater(len(columns), 0)
            self.assertEqual(len(columns), len(set(columns)))

    def test_no_leverage_is_possible(self):
        candidate = gen3.candidate_pool()[0]
        probabilities = pd.Series([0.0, 0.6, 1.0])
        volatility = pd.Series([0.1, 0.1, 0.1])
        exposure = gen3.probabilities_to_exposure(probabilities, volatility, candidate)
        self.assertGreaterEqual(float(exposure.min()), 0.0)
        self.assertLessEqual(float(exposure.max()), 1.0)

    def test_development_baselines_use_fold_positions_not_fake_dates(self):
        index = pd.date_range("2020-01-31", periods=8, freq="ME")
        frame = pd.DataFrame({
            "asset_return": [0.01, -0.01, 0.02, 0.01, -0.02, 0.03, 0.01, 0.02],
            "spy_ma_gap_200": [0.1, 0.1, -0.1, 0.1, -0.1, 0.1, 0.1, 0.1],
        }, index=index)
        folds = [
            (np.array([0, 1]), np.array([2, 3])),
            (np.array([0, 1, 2, 3]), np.array([4, 5])),
            (np.array([0, 1, 2, 3, 4, 5]), np.array([6, 7])),
        ]
        with patch.object(gen3.base, "monthly_dataset", return_value=frame), \
             patch.object(gen3.base, "holdout_split", return_value=(frame, frame.iloc[0:0])), \
             patch.object(gen3.base, "chronological_folds", return_value=folds), \
             patch.object(gen3.engine, "monthly_risk_free", return_value=pd.Series(0.0, index=index[2:])):
            baselines = gen3.development_baselines(pd.DataFrame())
        self.assertIn("buy_and_hold", baselines)
        self.assertIn("trend_200d", baselines)
        self.assertGreater(baselines["buy_and_hold"]["months"], 0)


if __name__ == "__main__":
    unittest.main()
