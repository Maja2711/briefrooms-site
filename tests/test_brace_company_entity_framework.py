from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.brace_company_entity_framework import (
    REPORT_FILENAME,
    STATE_FILENAME,
    candidate_watchlist_entities,
    capabilities,
    current_portfolio_entities,
    desired_entities,
    dimension_registry_for,
    entity_belief_definitions,
    promotion_evidence_standard,
    run,
    safety_controls,
    update_activation_state,
)


class BraceCompanyEntityFrameworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.universe = {
            "instruments": [
                {"instrument_id": "alpha", "data_symbol": "ALPHA", "asset_type": "STOCK", "sector": "Information Technology", "region": "United States", "exchange": "NASDAQ", "availability": "AVAILABLE", "active": True},
                {"instrument_id": "beta", "data_symbol": "BETA", "asset_type": "STOCK", "sector": "Financials", "exposure_key": "diversified_banking", "region": "United States", "exchange": "NYSE", "availability": "AVAILABLE", "active": True},
                {"instrument_id": "gamma", "data_symbol": "GAMMA", "asset_type": "STOCK", "sector": "Health Care", "region": "United States", "exchange": "NYSE", "availability": "AVAILABLE", "active": True},
                {"instrument_id": "etf", "data_symbol": "ETF", "asset_type": "BROAD_ETF", "sector": "Diversified", "availability": "AVAILABLE", "active": True},
            ]
        }
        self.portfolio = {
            "positions": [
                {"id": "alpha", "market_symbol": "ALPHA", "asset_type": "Stock", "status": "active"},
                {"id": "etf", "market_symbol": "ETF", "asset_type": "ETF", "status": "active"},
            ]
        }
        self.analysis = {
            "candidates": [
                {"instrument_id": "beta", "data_symbol": "BETA", "asset_type": "STOCK", "availability": "AVAILABLE", "active": True, "rank": 2, "confidence_score": 0.01},
                {"instrument_id": "etf", "data_symbol": "ETF", "asset_type": "BROAD_ETF", "availability": "AVAILABLE", "active": True, "rank": 1},
            ]
        }

    def test_current_portfolio_stock_is_always_on_and_etf_is_ignored(self) -> None:
        rows = current_portfolio_entities(self.portfolio, self.universe)
        self.assertEqual(set(rows), {"alpha"})
        self.assertEqual(rows["alpha"]["activation_class"], "always_on")
        self.assertEqual(rows["alpha"]["activation_source"], "current_portfolio")

    def test_canonical_candidate_list_is_activation_boundary_without_second_score_threshold(self) -> None:
        rows = candidate_watchlist_entities(self.analysis, self.universe)
        self.assertEqual(set(rows), {"beta"})
        self.assertEqual(rows["beta"]["activation_class"], "pre_entry_research")
        self.assertEqual(rows["beta"]["candidate_rank"], 2)
        # confidence_score is deliberately poor: PR12 trusts the already-filtered
        # canonical BRACE candidate list rather than inventing a second threshold.
        self.assertEqual(rows["beta"]["confidence_score"], 0.01)

    def test_non_candidate_universe_stock_does_not_activate(self) -> None:
        rows = desired_entities(self.portfolio, self.analysis, self.universe)
        self.assertEqual(set(rows), {"alpha", "beta"})
        self.assertNotIn("gamma", rows)

    def test_candidate_activation_precedes_later_portfolio_entry_and_lineage_is_preserved(self) -> None:
        day1 = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)
        candidate_only = desired_entities({"positions": []}, self.analysis, self.universe)
        state1 = update_activation_state({}, candidate_only, day1)
        first = state1["entities"]["beta"]
        self.assertEqual(first["first_activation_source"], "brace_candidate_watchlist")
        first_at = first["first_activated_at"]

        day2 = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
        portfolio2 = {"positions": [{"id": "beta", "asset_type": "Stock", "status": "active"}]}
        desired2 = desired_entities(portfolio2, {"candidates": []}, self.universe)
        state2 = update_activation_state(state1, desired2, day2)
        beta = state2["entities"]["beta"]
        self.assertEqual(beta["first_activated_at"], first_at)
        self.assertEqual(beta["first_activation_source"], "brace_candidate_watchlist")
        self.assertTrue(beta["ever_candidate_watchlist"])
        self.assertTrue(beta["ever_current_portfolio"])
        self.assertEqual(beta["current_activation_source"], "current_portfolio")

    def test_removed_candidate_becomes_dormant_without_deleting_history(self) -> None:
        day1 = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)
        state1 = update_activation_state({}, desired_entities({"positions": []}, self.analysis, self.universe), day1)
        first_at = state1["entities"]["beta"]["first_activated_at"]
        day2 = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
        state2 = update_activation_state(state1, {}, day2)
        beta = state2["entities"]["beta"]
        self.assertEqual(beta["current_status"], "dormant")
        self.assertEqual(beta["first_activated_at"], first_at)
        self.assertTrue(beta["ever_candidate_watchlist"])

    def test_common_core_plus_sector_modules_materialize_definitions_but_no_forecasts(self) -> None:
        beta = desired_entities({"positions": []}, self.analysis, self.universe)["beta"]
        dimensions = {row["dimension"] for row in dimension_registry_for(beta)}
        self.assertIn("earnings_momentum", dimensions)
        self.assertIn("valuation", dimensions)
        self.assertIn("net_interest_income_durability", dimensions)
        self.assertIn("credit_quality", dimensions)
        definitions = entity_belief_definitions(beta)
        self.assertTrue(definitions)
        self.assertTrue(all(row["belief_id"].startswith("entity.beta.") for row in definitions))
        self.assertTrue(all(row["forecast_capture_enabled"] is False for row in definitions))
        self.assertTrue(all(row["engine_influence_enabled"] is False for row in definitions))
        self.assertTrue(all(row["reporting_regime"] == "unresolved_requires_primary_source_adapter" for row in definitions))

    def test_zero_influence_and_with_without_governance(self) -> None:
        self.assertTrue(all(value is False for value in safety_controls().values()))
        caps = capabilities()
        self.assertTrue(caps["entity_activation_registry_enabled"])
        self.assertTrue(caps["candidate_watchlist_pre_entry_activation_enabled"])
        self.assertFalse(caps["entity_evidence_ingestion_enabled"])
        self.assertFalse(caps["entity_forecast_capture_enabled"])
        self.assertFalse(caps["with_without_bridge_enabled"])
        gate = promotion_evidence_standard()
        self.assertTrue(gate["with_without_required"])
        self.assertTrue(gate["effective_n_required"])
        self.assertFalse(gate["automatic_promotion"])

    def test_run_persists_private_activation_state_and_framework_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            portfolio_path = root / "portfolio.json"
            analysis_path = root / "analysis.json"
            universe_path = root / "universe.json"
            portfolio_path.write_text(json.dumps(self.portfolio), encoding="utf-8")
            analysis_path.write_text(json.dumps(self.analysis), encoding="utf-8")
            universe_path.write_text(json.dumps(self.universe), encoding="utf-8")
            state_dir = root / "state"
            report = run(
                state_dir,
                portfolio_path=portfolio_path,
                analysis_path=analysis_path,
                universe_path=universe_path,
                as_of=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
            )
            self.assertTrue((state_dir / STATE_FILENAME).exists())
            self.assertTrue((state_dir / REPORT_FILENAME).exists())
            self.assertEqual(report["sample"]["active_entities"], 2)
            self.assertEqual(report["sample"]["active_current_portfolio_entities"], 1)
            self.assertEqual(report["sample"]["active_candidate_watchlist_entities"], 1)
            self.assertTrue(report["activation_policy"]["portfolio_entry_not_required"])
            self.assertFalse(report["activation_policy"]["additional_hidden_candidate_threshold"])
            self.assertFalse(report["anti_hindsight"]["historical_backfill"])
            self.assertFalse(report["capabilities"]["entity_forecast_capture_enabled"])
            self.assertTrue(all(not str(x["market_symbol"]).startswith("ETF") for x in report["entities"]["active"]))


if __name__ == "__main__":
    unittest.main()
