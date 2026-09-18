#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BracePortfolioP1ContractTests(unittest.TestCase):
    def read(self, path: str) -> str:
        return (ROOT / path).read_text(encoding="utf-8")

    def test_current_frontend_uses_only_canonical_brace_public_source(self):
        current_frontend = "
".join(
            self.read(path)
            for path in (
                "scripts/portfolio-10k-dashboard.js",
                "scripts/portfolio-10k-dashboard-en.js",
                "scripts/portfolio-10k-brace-public.js",
                "scripts/portfolio-10k-control-public.js",
            )
        )
        self.assertIn("/data/portfolio10k/public/brace_engine_public.json", current_frontend)
        self.assertNotIn("/data/investments/portfolio_10k_brace.json", current_frontend)

    def test_no_hardcoded_shadow_badge_remains_in_current_pages(self):
        for path in (
            "pl/inwestycje/portfel-10k.html",
            "en/investing/portfolio-10k.html",
        ):
            html = self.read(path)
            self.assertNotIn(">SHADOW<", html)
            self.assertGreaterEqual(html.count("data-brace-status"), 2)

    def test_frontend_cache_is_measured_in_hours_and_exposes_freshness_states(self):
        for path in (
            "scripts/portfolio-10k-dashboard.js",
            "scripts/portfolio-10k-dashboard-en.js",
        ):
            js = self.read(path)
            self.assertIn("LIVE_MAX_AGE_MS = 2 * 60 * 60 * 1000", js)
            self.assertIn("CACHE_MAX_AGE_MS = 6 * 60 * 60 * 1000", js)
            self.assertNotIn("7 * 24 * 60 * 60 * 1000", js)
            for state in ("LIVE", "CACHED", "STALE"):
                self.assertIn(state, js)

    def test_immutable_baseline_is_separate_from_controlled_public_state(self):
        baseline = json.loads(self.read("data/portfolio10k/baseline_portfolio.json"))
        controlled = json.loads(self.read("data/investments/portfolio_10k.json"))
        registry = json.loads(self.read("data/portfolio10k/methodology_registry.json"))

        self.assertTrue(baseline["immutable"])
        self.assertFalse(baseline["brace_controlled"])
        self.assertEqual(
            "893be8b4ce5497b62ac99bd282a5a793826d545d",
            baseline["baseline_source_commit"],
        )
        self.assertEqual(
            ["fwia", "zprv", "googl", "amzn", "tsm", "visa", "spgi", "novo"],
            [item["id"] for item in baseline["positions"]],
        )
        self.assertEqual("BRACE_CONTROLLED_PUBLIC_PORTFOLIO", controlled["state_role"])
        self.assertTrue(controlled["baseline_benchmark_immutable"])
        self.assertEqual(
            "/data/portfolio10k/baseline_portfolio.json",
            controlled["baseline_benchmark_path"],
        )
        self.assertEqual(
            "data/portfolio10k/baseline_portfolio.json",
            registry["source_metadata"]["baseline_portfolio"],
        )
        self.assertEqual(
            "data/investments/portfolio_10k.json",
            registry["source_metadata"]["controlled_portfolio"],
        )

    def test_runtime_data_boundary_uses_distinct_paths(self):
        module = self.read("scripts/brace_portfolio_data.py")
        self.assertIn(
            'CONTROLLED_PORTFOLIO_PATH = ROOT / "data" / "investments" / "portfolio_10k.json"',
            module,
        )
        self.assertIn(
            'BASELINE_PORTFOLIO_PATH = ENGINE_DATA_ROOT / "baseline_portfolio.json"',
            module,
        )


if __name__ == "__main__":
    unittest.main()
