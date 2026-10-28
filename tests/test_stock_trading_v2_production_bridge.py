import unittest
from datetime import datetime, timedelta, timezone

from scripts import stock_trading_portfolio as portfolio
from scripts import stock_trading_v2_production_bridge as bridge


class V2ProductionBridgeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "maximum_risk_percent": 0.07,
            "minimum_reward_risk": 1.5,
            "maximum_execution_quote_age_minutes": 2,
            "maximum_execution_quote_age_minutes_by_market": {"GPW": 20, "US": 2},
            "maximum_opportunity_age_minutes": 90,
            "require_regular_session_for_new_entry": True,
            "canary_max_open_positions_per_market": 1,
            "full_max_open_positions_per_market": 3,
            "auto_promote_canary": True,
            "healthy_market_sessions_required": 2,
            "healthy_sessions_per_market_required": 1,
            "full_cycle_max_new_positions_per_market": 3,
            "require_both_markets_before_full": True,
        }
        self.now = datetime(2026, 9, 17, 15, 0, tzinfo=timezone.utc)

    def quote(self, symbol, market, *, now_utc=None, maximum_age_seconds=None):
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
            "research_risk_plan": {"reference_price": 100.0, "risk_percent": risk, "reward_risk": 2.0},
        }


    def test_execution_quote_age_is_market_specific(self):
        self.assertEqual(1200, bridge._execution_quote_max_age_seconds("GPW", self.cfg))
        self.assertEqual(120, bridge._execution_quote_max_age_seconds("US", self.cfg))

    def test_actual_decision_clock_rechecks_cached_age_without_changing_market_limits(self):
        self.now = datetime(2026, 9, 17, 14, tzinfo=timezone.utc)
        policy = portfolio.load_policy()
        candidate = self.candidate("AAA", 1, 85)
        opportunity = {"generated_at": self.now.isoformat(), "decision": {"action": "BUY", "candidate": candidate}, "evaluated_candidates": [candidate]}
        for market, seconds, expected_open in (("US", 120, True), ("US", 121, False), ("US", 300, False), ("GPW", 300, True), ("GPW", 1201, False)):
            with self.subTest(market=market, delay=seconds):
                initial = portfolio.empty_state(self.now, policy)
                result, actions, _ = bridge.process_market(initial, market, opportunity, config=self.cfg, runtime={"phase": "FULL"},
                    now_utc=self.now, quote_fetcher=lambda *args, **kwargs: {**self.quote(*args, **kwargs), "capture_age_seconds": 0},
                    decision_clock=lambda: self.now + timedelta(seconds=seconds), production_policy=policy)
                self.assertEqual(int(expected_open), len(portfolio.open_positions(result, market)))
                if expected_open:
                    self.assertEqual(seconds, portfolio.open_positions(result, market)[0]["entry_validation"]["decision_age_seconds"])
                else:
                    self.assertTrue(any(action.get("reason") == "execution_quote_stale_at_decision" for action in actions))

    def test_actual_decision_clock_cannot_cross_regular_close_or_published_early_close(self):
        policy = portfolio.load_policy()
        cases = (
            ("US", "2026-09-17T19:59:30Z", "2026-09-17T20:00:00Z", "2026-09-17T13:30:00Z", "2026-09-17T20:00:00Z", False),
            ("US", "2026-11-27T17:59:30Z", "2026-11-27T18:00:30Z", "2026-11-27T14:30:00Z", "2026-11-27T18:00:00Z", False),
            ("GPW", "2026-09-17T14:59:30Z", "2026-09-17T15:00:00Z", "2026-09-17T07:00:00Z", "2026-09-17T15:05:00Z", False),
            ("US", "2026-11-27T17:58:00Z", "2026-11-27T17:59:59Z", "2026-11-27T14:30:00Z", "2026-11-27T18:00:00Z", True),
        )
        for market, observed, decided, opening, closing, admitted in cases:
            with self.subTest(market=market, decision_at=decided):
                self.now = bridge._parse_iso(observed)
                candidate = self.candidate("AAA", 1, 85)
                opportunity = {"generated_at": observed, "decision": {"action": "BUY", "candidate": candidate}, "evaluated_candidates": [candidate]}
                result, actions, _ = bridge.process_market(portfolio.empty_state(self.now, policy), market, opportunity,
                    config=self.cfg, runtime={"phase": "FULL"}, now_utc=self.now, production_policy=policy,
                    quote_fetcher=lambda *args, **kwargs: {**self.quote(*args, **kwargs), "capture_age_seconds": 0,
                        "session_periods": {"REGULAR": {"start": opening, "end": closing}}},
                    decision_clock=lambda: bridge._parse_iso(decided))
                self.assertEqual(int(admitted), len(portfolio.open_positions(result, market)))
                if not admitted:
                    self.assertTrue(any(action.get("reason") == "market_not_regular_at_decision" for action in actions))

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

    def test_full_phase_can_fill_all_three_slots_in_one_cycle(self):
        policy = portfolio.load_policy()
        state = portfolio.empty_state(now=self.now, policy=policy)
        candidates = [
            self.candidate("AAA", 1, 95),
            self.candidate("BBB", 2, 90),
            self.candidate("CCC", 3, 85),
        ]
        opportunity = {
            "generated_at": self.now.isoformat(),
            "cash": {"utility": 50.0, "minimum_new_position_edge_points": 5.0},
            "decision": {"action": "BUY", "candidate": candidates[0]},
            "evaluated_candidates": candidates,
        }
        state, audits, healthy = bridge.process_market(
            state,
            "US",
            opportunity,
            config=self.cfg,
            runtime={"phase": "FULL"},
            now_utc=self.now,
            quote_fetcher=self.quote,
        )
        self.assertTrue(healthy)
        self.assertEqual(3, len(portfolio.open_positions(state, "US")))
        self.assertEqual(3, sum(1 for row in audits if row.get("action") == "open"))

    def test_candidate_below_cash_edge_is_not_filled(self):
        policy = portfolio.load_policy()
        state = portfolio.empty_state(now=self.now, policy=policy)
        strong = self.candidate("AAA", 1, 90)
        weak = self.candidate("BBB", 2, 54)
        opportunity = {
            "generated_at": self.now.isoformat(),
            "cash": {"utility": 50.0, "minimum_new_position_edge_points": 5.0},
            "decision": {"action": "BUY", "candidate": strong},
            "evaluated_candidates": [strong, weak],
        }
        state, audits, _ = bridge.process_market(
            state,
            "US",
            opportunity,
            config=self.cfg,
            runtime={"phase": "FULL"},
            now_utc=self.now,
            quote_fetcher=self.quote,
        )
        self.assertEqual(["AAA"], [row["symbol"] for row in portfolio.open_positions(state, "US")])
        self.assertTrue(any(row.get("reason") == "candidate_does_not_beat_cash_edge" for row in audits))

    def test_bridge_passes_exact_gpw_delayed_paper_quote_limit(self):
        self.now = datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)
        policy = portfolio.load_policy()
        state = portfolio.empty_state(now=self.now, policy=policy)
        candidate = self.candidate("ASB.WA", 1, 95)
        seen = {}

        def quote(symbol, market, *, now_utc=None, maximum_age_seconds=None):
            seen["maximum_age_seconds"] = maximum_age_seconds
            return self.quote(symbol, market, now_utc=now_utc, maximum_age_seconds=maximum_age_seconds)

        cfg = {**self.cfg, "maximum_execution_quote_age_minutes": 2}
        opportunity = {
            "generated_at": self.now.isoformat(),
            "cash": {"utility": 50.0, "minimum_new_position_edge_points": 5.0},
            "decision": {"action": "BUY", "candidate": candidate},
            "evaluated_candidates": [candidate],
        }
        state, audits, healthy = bridge.process_market(
            state,
            "GPW",
            opportunity,
            config=cfg,
            runtime={"phase": "FULL"},
            now_utc=self.now,
            quote_fetcher=quote,
        )
        self.assertTrue(healthy)
        self.assertEqual(1200, seen["maximum_age_seconds"])
        self.assertEqual("ASB.WA", portfolio.open_positions(state, "GPW")[0]["symbol"])
        self.assertEqual("open", audits[0]["action"])

    def test_stale_execution_quote_is_ready_not_filled(self):
        policy = portfolio.load_policy()
        state = portfolio.empty_state(now=self.now, policy=policy)
        candidate = self.candidate("AAA", 1, 90)

        def stale_quote(symbol, market, *, now_utc=None, maximum_age_seconds=None):
            raise bridge.quotes.ExecutionQuoteUnavailable(
                "stale",
                diagnostics=[{"provider": "test", "capture_age_seconds": 900}],
            )

        opportunity = {
            "generated_at": self.now.isoformat(),
            "cash": {"utility": 50.0, "minimum_new_position_edge_points": 5.0},
            "decision": {"action": "BUY", "candidate": candidate},
            "evaluated_candidates": [candidate],
        }
        state, audits, healthy = bridge.process_market(
            state,
            "US",
            opportunity,
            config=self.cfg,
            runtime={"phase": "FULL"},
            now_utc=self.now,
            quote_fetcher=stale_quote,
        )
        self.assertFalse(healthy)
        self.assertEqual([], portfolio.open_positions(state, "US"))
        self.assertEqual("ready_waiting_fresh_quote", audits[0]["action"])


if __name__ == "__main__":
    unittest.main()
