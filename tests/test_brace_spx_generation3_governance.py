from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brace_spx_generation3_verdict import evaluate
from publish_brace_spx_generation3_public import assert_safe, sanitize


class Generation3GovernanceTests(unittest.TestCase):
    def sample(self):
        report = {
            "status": "generation_exhausted_holdout_still_sealed",
            "generated_at": "2026-07-29T00:00:00+00:00",
            "candidate_space_size": 48,
            "experiments_total": 48,
            "experiments_remaining": 0,
            "holdout": {"status": "sealed", "accessed": False, "months": 48},
            "champion": {
                "metrics": {"cagr": 0.14, "sharpe_excess": 1.5, "max_drawdown": -0.08, "calmar": 1.75},
                "deflated_sharpe_ratio": {"probability": 0.98},
            },
            "multiple_testing": {"pbo": {"available": True, "probability": 0.15}},
            "development_baselines": {
                "buy_and_hold": {"sharpe_excess": 1.0},
                "trend_200d": {"sharpe_excess": 0.9},
            },
        }
        audit = {
            "selection": {"selected": {
                "cagr": 0.14,
                "sharpe_excess": 1.5,
                "max_drawdown": -0.08,
                "calmar": 1.75,
                "positive_folds": 6,
                "folds": 6,
                "fold_sharpe_std": 0.6,
            }}
        }
        manifest = {"generation_id": "spx-focused-v3", "candidate_signature": "abc123"}
        return report, audit, manifest

    def test_strict_gate_can_pass_only_after_exhaustion(self):
        report, audit, manifest = self.sample()
        verdict = evaluate(report, audit, manifest)
        self.assertTrue(verdict["strict_gate_passed"])
        report["experiments_remaining"] = 1
        verdict = evaluate(report, audit, manifest)
        self.assertFalse(verdict["strict_gate_passed"])
        self.assertEqual(verdict["status"], "research_in_progress")

    def test_holdout_access_blocks_gate(self):
        report, audit, manifest = self.sample()
        report["holdout"]["accessed"] = True
        self.assertFalse(evaluate(report, audit, manifest)["strict_gate_passed"])

    def test_public_snapshot_contains_no_model_parameters(self):
        report, audit, manifest = self.sample()
        verdict = evaluate(report, audit, manifest)
        payload = sanitize(report, manifest, verdict)
        assert_safe(payload)
        rendered = str(payload)
        for forbidden in ("threshold_high", "candidate_id", "monthly_returns", "params"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
