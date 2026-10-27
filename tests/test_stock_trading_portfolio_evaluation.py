import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import stock_trading_portfolio as portfolio
from scripts import stock_trading_portfolio_analytics as analytics
from scripts import stock_trading_v2_production_replay as replay
from scripts import stock_trading_v2_production_bridge as bridge
from scripts import stock_trading_v2_contracts as contracts

UTC = timezone.utc


def bridge_config():
    # Replay fixtures use an explicit frozen input; research intentionally
    # disables its default production bridge and must keep that restriction.
    return bridge.load_config(Path(__file__).parent / "fixtures" / "stock_trading_bridge_config_v1.json")


def policy():
    value = deepcopy(portfolio.load_policy())
    value["cash_utility"] = 50.0
    for market, cfg in value["markets"].items():
        cfg["minimum_entry_score"] = 0
        cfg["minimum_cash_edge_points"] = 5.0
        cfg["portfolio_risk"] = {"enabled": True, "max_aggregate_stop_loss_cash": 1050, "max_sector_positions": 2, "max_unknown_sector_positions": 3}
        cfg["transaction_costs"] = {"version": "explicit-test-costs-v1", "commission_bps_per_side": 1, "half_spread_bps_per_side": 2, "slippage_bps_per_side": 2, "fixed_fee_per_side": 0}
    return value


def candidate(symbol="AAA", *, sector="Technology", sourced=True, generated_at="2026-09-17T14:00:00Z"):
    selection = {"symbol": symbol, "ticker": symbol, "score": 85, "reference_price": 100.0, "stop": 95.0, "target": 110.0, "risk_percent": .05, "reward_risk": 2.0, "sector": sector, "market_snapshot": {"last": 100.0}}
    if sourced:
        selection["sector_provenance"] = {"source": "test frozen issuer metadata", "observed_at": "2026-09-17T13:00:00Z"}
    return {"date": "2026-09-17", "generated_at": generated_at, "decision": "TRADE", "selection": selection}


class PortfolioEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.policy = policy()
        self.now = datetime(2026, 9, 17, 14, tzinfo=UTC)
        self.state = portfolio.empty_state(self.now, self.policy)

    def admit(self, state, symbol="AAA", **kwargs):
        return portfolio.admit_candidate(state, "US", candidate(symbol, **kwargs), now=self.now, policy=self.policy, authority="v2_production_bridge")

    def test_stop_budget_uses_current_mark_and_blocks_without_mutation(self):
        state, _ = self.admit(self.state)
        state["markets"]["US"]["open_positions"][0]["last_mark"] = 120
        before = json.dumps(state, sort_keys=True)
        updated, action = self.admit(state, "BBB")
        self.assertEqual("portfolio_stop_loss_budget_exceeded", action["reason"])
        self.assertEqual(1, len(portfolio.open_positions(updated, "US")))
        self.assertEqual(before, json.dumps(state, sort_keys=True))

    def test_sector_limit_uses_only_sourced_metadata_unknown_has_separate_bound(self):
        state, first = self.admit(self.state)
        state, second = self.admit(state, "BBB")
        state, third = self.admit(state, "CCC")
        self.assertEqual("open", first["action"])
        self.assertEqual("open", second["action"])
        self.assertEqual("portfolio_sector_concentration_exceeded", third["reason"])
        self.policy["markets"]["US"]["portfolio_risk"]["max_unknown_sector_positions"] = 1
        state, _ = self.admit(self.state, sourced=False)
        state, action = self.admit(state, "BBB", sourced=False)
        self.assertEqual("portfolio_unknown_sector_limit_exceeded", action["reason"])
        self.assertEqual("UNKNOWN", action["candidate_sector"])

    def test_explicit_costs_on_both_legs_and_frozen_at_entry(self):
        state, _ = self.admit(self.state)
        position = portfolio.open_positions(state, "US")[0]
        self.assertEqual(2.5, position["entry_cost_amount"])
        self.policy["markets"]["US"]["transaction_costs"]["commission_bps_per_side"] = 999
        closed_state, _ = portfolio.close_position(state, "US", position["position_id"], now=self.now + timedelta(days=1), exit_price=110, reason="test")
        closed = closed_state["markets"]["US"]["closed_positions"][0]
        self.assertAlmostEqual(2.75, closed["exit_cost_amount"])
        self.assertAlmostEqual(494.75, closed["net_pnl_amount"])
        self.assertAlmostEqual(500, closed["pnl_amount"])
        self.assertEqual(1, closed["transaction_cost_model"]["commission_bps_per_side"])

    def test_legacy_closed_records_neither_backfilled_nor_truncated(self):
        history = [{"position_id": f"old-{index}", "status": "CLOSED", "entry": 10, "exit_price": 11, "arbitrary": {"original": index}} for index in range(301)]
        self.state["markets"]["US"]["closed_positions"] = deepcopy(history)
        state, _ = self.admit(self.state)
        pid = portfolio.open_positions(state, "US")[0]["position_id"]
        state, _ = portfolio.close_position(state, "US", pid, now=self.now + timedelta(hours=1), exit_price=99, reason="test")
        self.assertEqual(history, state["markets"]["US"]["closed_positions"][:301])
        self.assertEqual(302, len(state["markets"]["US"]["closed_positions"]))

    def test_missing_historical_costs_remain_unscorable(self):
        position = {"position_id": "old", "entry": 100, "exit_price": 110, "quantity": 50, "status": "CLOSED"}
        self.assertEqual("UNSCORABLE", analytics.position_economics(position)["status"])
        self.assertNotIn("transaction_cost_model", position)

    def test_analytics_drawdown_and_winner_dependency_include_open_equity(self):
        points = [{"net_nav": 15000}, {"net_nav": 16500}, {"net_nav": 14850, "benchmark_return_percent": 3}]
        result = analytics.curve_metrics(points, initial_capital=15000, positions=[{"position_id": "winner", "net_pnl_amount": 750}, {"position_id": "loser", "net_pnl_amount": -900}])
        self.assertAlmostEqual(10, result["max_drawdown_percent"])
        self.assertAlmostEqual(-1, result["net_return_percent"])
        self.assertEqual(-900, result["pnl_excluding_top_winner"])
        self.assertEqual(1, result["top_winner_share"])
        self.assertAlmostEqual(-4, result["excess_return_percent"])

    def frame(self, root, state, moment, observation=None, **kwargs):
        return replay.capture_frame(root, kind="portfolio_lifecycle", observed_at=moment, recorded_at=moment + timedelta(seconds=1), policy=self.policy, initial_state=state,
                                    observations={"US": observation or {}}, benchmark={"US": {"price": 100, "observed_at": moment.isoformat(), "provider": "frozen test benchmark"}}, **kwargs)["frame"]

    def observation(self, start, end, *, high=121, low=99, last=120):
        return {"AAA": {"snapshot": {"high": high, "low": low, "last": last, "observed_at": end.isoformat(), "trigger_window_start": start.isoformat(), "post_effective_only": True},
                        "closes": [50 + index for index in range(71)], "closes_as_of": end.isoformat(), "atr": 1.0}}

    def test_exact_replay_matches_production_profit_runner_and_later_stop(self):
        state, _ = self.admit(self.state)
        first = self.now + timedelta(hours=1)
        second = first + timedelta(minutes=5)
        obs1 = self.observation(self.now, first)
        expected, actions = portfolio.review_market(state, "US", observations=obs1, now=first.astimezone(portfolio.MARKET_TZ["US"]), policy=self.policy)
        self.assertEqual("hold_profit_runner", actions[0]["action"])
        new_stop = portfolio.open_positions(expected, "US")[0]["stop"]
        obs2 = self.observation(first, second, high=122, low=new_stop - .1, last=119)
        expected, actions2 = portfolio.review_market(expected, "US", observations=obs2, now=second.astimezone(portfolio.MARKET_TZ["US"]), policy=self.policy)
        self.assertEqual("stop_loss", actions2[0]["reason"])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            frames = [self.frame(Path(directory), state, first, obs1), self.frame(Path(directory), state, second, obs2)]
            result = replay.replay_portfolio(state, frames, policy=self.policy, bridge_config={})
        self.assertEqual("COMPLETE", result["status"], result["diagnostics"])
        self.assertEqual(expected, result["final_state"])
        self.assertEqual(2, len(result["curves_by_market"]["US"]))
        self.assertGreater(result["metrics_by_market"]["US"]["cost_amount"], 0)
        self.assertFalse(result["live_execution_claim"])

    def test_new_stop_cannot_use_an_earlier_low_or_future_thesis_closes(self):
        state, _ = self.admit(self.state)
        moment = self.now + timedelta(hours=1)
        state["markets"]["US"]["open_positions"][0]["risk_last_changed_at"] = moment.isoformat()
        obs = self.observation(self.now, moment + timedelta(minutes=5), low=1)
        obs["AAA"]["closes_as_of"] = (moment + timedelta(days=1)).isoformat()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            frame = self.frame(Path(directory), state, moment + timedelta(minutes=5), obs)
            result = replay.replay_portfolio(state, [frame], policy=self.policy, bridge_config={})
        self.assertEqual("PENDING", result["status"])
        self.assertEqual(1, len(portfolio.open_positions(result["final_state"], "US")))
        self.assertTrue(any(row["reason"] == "unverified_post_effective_trigger_window" for row in result["diagnostics"]))
        self.assertTrue(any(row["reason"] == "future_or_inverted_lifecycle_input" for row in result["diagnostics"]))

    def test_closed_session_missing_observation_reproduces_canonical_hold_without_pending_deadlock(self):
        state, _ = self.admit(self.state)
        closed = self.now.replace(hour=21)
        expected, _ = portfolio.review_market(state, "US", observations={}, now=closed.astimezone(portfolio.MARKET_TZ["US"]), policy=self.policy)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            frame = self.frame(Path(directory), state, closed, {})
            result = replay.replay_portfolio(state, [frame], policy=self.policy, bridge_config={})
        self.assertEqual("COMPLETE", result["status"], result["diagnostics"])
        self.assertEqual(expected, result["final_state"])
        self.assertEqual(1, len(portfolio.open_positions(result["final_state"], "US")))

    def test_capture_is_immutable_and_tampering_or_missing_mark_is_pending(self):
        state, _ = self.admit(self.state)
        moment = self.now + timedelta(hours=1)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            frame = self.frame(Path(directory), state, moment)
            repeat = self.frame(Path(directory), state, moment)
            self.assertEqual(frame, repeat)
            original_paths = list(Path(directory).rglob("*.json"))
            self.assertEqual(1, len(original_paths))
            unscorable = replay.replay_portfolio(state, [frame], policy=self.policy, bridge_config={})
            self.assertEqual("PENDING", unscorable["status"])
            frame["observations"]["US"]["AAA"] = self.observation(self.now, moment)["AAA"]
            broken = replay.replay_portfolio(state, [frame], policy=self.policy, bridge_config={})
            self.assertTrue(any(row["reason"] == "native_frame_receipt_missing_or_invalid" for row in broken["diagnostics"]))

    def opportunity(self, symbols, utility=85):
        rows = [{"symbol": symbol, "market_data_symbol": symbol, "name": symbol, "deep_rank": index + 1, "utility": utility,
                 "eligible": True, "evidence_status": "COMPLETE", "research_risk_plan": {"reference_price": 100, "stop": 97, "target": 106, "risk_percent": .03, "reward_risk": 2}} for index, symbol in enumerate(symbols)]
        value = {"schema_version": "stock-trading-v2-portfolio-opportunity-v1", "market": "US", "generated_at": self.now.isoformat(),
                 "cash": {"utility": 50, "minimum_new_position_edge_points": 5}, "decision": {"action": "CASH" if utility < 55 else "BUY", "candidate": rows[0]},
                 "evaluated_candidates": rows, "portfolio": {"max_open_positions": 3, "open_position_count": 0, "available_slots": 3},
                 "governance": {"production_decision_influence": False, "automatic_portfolio_admission": False, "automatic_position_replacement": False, "execution_ready": False}}
        value["opportunity_sha256"] = contracts.payload_sha256(value)
        return value

    def quote(self):
        return {"price": 100, "observed_at": self.now.isoformat(), "received_at": self.now.isoformat(), "decision_at": self.now.isoformat(), "provider": "frozen test", "market_state": "REGULAR", "capture_age_seconds": 0, "is_realtime": True, "delay_status": "realtime"}

    def raw_observation(self, moment, bars):
        return {"snapshot": portfolio.snapshot_from_source_bars(bars, "US", now=moment), "source_bars": bars,
                "closes": [50 + index for index in range(71)], "closes_as_of": moment.isoformat(), "atr": 1.0}

    def bar(self, start, receipt, *, high=101, low=99, close=100):
        return {"start": start.isoformat(), "observed_at": receipt.isoformat(), "high": high, "low": low, "close": close, "provider": "Yahoo"}

    def test_watch_receipts_are_bounded_immutable_and_never_admit_symbols(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "watches.json"
            first = replay.register_replay_watch(market="US", symbol="AAA", decision_at=self.now, root=path)
            original = path.read_bytes()
            repeat = replay.register_replay_watch(market="US", symbol="AAA", decision_at=self.now + timedelta(hours=1), root=path)
            self.assertEqual("EXISTING", repeat["status"])
            self.assertEqual(first["watch"], repeat["watch"])
            self.assertEqual(original, path.read_bytes())
            replay.register_replay_watch(market="US", symbol="BBB", decision_at=self.now, root=path)
            self.assertEqual("CAPACITY_REACHED", replay.register_replay_watch(market="US", symbol="CCC", decision_at=self.now, root=path)["status"])
            self.assertEqual(2, len(replay.active_replay_watches(now=self.now, root=path)))
            self.assertEqual([], replay.active_replay_watches(now=self.now + timedelta(hours=168), root=path))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["watches"][0]["symbol"] = "UNVERIFIED"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "watch receipt invalid"):
                replay.active_replay_watches(now=self.now, root=path)
        self.assertEqual([], portfolio.open_positions(self.state, "US"))

    def test_lifecycle_collector_freezes_real_and_watched_raw_bars_without_watch_trade_authority(self):
        state, _ = self.admit(self.state)
        moment = self.now + timedelta(minutes=10)
        raw = self.raw_observation(moment, [self.bar(self.now, moment), self.bar(self.now + timedelta(minutes=5), moment)])
        benchmark = {"US": {"price": 100, "observed_at": moment.isoformat(), "provider": "native test benchmark"}}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            path = root / "watches.json"
            replay.register_replay_watch(market="US", symbol="BBB", decision_at=self.now, root=path)
            with patch.object(portfolio, "_daily_observation", return_value=raw) as collector:
                updated, actions = portfolio.run_market(
                    state, "US", now=moment, policy=self.policy, capture_root=root / "frames", watch_path=path,
                    benchmark_fetcher=lambda now: (benchmark, {}),
                )
            frame = json.loads(next((root / "frames").rglob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(["AAA", "BBB"], [call.args[0] for call in collector.call_args_list])
        self.assertEqual({"AAA", "BBB"}, set(frame["observations"]["US"]))
        self.assertEqual(raw["source_bars"], frame["observations"]["US"]["BBB"]["source_bars"])
        self.assertEqual(["AAA"], [position["symbol"] for position in portfolio.open_positions(updated, "US")])
        self.assertTrue(any(action["action"] == "production_replay_inputs_frozen" for action in actions))

    def test_collector_with_only_counterfactual_watch_can_complete_both_books(self):
        alternative = deepcopy(self.policy)
        alternative["markets"]["US"]["minimum_cash_edge_points"] = 4.0
        moment = self.now + timedelta(minutes=10)
        # The 14:00 bar predates the virtual entry at 14:00:07; its low must
        # not close the virtual position, despite being in the raw session.
        entered = self.now + timedelta(seconds=7)
        raw = self.raw_observation(moment, [self.bar(self.now, moment, low=90), self.bar(self.now + timedelta(minutes=5), moment)])
        benchmark = {"US": {"price": 100, "observed_at": moment.isoformat(), "provider": "native test benchmark"}}
        quote = {**self.quote(), "decision_at": entered.isoformat()}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            path = root / "watches.json"
            replay.register_replay_watch(market="US", symbol="AAA", decision_at=entered, root=path)
            first = replay.capture_frame(root / "frames", kind="ADMISSION", observed_at=entered, decision_started_at=self.now, recorded_at=entered,
                    policy=self.policy, initial_state=self.state, opportunities={"US": self.opportunity(["AAA"], utility=54.5)}, quotes={"US": {"AAA": quote}},
                    benchmark={"US": {"price": 100, "observed_at": self.now.isoformat(), "provider": "native test benchmark"}})["frame"]
            with patch.object(portfolio, "_daily_observation", return_value=raw) as collector:
                canonical, _ = portfolio.run_market(self.state, "US", now=moment, policy=self.policy, capture_root=root / "lifecycle", watch_path=path, benchmark_fetcher=lambda now: (benchmark, {}))
            second = json.loads(next((root / "lifecycle").rglob("*.json")).read_text(encoding="utf-8"))
            result = replay.compare_policies(self.state, [first, second], champion_policy=self.policy, challenger_policy=alternative, bridge_config=bridge_config())
        self.assertEqual("COMPLETE", result["status"], result["challenger"]["diagnostics"])
        self.assertEqual([], portfolio.open_positions(canonical, "US"))
        self.assertEqual("AAA", collector.call_args.args[0])
        opened = portfolio.open_positions(result["challenger"]["final_state"], "US")[0]
        self.assertEqual(entered, datetime.fromisoformat(opened["opened_at"]))
        self.assertEqual([], result["challenger"]["final_state"]["markets"]["US"]["closed_positions"])

    def test_raw_bar_projection_uses_virtual_ratchet_and_rejects_future_receipts(self):
        state, _ = self.admit(self.state)
        position = portfolio.open_positions(state, "US")[0]
        moment = self.now + timedelta(minutes=20)
        bars = [self.bar(self.now, moment, low=90), self.bar(self.now + timedelta(minutes=15), moment, low=99)]
        raw = self.raw_observation(moment, bars)
        position["risk_last_changed_at"] = (self.now + timedelta(minutes=12)).isoformat()
        projected = replay.project_observation(position, raw, moment)
        self.assertEqual(99, projected["snapshot"]["low"])
        self.assertEqual(self.now + timedelta(minutes=15), datetime.fromisoformat(projected["snapshot"]["trigger_window_start"]))
        self.assertEqual(90, raw["snapshot"]["low"])
        bars[-1]["observed_at"] = (moment + timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "future_or_inverted_source_bar"):
            replay.project_observation(position, raw, moment)

    def test_execution_clock_is_quote_specific_even_when_frame_capture_finishes_later(self):
        decision = self.now + timedelta(seconds=3)
        frame_time = self.now + timedelta(minutes=4)
        quote = {**self.quote(), "decision_at": decision.isoformat()}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            frame = replay.capture_frame(Path(directory), kind="ADMISSION", observed_at=frame_time, decision_started_at=self.now, recorded_at=frame_time,
                    policy=self.policy, initial_state=self.state, opportunities={"US": self.opportunity(["AAA"])}, quotes={"US": {"AAA": quote}},
                    benchmark={"US": {"price": 100, "observed_at": self.now.isoformat(), "provider": "native test benchmark"}})["frame"]
            result = replay.replay_portfolio(self.state, [frame], policy=self.policy, bridge_config=bridge_config())
            self.assertEqual("COMPLETE", result["status"], result["diagnostics"])
            self.assertEqual(decision, datetime.fromisoformat(portfolio.open_positions(result["final_state"], "US")[0]["opened_at"]))
            bad_quote = {**quote, "decision_at": (self.now - timedelta(seconds=1)).isoformat()}
            invalid = replay.capture_frame(Path(directory), kind="ADMISSION", observed_at=frame_time, recorded_at=frame_time, policy=self.policy, initial_state=self.state,
                    opportunities={"US": self.opportunity(["AAA"])}, quotes={"US": {"AAA": bad_quote}}, benchmark=frame["benchmark"])["frame"]
            rejected = replay.replay_portfolio(self.state, [invalid], policy=self.policy, bridge_config=bridge_config())
        self.assertEqual("PENDING", rejected["status"])
        self.assertEqual([], portfolio.open_positions(rejected["final_state"], "US"))
        self.assertTrue(any(row["reason"] == "execution_quote_time_invalid" for row in rejected["diagnostics"]))

    def test_inherited_open_winner_uses_only_prospective_window_contribution(self):
        state, _ = self.admit(self.state)
        state["markets"]["US"]["open_positions"][0]["last_mark"] = 110
        moment = self.now + timedelta(minutes=5)
        observation = self.observation(self.now, moment, high=111, low=110, last=111)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            frame = self.frame(Path(directory), state, moment, observation)
            result = replay.replay_portfolio(state, [frame], policy=self.policy, bridge_config={})
        self.assertEqual("COMPLETE", result["status"], result["diagnostics"])
        metrics = result["metrics_by_market"]["US"]
        self.assertAlmostEqual(49.975, metrics["net_pnl_amount"])
        self.assertAlmostEqual(.025, metrics["cost_amount"])
        self.assertAlmostEqual(49.975, metrics["largest_winner_net_pnl_amount"])
        self.assertAlmostEqual(0, metrics["pnl_excluding_top_winner"])

    def test_replay_advances_actual_canary_health_before_next_frame(self):
        config = bridge_config()
        config.update(auto_promote_canary=True, require_both_markets_before_full=False, healthy_sessions_per_market_required=1)
        runtime = {"phase": "CANARY", "healthy_market_sessions": []}
        later = self.now + timedelta(minutes=5)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            frames = []
            for index, (symbol, moment) in enumerate((("AAA", self.now), ("BBB", later))):
                quote = {**self.quote(), **{key: moment.isoformat() for key in ("observed_at", "received_at", "decision_at")}}
                frames.append(replay.capture_frame(Path(directory), kind="ADMISSION", observed_at=moment, recorded_at=moment, policy=self.policy,
                        initial_state=self.state, runtime=runtime if index == 0 else {}, opportunities={"US": self.opportunity([symbol])}, quotes={"US": {symbol: quote}},
                        benchmark={"US": {"price": 100, "observed_at": moment.isoformat(), "provider": "native test benchmark"}})["frame"])
            result = replay.replay_portfolio(self.state, frames, policy=self.policy, bridge_config=config)
        self.assertEqual("COMPLETE", result["status"], result["diagnostics"])
        self.assertEqual("FULL", result["final_runtime"]["phase"])
        self.assertEqual(2, len(portfolio.open_positions(result["final_state"], "US")))

    def test_reentry_features_are_native_frozen_inputs_without_network_fallback(self):
        state, _ = self.admit(self.state)
        position = portfolio.open_positions(state, "US")[0]
        stopped = self.now + timedelta(minutes=1)
        state, _ = portfolio.close_position(state, "US", position["position_id"], now=stopped, exit_price=95, reason="stop_loss")
        moment = self.now + timedelta(minutes=30)
        quote = {**self.quote(), **{key: moment.isoformat() for key in ("observed_at", "received_at", "decision_at")}}
        frozen = {"evidence": {"US": {"AAA": {"evidence": [{"source": "native bound receipt"}], "features": {"volume_ratio_20d": 1.2}}}},
                  "features": {"US": {"AAA": {"drop_atr": 2, "rebound_atr": 1, "volume_ratio": 1.2}}}}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            kwargs = dict(kind="ADMISSION", observed_at=moment, recorded_at=moment, policy=self.policy, initial_state=state,
                          opportunities={"US": self.opportunity(["AAA"])}, quotes={"US": {"AAA": quote}},
                          benchmark={"US": {"price": 100, "observed_at": moment.isoformat(), "provider": "native test benchmark"}})
            frame = replay.capture_frame(Path(directory), reentry_inputs=frozen, **kwargs)["frame"]
            missing = replay.capture_frame(Path(directory), reentry_inputs={"evidence": frozen["evidence"]}, **kwargs)["frame"]
            with patch.object(bridge.reentry_learning, "quote_features", side_effect=AssertionError("network fallback forbidden")):
                result = replay.replay_portfolio(state, [frame], policy=self.policy, bridge_config=bridge_config())
                pending = replay.replay_portfolio(state, [missing], policy=self.policy, bridge_config=bridge_config())
        self.assertEqual("COMPLETE", result["status"], result["diagnostics"])
        self.assertEqual("PENDING", pending["status"])
        self.assertTrue(any(row["reason"] == "native_reentry_features_missing" for row in pending["diagnostics"]))

    def test_replay_enforces_whole_book_cap_and_missing_execution_is_pending(self):
        opportunity = self.opportunity(["AAA", "BBB", "CCC", "DDD"])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            frame = replay.capture_frame(Path(directory), kind="ADMISSION", observed_at=self.now, recorded_at=self.now,
                    policy=self.policy, initial_state=self.state, opportunities={"US": opportunity}, quotes={"US": {symbol: self.quote() for symbol in ("AAA", "BBB", "CCC", "DDD")}},
                    benchmark={"US": {"price": 100, "observed_at": self.now.isoformat(), "provider": "frozen benchmark"}})["frame"]
            result = replay.replay_portfolio(self.state, [frame], policy=self.policy, bridge_config=bridge_config())
            self.assertEqual("COMPLETE", result["status"], result["diagnostics"])
            self.assertEqual(3, len(portfolio.open_positions(result["final_state"], "US")))
            missing = replay.capture_frame(Path(directory), kind="ADMISSION", observed_at=self.now, recorded_at=self.now,
                    policy=self.policy, initial_state=self.state, opportunities={"US": self.opportunity(["AAA"])},
                    benchmark={"US": {"price": 100, "observed_at": self.now.isoformat(), "provider": "frozen benchmark"}})["frame"]
            unscorable = replay.replay_portfolio(self.state, [missing], policy=self.policy, bridge_config=bridge_config())
            self.assertEqual("PENDING", unscorable["status"])
            self.assertEqual([], portfolio.open_positions(unscorable["final_state"], "US"))
            self.assertTrue(any(row["reason"] == "exact_execution_quote_missing" for row in unscorable["diagnostics"]))

    def test_paired_actual_cash_margin_policies_replay_same_quote_tape_after_costs(self):
        alternative = deepcopy(self.policy)
        alternative["markets"]["US"]["minimum_cash_edge_points"] = 4.0
        moment = self.now + timedelta(minutes=5)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            first = replay.capture_frame(Path(directory), kind="ADMISSION", observed_at=self.now, recorded_at=self.now,
                    policy=self.policy, initial_state=self.state, opportunities={"US": self.opportunity(["AAA"], utility=54.5)}, quotes={"US": {"AAA": self.quote()}},
                    benchmark={"US": {"price": 100, "observed_at": self.now.isoformat(), "provider": "frozen benchmark"}})["frame"]
            second = self.frame(Path(directory), self.state, moment, self.observation(self.now, moment, high=101, low=96.9, last=98))
            result = replay.compare_policies(self.state, [first, second], champion_policy=self.policy, challenger_policy=alternative, bridge_config=bridge_config())
        self.assertEqual("COMPLETE", result["status"], {name: result[name]["diagnostics"] for name in ("champion", "challenger")})
        market = result["market_results"]["US"]
        self.assertEqual(0, market["champion"]["net_pnl_amount"])
        self.assertLess(market["challenger"]["net_pnl_amount"], -150)
        self.assertLess(market["net_delta_percent"], -1)
        self.assertEqual(2, len(market["paired_increments"]))
        self.assertEqual(1, len(result["challenger"]["final_state"]["markets"]["US"]["closed_positions"]))

    def test_monitor_predeclared_future_window_waits_then_recommends_rollback(self):
        end = self.now + timedelta(days=30)
        identity = {"deployment_id": "version-1", "policy_sha256": "p", "cost_model_sha256": "c", "market": "US"}
        deployment = {**identity, "activated_at": self.now.isoformat(), "monitoring_contract": {"frozen_at": self.now.isoformat(), "window_end_at": end.isoformat(), "initial_capital": 15000, "minimum_paired_observations": 2, "rollback_max_drawdown_percent": 5, "rollback_min_net_delta_percent": -1}}
        observations = [{**identity, "observed_at": (self.now + timedelta(days=offset)).isoformat(), "status": "COMPLETE", "net_nav": nav, "control_net_nav": 15000} for offset, nav in ((1, 15000), (29, 14600))]
        early = analytics.deployment_economic_monitor(deployment, observations, as_of=end - timedelta(hours=1))
        self.assertEqual("CONTINUE_MONITORING", early["recommendation"])
        final = analytics.deployment_economic_monitor(deployment, observations, as_of=end)
        self.assertEqual("RECOMMEND_ROLLBACK", final["recommendation"])
        self.assertEqual("fixed_window_net_underperformance", final["reason"])
        self.assertFalse(final["production_mutated"])
        deployment["monitoring_contract"]["frozen_at"] = end.isoformat()
        self.assertEqual("UNSCORABLE", analytics.deployment_economic_monitor(deployment, observations, as_of=end)["status"])


if __name__ == "__main__":
    unittest.main()
