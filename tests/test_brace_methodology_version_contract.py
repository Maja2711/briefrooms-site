#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BraceMethodologyVersionContractTests(unittest.TestCase):
    def load(self, path: str):
        return json.loads((ROOT / path).read_text(encoding="utf-8"))

    def test_current_brace_surfaces_share_configured_version(self):
        config = self.load("data/portfolio10k/config.json")
        expected_full = config["methodology_version"]
        expected_semver = expected_full.removeprefix("brace-portfolio-v")

        registry = self.load("data/portfolio10k/methodology_registry.json")
        brace = next(
            item for item in registry["methodologies"]
            if item["methodology_id"] == "brace-portfolio-engine"
        )
        self.assertEqual(expected_semver, brace["version"])

        analysis = self.load("data/portfolio10k/analysis.json")
        self.assertEqual(expected_full, analysis["methodology_version"])

        pending = self.load("data/portfolio10k/pending_decisions.json")
        self.assertEqual(expected_full, pending["methodology_version"])
        for item in pending.get("decisions", []):
            self.assertEqual(expected_full, item["methodology_version"])

        operational = self.load("data/portfolio10k/operational_state.json")
        self.assertEqual(expected_full, operational["methodology_version"])

        research = self.load("data/portfolio10k/research_validation.json")
        self.assertEqual(expected_full, research["methodology_version"])

        paper_orders = self.load("data/portfolio10k/paper_orders.json")
        self.assertEqual(expected_full, paper_orders["methodology_version"])

        public = self.load("data/portfolio10k/public/brace_engine_public.json")
        self.assertEqual(expected_semver, public["methodology_version"])
        if public.get("champion", {}).get("methodology_id") == "brace-portfolio-engine":
            self.assertEqual(expected_semver, public["champion"]["version"])
        if public.get("challenger", {}).get("methodology_id") == "brace-portfolio-engine":
            self.assertEqual(expected_semver, public["challenger"]["version"])
        for item in public.get("pending_decisions", []):
            if item.get("methodology_version"):
                self.assertEqual(expected_full, item["methodology_version"])

    def test_generators_do_not_hardcode_retired_v3_0_version(self):
        for path in (
            "scripts/brace_portfolio_engine.py",
            "scripts/brace_portfolio_learning.py",
            "scripts/brace_portfolio_backtest.py",
        ):
            source = (ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn("brace-portfolio-v3.0.0", source)
        engine = (ROOT / "scripts/brace_portfolio_engine.py").read_text(encoding="utf-8")
        self.assertNotIn('or "3.0.0"', engine)

    def test_config_version_format_is_governed(self):
        config = self.load("data/portfolio10k/config.json")
        value = config["methodology_version"]
        self.assertTrue(value.startswith("brace-portfolio-v"))
        parts = value.removeprefix("brace-portfolio-v").split(".")
        self.assertEqual(3, len(parts))
        self.assertTrue(all(part.isdigit() for part in parts))


if __name__ == "__main__":
    unittest.main()
