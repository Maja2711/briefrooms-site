import unittest
from datetime import datetime, timezone

from scripts import stock_trading_portfolio as portfolio
from scripts import stock_trading_v2_production_bridge as bridge


class V2ProductionBridgeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "maximum_risk_percent": 0.07,
            "minimum_reward_risk": 1.5,
            "maximum_execution_quote_age_minutes": 20,
            "maximum_opportunity_age_minutes": 90,
            "require_regular_session_for_new_entry": True,
            "canary_max_open_positions_per_market": 1,
            "full_max_open_positions_per_market": 3,
            "auto_promote_canary": True,
            "healthy_market_sessions_required": 2,
            "require_both_markets_before_full": True,
        }
        self.now = datetime(2026, 9, 17, 15, 0, tzinfo=timezone.utc)

    def quote(self, symbol, market, *, now_utc=None):
        return {
            "price": 100.0,
            "market_state": "REGULAR",
            "capture_age_seconds": 30,
            "provider": "test",
            "observed_at": self.now.isoformat(),
            "received_at": self.now.isoformat(),
            "delay_status": "realtime",
            "is_realtime": True,
        }

    def candidate(self, symbol, rank, utility, risk=0.03):
        return {
            "symbol": symbol,
            "market_data_symbol": symbol,
            "name": symbol,
            "deep_rank": rank,
            "utility": utility,
            "eligible": True,
            "evidence_status": "COMPLETE",
            "research_risk_plan": {"risk_percent": risk, "reward_risk": 2.0},
        }

    def test_frontier_deduplicates_and_sorts(self):
        a = self.candidate("AAA", 1, 80)
        b = self.candidate("BBB", 2, 90)
        opportunity = {"decision": {"candidate": a}, "evaluated_candidates": [a, b]}
        rows = bridge.candidate_frontier(opportunity)
        self.assertEqual([row["symbol"] for row in rows], ["BBB", "AAA"])

    def test_rejected_first_candidate_does_not_stop_search(self):
        policy = portfolio.load_policy()
        state = portfolio.empty_state(now=self.now, policy=policy)
        bad = self.candidate("BAD", 1, 99, risk=0.20)
        good = self.candidate("GOOD", 2, 90, risk=0.03)
        opportunity = {
            "generated_at": self.now.isoformat(),
            "decision": {"action": "BUY", "candidate": bad},
            "evaluated_candidates": [bad, good],
        }
        runtime = {"phase": "CANARY"}
        state, audits, healthy = bridge.process_market(
            state,
            "US",
            opportunity,
            config=self.cfg,
            runtime=runtime,
            now_utc=self.now,
            quote_fetcher=self.quote,
        )
        self.assertTrue(healthy)
        self.assertEqual(len(portfolio.open_positions(state, "US")), 1)
        self.assertEqual(portfolio.open_positions(state, "US")[0]["symbol"], "GOOD")
        self.assertTrue(any(row.get("reason") == "research_risk_invalid" for row in audits))
        opened = next(row for row in audits if row.get("action") == "open")
        self.assertEqual(opened["opened_at"], portfolio.open_positions(state, "US")[0]["opened_at"])
        self.assertEqual(opened["entry_decision_at"], opened["opened_at"])
        self.assertEqual(
            opened["execution_provenance"]["source"],
            "stock_trading_v2_production_bridge",
        )

    def test_canary_capacity_is_one_position_per_market(self):
        policy = portfolio.load_policy()
        state = portfolio.empty_state(now=self.now, policy=policy)
        first = self.candidate("AAA", 1, 95)
        second = self.candidate("BBB", 2, 94)
        opportunity = {
            "generated_at": self.now.isoformat(),
            "decision": {"action": "BUY", "candidate": first},
            "evaluated_candidates": [first, second],
        }
        state, _, _ = bridge.process_market(
            state,
            "US",
            opportunity,
            config=self.cfg,
            runtime={"phase": "CANARY"},
            now_utc=self.now,
            quote_fetcher=self.quote,
        )
        self.assertEqual(len(portfolio.open_positions(state, "US")), 1)

    def test_canary_promotes_only_after_both_markets_are_healthy(self):
        runtime = {"phase": "CANARY", "healthy_market_sessions": []}
        bridge.maybe_promote(runtime, self.cfg, ["GPW:2026-09-17"], self.now)
        self.assertEqual(runtime["phase"], "CANARY")
        bridge.maybe_promote(runtime, self.cfg, ["GPW:2026-09-17", "US:2026-09-17"], self.now)
        self.assertEqual(runtime["phase"], "FULL")


if __name__ == "__main__":
    unittest.main()
