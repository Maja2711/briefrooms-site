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

import brace_spx_generation5_evaluation_v2 as evaluation
import brace_spx_generation5_research as gen5


class Generation5EvaluationV2Tests(unittest.TestCase):
    def test_protocol_is_same_generation_and_same_candidate_universe(self):
        pool = gen5.candidate_pool()
        self.assertEqual(evaluation.GENERATION_ID, "spx-state-geometry-v5")
        self.assertEqual(len(pool), 12)
        self.assertEqual(len({candidate.candidate_id() for candidate in pool}), 12)
        self.assertEqual(evaluation.candidate_signature(), gen5.engine.generation_signature(pool))
        self.assertIn("evaluation_v2", evaluation.REPORT_PATH.name)
        self.assertIn("evaluation_v2", evaluation.LEDGER_PATH.name)

    def test_defensive_sleeve_earns_risk_free_return(self):
        index = pd.date_range("2020-01-31", periods=4, freq="ME")
        asset = pd.Series([0.10, -0.05, 0.04, 0.02], index=index)
        target = pd.Series([0.0, 0.0, 0.5, 0.5], index=index)
        risk_free = pd.Series([0.01, 0.01, 0.01, 0.01], index=index)
        returns, turnover, applied = evaluation.defensive_portfolio_returns(
            asset,
            target,
            risk_free,
            cost=0.0,
        )
        self.assertAlmostEqual(float(returns.iloc[0]), 0.01, places=12)
        self.assertAlmostEqual(float(returns.iloc[1]), 0.01, places=12)
        self.assertAlmostEqual(float(returns.iloc[2]), 0.01, places=12)
        self.assertAlmostEqual(float(returns.iloc[3]), 0.5 * 0.02 + 0.5 * 0.01, places=12)
        self.assertAlmostEqual(float(applied.iloc[3]), 0.5, places=12)
        self.assertAlmostEqual(float(turnover.iloc[3]), 0.5, places=12)

    def test_fold_uses_observable_warm_state_instead_of_cold_reset(self):
        index = pd.date_range("2010-01-31", periods=6, freq="ME")
        development = pd.DataFrame(
            {
                "asset_return": [0.01] * 6,
                "realized_vol_20": [0.10] * 6,
            },
            index=index,
        )
        risk_free = pd.Series(0.001, index=index)
        candidate = gen5.candidate_pool()[0]
        folds = [(np.array([0, 1, 2]), np.array([4, 5]))]

        def full_signal(context: pd.DataFrame) -> pd.Series:
            return pd.Series(1.0, index=context.index)

        def full_exposure(probability, realized_vol, _candidate):
            return pd.Series(1.0, index=probability.index)

        with patch.object(evaluation.base, "chronological_folds", return_value=folds), \
             patch.object(evaluation.gen5, "shared_probability", side_effect=full_signal), \
             patch.object(evaluation.gen5, "probabilities_to_exposure", side_effect=full_exposure):
            _returns, _turnover, exposure, fold_metrics = evaluation._candidate_context_paths(
                development,
                candidate,
                risk_free,
            )

        self.assertEqual(list(exposure.index), list(index[4:6]))
        self.assertAlmostEqual(float(exposure.iloc[0]), 1.0, places=12)
        self.assertAlmostEqual(float(fold_metrics[0]["first_applied_exposure"]), 1.0, places=12)

    def test_bootstrap_is_deterministic_and_paired(self):
        index = pd.date_range("2010-01-31", periods=36, freq="ME")
        selected = pd.Series(0.012, index=index)
        turnover = pd.Series(0.0, index=index)
        baseline = pd.Series(0.008, index=index)
        risk_free = pd.Series(0.001, index=index)
        first = evaluation.bootstrap_metrics(
            selected,
            turnover,
            baseline,
            risk_free,
            draws=50,
            block_months=6,
            seed=17,
        )
        second = evaluation.bootstrap_metrics(
            selected,
            turnover,
            baseline,
            risk_free,
            draws=50,
            block_months=6,
            seed=17,
        )
        self.assertEqual(first, second)
        self.assertTrue(first["available"])
        self.assertEqual(first["probability_cagr_advantage_positive"], 1.0)

    def test_holdout_boundaries_are_not_redefined_by_protocol(self):
        self.assertEqual(gen5.FIXED_HOLDOUT_START, pd.Timestamp("2022-08-31"))
        self.assertEqual(gen5.FIXED_HOLDOUT_END, pd.Timestamp("2026-07-31"))
        self.assertNotIn("holdout", evaluation.evaluation_engine_signature().lower())


if __name__ == "__main__":
    unittest.main()
