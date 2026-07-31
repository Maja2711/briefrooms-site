from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brace_spx_generation5_selection import select
from brace_spx_generation5_verdict import evaluate


class Generation5GovernanceTests(unittest.TestCase):
    def sample(self):
        experiments = []
        for number in range(12):
            candidate_id = f"candidate-{number}"
            sharpe = 1.20 - number * 0.01
            experiments.append({
                "candidate_id": candidate_id,
                "candidate": {"params": {"candidate_name": candidate_id, "geometry_family": "staircase"}},
                "metrics": {
                    "sharpe_excess": sharpe,
                    "cagr": 0.12,
                    "max_drawdown": -0.10,
                    "calmar": 1.2,
                    "annualized_turnover": 2.0,
                    "months": 108,
                },
                "fold_metrics": [{"sharpe_excess": 1.0 + number * 0.001} for _ in range(6)],
                "monthly_returns": [
                    {
                        "date": f"{2014 + year:04d}-{month:02d}-28",
                        "return": 0.004 + 0.0004 * ((month + number * 3) % 7) + 0.0001 * (year % 3),
                    }
                    for year in range(9)
                    for month in range(1, 13)
                ],
            })
        geometry_stats = {
            row["candidate_id"]: {
                "active_exposure_buckets": 4,
                "annualized_transition_rate": 3.0,
                "average_exposure": 0.6,
                "exposure_std": 0.2,
                "mean_absolute_exposure_correlation_to_pool": 0.60,
            }
            for row in experiments
        }
        report = {
            "generation_id": "spx-state-geometry-v5",
            "experiments_total": 12,
            "experiments_remaining": 0,
            "candidate_space_size": 12,
            "holdout": {"status": "sealed", "accessed": False, "access_count": 0},
            "champion": {"candidate_id": "candidate-0"},
            "multiple_testing": {"sharpe_cross_section_std": 0.05, "pbo": {"available": True, "probability": 0.10}},
            "development_baselines": {
                "buy_and_hold": {"sharpe_excess": 0.90},
                "trend_200d": {"sharpe_excess": 0.85},
            },
            "return_diversity": {
                "available": True,
                "median_absolute_pairwise_correlation": 0.60,
                "effective_independent_candidates": 4.0,
                "largest_cluster_share": 0.50,
            },
            "geometry": {
                "candidate_stats": geometry_stats,
                "exposure_diversity": {
                    "available": True,
                    "median_absolute_pairwise_correlation": 0.55,
                    "effective_independent_candidates": 4.5,
                    "largest_cluster_share": 0.50,
                },
            },
            "design": {"generation4_reopened": False},
        }
        manifest = {"generation_id": "spx-state-geometry-v5", "candidate_signature": "signature"}
        ledger = {"experiments": experiments}
        return experiments, report, manifest, ledger

    def passing_audit(self):
        selected = {
            "candidate_id": "candidate-0",
            "candidate_name": "candidate-0",
            "geometry_family": "staircase",
            "sharpe_excess": 1.20,
            "sharpe_standard_error": 0.12,
            "cagr": 0.12,
            "max_drawdown": -0.10,
            "calmar": 1.2,
            "annualized_turnover": 2.0,
            "positive_folds": 6,
            "folds": 6,
            "fold_sharpe_std": 0.20,
            "mean_absolute_return_correlation_to_pool": 0.60,
            "mean_absolute_exposure_correlation_to_pool": 0.60,
            "active_exposure_buckets": 4,
            "annualized_transition_rate": 3.0,
            "average_exposure": 0.6,
            "exposure_std": 0.2,
            "stable": True,
        }
        return {"selection": {"selected": selected}}

    def test_selection_uses_geometry_evidence(self):
        experiments, report, _manifest, _ledger = self.sample()
        result = select(experiments, report)
        self.assertIsNotNone(result["selected"])
        self.assertGreaterEqual(result["selected"]["active_exposure_buckets"], 3)
        self.assertIn("mean_absolute_exposure_correlation_to_pool", result["selected"])
        self.assertTrue(result["selection_rule"]["shared_signal_for_all_candidates"])

    def test_strict_gate_requires_exhaustion_and_sealed_holdout(self):
        _experiments, report, manifest, ledger = self.sample()
        audit = self.passing_audit()
        verdict = evaluate(report, audit, manifest, ledger)
        self.assertTrue(verdict["strict_gate_passed"], verdict["checks"])
        report["experiments_remaining"] = 1
        self.assertFalse(evaluate(report, audit, manifest, ledger)["strict_gate_passed"])
        report["experiments_remaining"] = 0
        report["holdout"]["accessed"] = True
        self.assertFalse(evaluate(report, audit, manifest, ledger)["strict_gate_passed"])

    def test_high_pbo_blocks_promotion(self):
        _experiments, report, manifest, ledger = self.sample()
        audit = self.passing_audit()
        report["multiple_testing"]["pbo"]["probability"] = 0.30
        verdict = evaluate(report, audit, manifest, ledger)
        self.assertFalse(verdict["strict_gate_passed"])
        self.assertFalse(verdict["checks"]["pbo_at_most_0_20"])

    def test_exposure_collapse_blocks_promotion(self):
        _experiments, report, manifest, ledger = self.sample()
        audit = self.passing_audit()
        report["geometry"]["exposure_diversity"]["largest_cluster_share"] = 0.90
        verdict = evaluate(report, audit, manifest, ledger)
        self.assertFalse(verdict["strict_gate_passed"])
        self.assertFalse(verdict["checks"]["largest_exposure_cluster_share_at_most_0_67"])


if __name__ == "__main__":
    unittest.main()
