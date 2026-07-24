import unittest

import numpy as np

from scripts.brace_spx_statistical_governance import (
    annualized_excess_sharpe,
    authorize_single_use_holdout,
    declaration_hash,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    reconcile_evidence,
)


class GovernanceTests(unittest.TestCase):
    def test_excess_sharpe_uses_risk_free_series(self):
        returns = [0.02, 0.01, -0.01, 0.03]
        no_rf = annualized_excess_sharpe(returns, 0.0)
        with_rf = annualized_excess_sharpe(returns, 0.005)
        self.assertGreater(no_rf, with_rf)

    def test_deflation_penalizes_more_trials(self):
        few = deflated_sharpe_ratio(1.2, 120, 5, 0.25)
        many = deflated_sharpe_ratio(1.2, 120, 500, 0.25)
        self.assertGreater(few, many)

    def test_pbo_is_a_probability(self):
        rng = np.random.default_rng(17)
        noise = rng.normal(0.0, 0.03, size=(160, 12))
        pbo = probability_of_backtest_overfitting(noise, blocks=8)
        self.assertGreaterEqual(pbo, 0.0)
        self.assertLessEqual(pbo, 1.0)

    def test_holdout_is_single_use(self):
        digest = declaration_hash({"generation": "g1", "candidate": "abc"})
        registry = authorize_single_use_holdout({}, "g1", digest)
        with self.assertRaises(RuntimeError):
            authorize_single_use_holdout(registry, "g1", digest)

    def test_reconciliation_blocks_downgrade(self):
        active = {"model_version": "0.1.0", "experiments_total": 18}
        public = {"generation": {"version": "0.6.0"}, "progress": {"experiments_completed": 540}}
        result = reconcile_evidence(active, public)
        self.assertTrue(result["active_branch_is_downgrade"])
        self.assertFalse(result["publication_allowed"])
        self.assertEqual(result["strongest_known_evidence"]["experiments"], 540)


if __name__ == "__main__":
    unittest.main()
