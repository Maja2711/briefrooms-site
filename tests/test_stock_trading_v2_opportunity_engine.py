from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime, timezone

from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_opportunity_engine as engine


def deep_snapshot(score: float = 72.0, *, evidence_status: str = "COMPLETE", liquid: bool = True) -> dict:
    payload = {
        "schema_version": "stock-trading-v2-deep-evidence-v1",
        "market": "US",
        "generated_at": "2026-09-16T15:00:00Z",
        "source_frontier_sha256": "frontier-test",
        "source_frontier_generated_at": "2026-09-16T14:55:00Z",
        "candidate_count": 1,
        "candidates": [
            {
                "symbol": "ACME",
                "market_data_symbol": "ACME",
                "name": "Acme Corp",
                "frontier_rank": 1,
                "deep_rank": 1,
                "opportunity_score": score,
                "deep_opportunity_score": score,
                "evidence_status": evidence_status,
                "evidence_metrics": {"evidence_quality_score": 60.0, "total_overlay_points": 0.0},
                "provider_health": {},
                "evidence": [],
                "liquidity": {
                    "discovery_pass": True,
                    "production_reference_pass": liquid,
                    "median_turnover": 50000000.0,
                    "production_reference": 25000000.0,
                },
                "freshness": {},
                "features": {},
                "research_risk_plan": {
                    "status": "VALID_RESEARCH_REFERENCE",
                    "reference_price": 100.0,
                    "stop": 98.0,
                    "target": 104.0,
                    "risk_percent": 0.02,
                    "reward_risk": 2.0,
                    "execution_ready": False,
                    "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED",
                    "execution_requirement": "fresh_quote_and_intraday_risk_revalidation",
                },
                "admission": {"status": "PENDING_SHADOW_PORTFOLIO_COMPARISON", "production_decision_influence": False},
            }
        ],
        "governance": {
            "production_decision_influence": False,
            "automatic_portfolio_admission": False,
            "primary_sources_are_evidence_not_trade_signals": True,
            "research_risk_plan_execution_ready": False,
            "no_forced_trade": True,
        },
    }
    payload["evidence_sha256"] = contracts.payload_sha256(payload)
    return payload


def portfolio(*scores: float) -> dict:
    rows = []
    for idx, score in enumerate(scores, start=1):
        rows.append({
            "position_id": f"us:p{idx}",
            "market": "US",
            "status": "OPEN",
            "symbol": f"OLD{idx}",
            "entry": 100.0,
            "last_mark": 100.0,
            "entry_score": score,
            "thesis_score": score,
            "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED",
            "scheduled_exit": None,
            "valid_until": None,
            "time_stop": None,
        })
    return {"markets": {"US": {"open_positions": rows}, "GPW": {"open_positions": []}}}


class StockTradingV2OpportunityEngineTests(unittest.TestCase):
    def setUp(self):
        self.config = engine.load_config()

    def test_buy_requires_edge_and_free_slot(self):
        payload = engine.compare_opportunities(
            deep_snapshot(72.0),
            portfolio(),
            self.config,
            generated_at=datetime(2026, 9, 16, 15, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(payload["decision"]["action"], "BUY")
        self.assertEqual(payload["decision"]["candidate"]["symbol"], "ACME")
        self.assertEqual(payload["portfolio"]["available_slots"], 3)
        self.assertFalse(payload["governance"]["execution_ready"])
        engine.validate_opportunity(payload)

    def test_full_book_replacement_requires_hysteresis(self):
        payload = engine.compare_opportunities(deep_snapshot(80.0), portfolio(45.0, 60.0, 65.0), self.config)
        self.assertEqual(payload["decision"]["action"], "REPLACE")
        self.assertEqual(payload["decision"]["replace"]["symbol"], "OLD1")
        self.assertEqual(payload["portfolio"]["available_slots"], 0)

    def test_full_book_holds_when_candidate_does_not_clear_replacement_margin(self):
        payload = engine.compare_opportunities(deep_snapshot(62.0), portfolio(58.0, 60.0, 65.0), self.config)
        self.assertEqual(payload["decision"]["action"], "HOLD")

    def test_no_position_and_weak_candidate_keeps_cash(self):
        payload = engine.compare_opportunities(deep_snapshot(53.0), portfolio(), self.config)
        self.assertEqual(payload["decision"]["action"], "CASH")

    def test_data_error_and_low_liquidity_are_hard_gates(self):
        for snapshot in (deep_snapshot(90.0, evidence_status="DATA_ERROR"), deep_snapshot(90.0, liquid=False)):
            payload = engine.compare_opportunities(snapshot, portfolio(), self.config)
            self.assertEqual(payload["decision"]["action"], "CASH")
            self.assertFalse(payload["evaluated_candidates"][0]["eligible"])

    def test_tamper_is_detected(self):
        payload = engine.compare_opportunities(deep_snapshot(72.0), portfolio(), self.config)
        broken = deepcopy(payload)
        broken["decision"]["action"] = "REPLACE"
        with self.assertRaises(Exception):
            engine.validate_opportunity(broken)


if __name__ == "__main__":
    unittest.main()
