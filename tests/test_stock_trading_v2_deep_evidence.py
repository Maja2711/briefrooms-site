from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime, timezone

from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_deep_evidence as evidence
from scripts import stock_trading_v2_discovery as discovery


def frontier(score: float = 70.0) -> dict:
    payload = {
        "schema_version": discovery.SCHEMA_VERSION,
        "market": "US",
        "generated_at": "2026-09-16T15:00:00Z",
        "mode": "shadow_no_production_influence",
        "holding_horizon": "OPEN_ENDED_MODEL_CONTROLLED",
        "global_selection_cutoff": None,
        "universe_semantic_sha256": "test-universe",
        "universe_size": 1000,
        "features_available": 100,
        "eligible_after_discovery_liquidity": 100,
        "frontier_size": 1,
        "cash_alternative": {"status": "AVAILABLE", "research_utility": 0.0},
        "candidates": [
            {
                "symbol": "ACME",
                "market_data_symbol": "ACME",
                "name": "Acme Corp",
                "exchange": "NASDAQ",
                "security_type": "COMMON_STOCK",
                "opportunity_score": score,
                "research_utility_vs_cash": 0.4,
                "score_components": {},
                "features": {
                    "latest_session": "2026-09-15",
                    "last_close": 100.0,
                    "returns": {"1": 0.02, "5": 0.05, "20": 0.10, "60": 0.15},
                    "atr_fraction": 0.02,
                    "median_turnover_20d": 50000000.0,
                    "volume_ratio_20d": 1.4,
                },
                "stage_zero": {"rank": 1, "lanes": ["movers"]},
                "liquidity": {
                    "discovery_pass": True,
                    "median_turnover": 50000000.0,
                    "production_reference_pass": True,
                    "production_reference": 25000000.0,
                },
                "freshness": {
                    "latest_session": "2026-09-15",
                    "observed_at": "2026-09-16T15:00:00Z",
                    "recheck_after": "2026-09-16T16:15:00Z",
                    "global_cutoff": None,
                },
                "admission": {"status": "PENDING_DEEP_EVIDENCE_AND_RISK_PLAN", "production_decision_influence": False},
                "frontier_rank": 1,
            }
        ],
        "fetch_audit": {},
        "governance": {
            "production_decision_influence": False,
            "automatic_portfolio_admission": False,
            "rank_only_not_recommendation": True,
            "deep_evidence_pending": True,
            "no_forced_trade": True,
            "no_global_selection_cutoff": True,
        },
    }
    payload["frontier_sha256"] = contracts.payload_sha256(payload)
    discovery.validate_frontier(payload)
    return payload


class StockTradingV2DeepEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.config = evidence.load_config()

    def test_positive_primary_event_is_bounded_overlay_and_risk_plan_is_research_only(self):
        source = [{
            "evidence_id": "ev-1",
            "provider": "SEC_EDGAR",
            "authority": "primary",
            "source_kind": "regulatory_filing",
            "title": "ACME raises guidance after record revenue",
            "published_at": "2026-09-16T14:00:00Z",
            "age_hours": 1.0,
            "event_type": "guidance",
            "materiality": 5,
            "direction": 1,
        }]
        providers = {
            "primary": {"provider": "SEC_EDGAR", "ok": True},
            "secondary": {"provider": "GOOGLE_NEWS_RSS", "ok": True},
        }
        payload = evidence.build_deep_evidence(
            frontier(),
            {"ACME": (source, providers)},
            self.config,
            generated_at=datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc),
        )
        row = payload["candidates"][0]
        self.assertEqual(row["evidence_status"], "COMPLETE")
        self.assertGreater(row["deep_opportunity_score"], row["opportunity_score"])
        self.assertLessEqual(row["evidence_metrics"]["total_overlay_points"], 8.0)
        self.assertEqual(row["research_risk_plan"]["status"], "VALID_RESEARCH_REFERENCE")
        self.assertFalse(row["research_risk_plan"]["execution_ready"])
        self.assertLess(row["research_risk_plan"]["stop"], row["research_risk_plan"]["reference_price"])
        self.assertGreater(row["research_risk_plan"]["target"], row["research_risk_plan"]["reference_price"])
        evidence.validate_deep_evidence(payload)

    def test_no_recent_event_is_neutral_not_a_data_error(self):
        providers = {
            "primary": {"provider": "SEC_EDGAR", "ok": True, "recent_relevant": 0},
            "secondary": {"provider": "GOOGLE_NEWS_RSS", "ok": True, "recent_relevant": 0},
        }
        payload = evidence.build_deep_evidence(frontier(), {"ACME": ([], providers)}, self.config)
        row = payload["candidates"][0]
        self.assertEqual(row["evidence_status"], "COMPLETE")
        self.assertEqual(row["evidence_metrics"]["total_overlay_points"], 0.0)
        self.assertEqual(row["deep_opportunity_score"], row["opportunity_score"])

    def test_both_providers_failed_is_fail_closed_for_later_admission(self):
        providers = {
            "primary": {"provider": "SEC_EDGAR", "ok": False},
            "secondary": {"provider": "GOOGLE_NEWS_RSS", "ok": False},
        }
        payload = evidence.build_deep_evidence(frontier(), {"ACME": ([], providers)}, self.config)
        row = payload["candidates"][0]
        self.assertEqual(row["evidence_status"], "DATA_ERROR")
        self.assertLess(row["deep_opportunity_score"], row["opportunity_score"])

    def test_tamper_is_detected(self):
        providers = {"primary": {"ok": True}, "secondary": {"ok": True}}
        payload = evidence.build_deep_evidence(frontier(), {"ACME": ([], providers)}, self.config)
        broken = deepcopy(payload)
        broken["candidates"][0]["deep_opportunity_score"] = 999
        with self.assertRaises(Exception):
            evidence.validate_deep_evidence(broken)


if __name__ == "__main__":
    unittest.main()
