import json
import tempfile
import unittest
from pathlib import Path

from scripts.brace_spx_statistical_audit import estimate_pbo, run_audit


class StatisticalAuditTests(unittest.TestCase):
    def test_pbo_detects_unstable_winner(self):
        experiments = []
        fold_sets = [
            [0.4, 0.4, -0.4, -0.4],
            [0.3, 0.3, -0.3, -0.3],
            [-0.1, -0.1, 0.1, 0.1],
        ]
        for idx, values in enumerate(fold_sets):
            experiments.append({
                "candidate_id": f"c{idx}",
                "walk_forward": {"fold_metrics": [{"objective_advantage": value} for value in values]},
            })
        result = estimate_pbo(experiments)
        self.assertTrue(result["available"])
        self.assertGreater(result["probability_of_backtest_overfitting"], 0.0)

    def test_audit_fails_closed_on_legacy_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "report.json"
            ledger_path = root / "ledger.json"
            output_path = root / "audit.json"
            registry_path = root / "registry.json"
            report_path.write_text(json.dumps({
                "model_version": "0.1.0",
                "generated_at": "2026-07-24T00:00:00Z",
                "holdout_baselines": {"buy_hold": {"cagr": 0.1}},
                "champion": {
                    "candidate_id": "abc",
                    "walk_forward": {"metrics": {"sharpe_zero_rf": 1.2, "months": 108}},
                },
            }), encoding="utf-8")
            experiments = []
            for idx in range(4):
                experiments.append({
                    "candidate_id": f"c{idx}",
                    "walk_forward": {"fold_metrics": [
                        {"objective_advantage": 0.01 * (idx + fold)} for fold in range(6)
                    ]},
                })
            ledger_path.write_text(json.dumps({"experiments": experiments}), encoding="utf-8")
            audit = run_audit(report_path, ledger_path, output_path, registry_path)
            self.assertFalse(audit["promotion_allowed"])
            self.assertIn("sealed_holdout_integrity", audit["promotion_blockers"])
            self.assertIn("conventional_excess_return_sharpe_missing", audit["promotion_blockers"])
            self.assertTrue(output_path.exists())

    def test_registry_rejects_second_candidate_in_same_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "report.json"
            ledger_path = root / "ledger.json"
            output_path = root / "audit.json"
            registry_path = root / "registry.json"
            ledger_path.write_text(json.dumps({"experiments": []}), encoding="utf-8")
            base = {
                "model_version": "1.0.0",
                "generated_at": "2026-07-24T00:00:00Z",
                "champion": {
                    "candidate_id": "first",
                    "holdout": {"metrics": {"cagr": 0.1}},
                    "walk_forward": {"metrics": {"sharpe_excess": 1.0, "months": 120}},
                },
            }
            report_path.write_text(json.dumps(base), encoding="utf-8")
            run_audit(report_path, ledger_path, output_path, registry_path)
            base["champion"]["candidate_id"] = "second"
            report_path.write_text(json.dumps(base), encoding="utf-8")
            audit = run_audit(report_path, ledger_path, output_path, registry_path)
            self.assertTrue(audit["holdout_integrity"]["repeated_generation_access"])
            self.assertFalse(audit["promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
