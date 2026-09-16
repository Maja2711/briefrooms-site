from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_deep_evidence as deep
from scripts import stock_trading_v2_opportunity_engine as opportunity
from scripts import stock_trading_v2_opportunity_experience as freeze
from scripts import stock_trading_v2_outcome_replay as replay


def evidence_snapshot() -> dict:
    payload = {
        "schema_version": deep.SCHEMA_VERSION,
        "market": "US",
        "generated_at": "2026-09-16T15:00:00Z",
        "source_frontier_sha256": "frontier",
        "source_frontier_generated_at": "2026-09-16T14:55:00Z",
        "candidate_count": 2,
        "candidates": [],
        "governance": {
            "production_decision_influence": False,
            "automatic_portfolio_admission": False,
            "primary_sources_are_evidence_not_trade_signals": True,
            "research_risk_plan_execution_ready": False,
            "no_forced_trade": True,
        },
    }
    for rank, (symbol, score) in enumerate((("ACME", 75.0), ("BETA", 65.0)), start=1):
        payload["candidates"].append({
            "symbol": symbol,
            "market_data_symbol": symbol,
            "name": f"{symbol} Corp",
            "frontier_rank": rank,
            "deep_rank": rank,
            "opportunity_score": score,
            "deep_opportunity_score": score,
            "evidence_status": "COMPLETE",
            "evidence_metrics": {"evidence_quality_score": 60.0, "total_overlay_points": 0.0},
            "provider_health": {"primary": {"ok": True}, "secondary": {"ok": True}},
            "evidence": [{
                "evidence_id": f"ev-{symbol}", "provider": "SEC_EDGAR", "authority": "primary",
                "event_type": "earnings", "materiality": 5, "direction": 0,
                "title": "SEC 10-Q", "published_at": "2026-09-16T13:00:00Z", "url": "https://www.sec.gov/",
            }],
            "liquidity": {"production_reference_pass": True, "median_turnover": 50000000.0},
            "freshness": {"latest_session": "2026-09-15"},
            "features": {"latest_session": "2026-09-15", "last_close": 100.0, "returns": {"1": 0.01}},
            "research_risk_plan": {
                "status": "VALID_RESEARCH_REFERENCE", "reference_price": 100.0, "stop": 98.0,
                "target": 104.0, "risk_percent": 0.02, "reward_risk": 2.0,
                "execution_ready": False, "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED",
            },
            "admission": {"status": "PENDING_SHADOW_PORTFOLIO_COMPARISON", "production_decision_influence": False},
        })
    payload["evidence_sha256"] = contracts.payload_sha256(payload)
    deep.validate_deep_evidence(payload)
    return payload


def opportunity_snapshot(evidence: dict) -> dict:
    config = opportunity.load_config()
    return opportunity.compare_opportunities(evidence, {"markets": {"US": {"open_positions": []}}}, config)


class StockTradingV2OpportunityExperienceTests(unittest.TestCase):
    def test_buy_and_rejected_candidate_enter_same_immutable_contract(self):
        evidence = evidence_snapshot()
        opp = opportunity_snapshot(evidence)
        events = freeze.events_from_snapshots(opp, evidence, candidates_per_cycle=2, recorded_at="2026-09-16T15:01:00Z")
        self.assertEqual(len(events), 2)
        selected = [row for row in events if row["selected"]]
        rejected = [row for row in events if not row["selected"]]
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(selected[0]["symbol"], "ACME")
        self.assertEqual(selected[0]["source"]["engine"], freeze.SOURCE_ENGINE)
        self.assertEqual(selected[0]["candidate_state"]["decision_path"]["portfolio_action"], "BUY")
        self.assertEqual(selected[0]["candidate_state"]["risk_plan"]["reference_price"], 100.0)
        self.assertEqual(
            rejected[0]["candidate_state"]["decision_path"]["first_blocking_gate"],
            "opportunity_ranked_below_selected_candidate",
        )
        contracts.validate_experience_event(selected[0])
        contracts.validate_experience_event(rejected[0])

    def test_eligible_cash_rejection_is_opportunity_edge_not_hard_gate(self):
        evaluated = {
            "eligible": True,
            "eligibility_reason": "eligible_shadow_opportunity",
            "symbol": "BETA",
            "utility": 53.0,
            "research_risk_plan": {"status": "VALID_RESEARCH_REFERENCE"},
        }
        state = freeze.candidate_state(
            evaluated,
            {"symbol": "BETA", "evidence_status": "COMPLETE"},
            selected=False,
            portfolio_action="CASH",
            opportunity_reason="no_eligible_opportunity_clears_cash_edge",
        )
        self.assertEqual(
            state["decision_path"]["first_blocking_gate"],
            "opportunity_did_not_clear_cash_or_replacement_edge",
        )

    def test_hard_gate_rejection_preserves_actual_gate(self):
        evaluated = {
            "eligible": False,
            "eligibility_reason": "production_reference_liquidity_not_met",
            "symbol": "BETA",
            "utility": 80.0,
            "research_risk_plan": {"status": "VALID_RESEARCH_REFERENCE"},
        }
        state = freeze.candidate_state(
            evaluated,
            {"symbol": "BETA", "evidence_status": "COMPLETE"},
            selected=False,
            portfolio_action="CASH",
            opportunity_reason="no_eligible_opportunity_clears_cash_edge",
        )
        self.assertEqual(
            state["decision_path"]["first_blocking_gate"],
            "production_reference_liquidity_not_met",
        )

    def test_continuous_selected_and_rejected_plans_are_replayable_without_lookahead(self):
        evidence = evidence_snapshot()
        opp = opportunity_snapshot(evidence)
        events = freeze.events_from_snapshots(opp, evidence, candidates_per_cycle=2, recorded_at="2026-09-16T15:01:00Z")
        future_bars = [
            {"day": "2026-09-17", "open": 100.0, "high": 105.0, "low": 99.0, "close": 103.0},
            {"day": "2026-09-18", "open": 103.0, "high": 106.0, "low": 102.0, "close": 105.0},
        ]
        for event in events:
            outcome = replay.replay_horizon(
                event,
                future_bars,
                horizon_sessions=2,
                cost_stress_percent=0.1,
                replay_version="unit-test",
            )
            self.assertIsNotNone(outcome)
            assert outcome is not None
            self.assertEqual(outcome["status"], "SETTLED")
            self.assertEqual(outcome["exit_reason"], "TARGET")
            self.assertEqual(outcome["entry_session"], "2026-09-17")

    def test_repeated_same_daily_state_is_not_multiplied(self):
        evidence = evidence_snapshot()
        opp = opportunity_snapshot(evidence)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = freeze.ingest(opp, evidence, root=root, candidates_per_cycle=2)
            second = freeze.ingest(opp, evidence, root=root, candidates_per_cycle=2)
            self.assertEqual(first["events_written"], 2)
            self.assertEqual(second["events_written"], 0)
            self.assertEqual(second["events_skipped_daily_duplicate"], 2)


if __name__ == "__main__":
    unittest.main()
