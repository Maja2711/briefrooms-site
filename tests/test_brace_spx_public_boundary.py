from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publisher = load_module("publish_brace_spx_public", ROOT / "scripts" / "publish_brace_spx_public.py")
installer = load_module("install_brace_spx_public_panel", ROOT / "scripts" / "install_brace_spx_public_panel.py")


class PublicBoundaryTests(unittest.TestCase):
    def test_checked_in_public_report_contains_no_private_keys(self):
        payload = json.loads((ROOT / "data" / "public" / "brace_spx_public.json").read_text(encoding="utf-8"))
        publisher.assert_public_boundary(payload)
        self.assertFalse(payload["public_boundary"]["code_exposed"])
        self.assertFalse(payload["public_boundary"]["parameters_exposed"])
        self.assertFalse(payload["public_boundary"]["raw_predictions_exposed"])
        self.assertFalse(payload["public_boundary"]["full_experiment_ledger_exposed"])

    def test_sanitizer_whitelists_aggregates_only(self):
        private = {
            "model_version": "9.9.9",
            "generated_at": "2026-07-24T08:00:00+00:00",
            "experiments_total": 42,
            "candidate_space_size": 100,
            "sealed_holdout": {"months": 48, "status": "not_accessed"},
            "champion": {
                "candidate_id": "secret",
                "candidate": {
                    "family": "secret_model",
                    "feature_set": "secret_features",
                    "params": {"alpha": 123},
                    "entry_return": -0.02,
                },
                "walk_forward": {
                    "metrics": {"cagr": 0.12, "sharpe_zero_rf": 1.1, "max_drawdown": -0.2},
                    "average_exposure": 0.9,
                    "probability_tail": [{"probability": 0.99}],
                    "fold_metrics": [{"cagr": 99}],
                    "baseline_metrics": {
                        "buy_hold": {"cagr": 0.1},
                        "trend_200d": {"cagr": 0.08},
                    },
                    "robustness_gate": {"passed": False, "positive_folds": 4, "required_positive_folds": 5},
                },
            },
            "top_candidates": [{"candidate_id": "also-secret"}],
        }
        public = publisher.sanitize(private)
        publisher.assert_public_boundary(public)
        serialized = json.dumps(public)
        for secret in ("secret_model", "secret_features", "also-secret", "probability_tail"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(public["progress"]["experiments_completed"], 42)
        self.assertEqual(public["development_champion"]["metrics"]["cagr"], 0.12)

    def test_tab_installer_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "page.html"
            page.write_text("<html><body><main>\n<p>content</p></main></body></html>\n", encoding="utf-8")
            self.assertTrue(installer.install_page(page, installer.PL_TAB))
            self.assertFalse(installer.install_page(page, installer.PL_TAB))
            source = page.read_text(encoding="utf-8")
            self.assertEqual(source.count(installer.MARKER), 1)

    def test_public_pages_do_not_embed_private_model_fields(self):
        forbidden_text = ("candidate_id", "feature_set", "probability_tail", "fold_metrics", '"params"')
        for relative in (
            "pl/inwestycje/brace-spx-lab.html",
            "en/investing/brace-spx-lab.html",
            "scripts/brace-spx-lab-public.js",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            for forbidden in forbidden_text:
                self.assertNotIn(forbidden, source, relative)


if __name__ == "__main__":
    unittest.main()
