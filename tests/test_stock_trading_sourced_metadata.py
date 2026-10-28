import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import stock_trading_portfolio as portfolio
from scripts import stock_trading_portfolio_analytics as analytics
from scripts import stock_trading_quote_enricher as quotes
from scripts import stock_trading_sourced_metadata as sourced
from scripts import stock_trading_v2_production_bridge as bridge
from scripts import stock_trading_v2_production_replay as replay
from scripts import stock_trading_v2_reentry_learning as reentry
from scripts import stock_trading_session_calendar as calendar


class SourcedMetadataTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 15, tzinfo=timezone.utc)
        self.policy = portfolio.load_policy()
        self.state = portfolio.empty_state(self.now, self.policy)
        self.position = {"position_id": "original", "market": "US", "symbol": "SAP", "name": "SAP SE ADS",
                         "entry": 100, "last_mark": 100, "stop": 95, "quantity": 50, "status": "OPEN", "sector": None}
        self.state["markets"]["US"]["open_positions"] = [deepcopy(self.position)]
        self.state["markets"]["US"]["closed_positions"] = [{"position_id": "immutable", "arbitrary": {"source": "original"}}]
        self.universes = {"US": {"instruments": [{"market": "US", "listing_symbol": "SAP", "market_data_symbol": "SAP",
                             "name": "SAP SE ADS", "exchange": "NYSE", "source": {"provider": "Nasdaq Trader"}}]}}
        self.fetcher = lambda **kwargs: ([{"symbol": "SAP", "name": "SAP SE American Depositary Shares", "sector": "Technology"}], {})

    def test_two_runs_append_separately_and_preserve_history_bytes_and_legacy_costs(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            history = root / "portfolio.json"
            original = json.dumps(self.state, ensure_ascii=False, indent=4).encode()
            history.write_bytes(original)
            ledger = sourced.empty_ledger()
            first = sourced.collect(json.loads(history.read_text()), self.universes, ledger, now=self.now, nasdaq_fetcher=self.fetcher)
            sourced._atomic(root / "metadata.json", ledger)
            receipt_bytes = (root / "metadata.json").read_bytes()
            with patch.object(sourced.fast_scan, "fetch_nasdaq_screener_rows", side_effect=AssertionError("fresh observation must be reused")):
                second = sourced.collect(json.loads(history.read_text()), self.universes, ledger, now=self.now + timedelta(hours=1))
            sourced._atomic(root / "metadata.json", ledger)
            self.assertEqual(original, history.read_bytes())
            self.assertEqual(receipt_bytes, (root / "metadata.json").read_bytes())
            self.assertEqual(1, len(sourced.load_ledger(root / "metadata.json")["observations"]))
        self.assertEqual("Technology", first["markets"]["US"][0]["sector"])
        self.assertEqual("Technology", second["markets"]["US"][0]["sector"])
        self.assertEqual("UNSCORABLE_MISSING_FROZEN_HISTORICAL_COSTS", second["markets"]["US"][0]["historical_net_cost_status"])
        self.assertIsNone(second["markets"]["US"][0]["frozen_paper_cost_model"])
        self.assertEqual("UNSCORABLE", analytics.position_economics(self.position)["status"])
        self.assertEqual("UNAVAILABLE", second["markets"]["US"][0]["actual_broker_execution_costs"]["status"])

    def ledger(self):
        value = sourced.empty_ledger()
        sourced.collect(self.state, self.universes, value, now=self.now, nasdaq_fetcher=self.fetcher)
        return value

    def test_wrong_issuer_wrong_market_stale_future_and_tampered_metadata_are_not_used(self):
        good = self.ledger()
        for mutation in (
            lambda body: body["identity"].update(name="Unrelated Mining Corporation"),
            lambda body: body.update(market="GPW"),
            lambda body: body.update(observed_at=(self.now - timedelta(days=31)).isoformat()),
            lambda body: body.update(received_at=(self.now + timedelta(seconds=1)).isoformat()),
            lambda body: body["source"].update(url="https://untrusted.example/sector"),
        ):
            body = deepcopy(good["observations"][0]); body.pop("observation_id")
            mutation(body)
            invalid = sourced.empty_ledger(); sourced.append_observation(invalid, body)
            self.assertIsNone(sourced.sector_observation(self.position, "US", invalid, now=self.now))
        good["observations"][0]["sector"] = "Changed after receipt"
        self.assertIsNone(sourced.sector_observation(self.position, "US", good, now=self.now))

    def test_live_receipt_uses_fetch_completion_not_run_start(self):
        finished = self.now + timedelta(seconds=17)
        ledger = sourced.empty_ledger()
        sourced.collect(self.state, self.universes, ledger, now=self.now, nasdaq_fetcher=self.fetcher, clock=lambda: finished)
        self.assertEqual(finished.isoformat(), ledger["observations"][0]["received_at"])
        self.assertIsNone(sourced.sector_observation(self.position, "US", ledger, now=self.now))
        self.assertIsNotNone(sourced.sector_observation(self.position, "US", ledger, now=finished))

    def test_current_sector_changes_risk_view_without_rewriting_old_records(self):
        ledger = self.ledger()
        other = {**self.position, "position_id": "other", "symbol": "MCK", "name": "McKesson Corporation"}
        self.state["markets"]["US"]["open_positions"].append(other)
        body = deepcopy(ledger["observations"][0]); body.pop("observation_id")
        body.update(symbol="MCK", identity={**body["identity"], "symbol": "MCK", "market_data_symbol": "MCK", "listing_symbol": "MCK", "name": other["name"]})
        sourced.append_observation(ledger, body)
        selection = {"symbol": "NEW", "name": "New Technology", "sector": "Technology", "sector_provenance": {"source": "frozen candidate evidence", "observed_at": self.now.isoformat()}, "market_snapshot": {"last": 100}, "stop": 99}
        original = json.dumps(self.state, sort_keys=True)
        risk = portfolio.portfolio_admission_risk(self.state, "US", selection, policy=self.policy, now=self.now, risk_metadata=ledger)
        self.assertEqual("portfolio_sector_concentration_exceeded", risk["reason"])
        self.assertEqual({"technology": 2}, risk["sector_positions"])
        self.assertEqual(2, len(risk["current_sector_observation_ids"]))
        self.assertEqual("CURRENT_PROSPECTIVE_EXIT_STRESS_NOT_HISTORICAL_COST", risk["remaining_exit_cost_basis"]["original"])
        self.assertGreater(risk["aggregate_stop_loss_cash"], 500)
        self.assertEqual(original, json.dumps(self.state, sort_keys=True))

    def test_official_gpw_sector_requires_both_headline_and_financial_identity(self):
        page = '<h1 id="setH1">LPP (PLLPP0000011)</h1><a data-href="https://www.gpw.pl/ajaxindex.php?action=GPWListaSp&amp;code=LPP&amp;isin=PLLPP0000011&amp;sektor=odzież%20i%20obuwie">Dane</a>'
        self.assertEqual("odzież i obuwie", sourced.gpw_sector(page, ticker="LPP", isin="PLLPP0000011"))
        for broken in (page.replace("LPP (", "OPL ("), page.replace("code=LPP", "code=OPL"), page.replace("isin=PLLPP0000011", "isin=PLTLKPL00017")):
            with self.assertRaises(ValueError):
                sourced.gpw_sector(broken, ticker="LPP", isin="PLLPP0000011")
        orange = '<h1 id="setH1">ORANGEPL (PLTLKPL00017)</h1><a data-href="https://www.gpw.pl/ajaxindex.php?action=GPWListaSp&amp;code=ORANGEPL&amp;isin=PLTLKPL00017&amp;sektor=telekomunikacja">Dane</a>'
        self.assertEqual("telekomunikacja", sourced.gpw_sector(orange, ticker="OPL", isin="PLTLKPL00017"))
        with self.assertRaises(ValueError):
            sourced.gpw_sector(orange.replace("code=ORANGEPL", "code=OTHER"), ticker="OPL", isin="PLTLKPL00017")

    def test_spread_uses_explicit_bbo_not_last_trade_or_paper_assumptions(self):
        meta = {"symbol": "SAP", "bid": 100, "ask": 100.2, "bidAskTime": int(self.now.timestamp())}
        out = quotes.spread_observation(meta, "SAP", "US", now_utc=self.now)
        self.assertEqual("OBSERVED", out["status"])
        self.assertAlmostEqual(19.98002, out["spread_bps"], places=5)
        for change, status in (({"ask": 99}, "INVALID"), ({"bid": -1}, "INVALID"), ({"symbol": "OTHER"}, "INVALID"),
                               ({"bidAskTime": int((self.now + timedelta(seconds=1)).timestamp())}, "INVALID"),
                               ({"bidAskTime": int((self.now - timedelta(seconds=301)).timestamp())}, "STALE")):
            self.assertEqual(status, quotes.spread_observation({**meta, **change}, "SAP", "US", now_utc=self.now)["status"])
        meta.pop("bidAskTime"); meta["regularMarketTime"] = int(self.now.timestamp())
        self.assertEqual("UNAVAILABLE", quotes.spread_observation(meta, "SAP", "US", now_utc=self.now)["status"])
        position = {**self.position, "current_mark": {"spread_observation": out}}
        self.assertEqual("STALE", sourced.current_spread(position, "US", now=self.now + timedelta(minutes=6))["status"])

    def chart(self, symbol="SAP"):
        starts = [self.now - timedelta(minutes=offset) for offset in (60, 45, 30, 15, 0)]
        return {"meta": {"symbol": symbol, "currency": "USD"}, "timestamp": [int(t.timestamp()) for t in starts],
                "indicators": {"quote": [{"low": [94, 92, 93, 94, 1], "close": [95, 95, 95, 95, 1]}]}}

    def test_frozen_raw_chart_derives_virtual_stop_features_and_rejects_future_or_wrong_issuer(self):
        chart = reentry.freeze_chart("US", "SAP", self.chart(), received_at=self.now)
        stop = {"closed_at": (self.now - timedelta(minutes=50)).isoformat(), "exit_price": 100}
        with patch.object(portfolio, "_chart", side_effect=AssertionError("replay cannot fetch")):
            result = reentry.features_from_chart(chart, "US", "SAP", stop, 95, self.now.isoformat(), .02, 1.2, decision_at=self.now)
        self.assertAlmostEqual((95 - 92) / 1.9, result["rebound_atr"], places=5)
        self.assertEqual(4, len(chart["chart"]["timestamp"]))
        for broken in ({**chart, "symbol": "OTHER"}, {**chart, "received_at": (self.now + timedelta(seconds=1)).isoformat()}, {**chart, "chart_sha256": "invalid"}):
            with self.assertRaises(ValueError):
                reentry.features_from_chart(broken, "US", "SAP", stop, 95, self.now.isoformat(), .02, 1.2, decision_at=self.now)

    def test_bridge_collects_watched_and_near_cash_raw_inputs_without_real_stop(self):
        state = portfolio.empty_state(self.now, self.policy)
        candidate = {"symbol": "SAP", "market_data_symbol": "SAP", "name": "SAP SE", "utility": 54.5,
                     "eligible": True, "evidence_status": "COMPLETE", "research_risk_plan": {"reference_price": 100, "risk_percent": .03, "reward_risk": 2}}
        opportunity = {"generated_at": self.now.isoformat(), "decision": {"action": "CASH", "candidate": candidate}, "evaluated_candidates": [candidate]}
        cfg = bridge.load_config(Path(__file__).parent / "fixtures/stock_trading_bridge_config_v1.json")
        quote = {"price": 100, "observed_at": self.now.isoformat(), "received_at": self.now.isoformat(), "market_state": "REGULAR", "capture_age_seconds": 0}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory); path = root / "opportunity.json"; path.write_text(json.dumps(opportunity))
            with patch.object(bridge, "load_config", return_value=cfg), patch.object(bridge, "load_runtime", return_value={"phase": "FULL"}), \
                 patch.object(portfolio, "load_state", return_value=state), patch.object(portfolio, "save_state"), patch.object(bridge, "_atomic"), \
                 patch.object(sourced, "load_ledger", return_value=sourced.empty_ledger()), patch.object(bridge, "_capture_benchmarks", return_value=({}, {})), \
                 patch.object(replay, "active_replay_watches", side_effect=lambda **kwargs: [{"symbol": "NXPI"}] if kwargs["market"] == "US" else []), \
                 patch.object(replay, "register_replay_watch"), patch.object(quotes, "execution_quote_for_symbol", return_value=quote), \
                 patch.object(portfolio, "_chart", side_effect=lambda symbol, **kwargs: self.chart(symbol)) as fetch:
                calendar = {"US": {"calendar_id": "sourced-test-receipt"}}
                result = bridge.run({"US": path}, now_utc=self.now, capture_root=root / "frames", session_calendar=calendar)
            frame = json.loads(next((root / "frames").rglob("*.json")).read_text())
        self.assertEqual({"SAP", "NXPI"}, {call.args[0] for call in fetch.call_args_list})
        self.assertEqual(2, fetch.call_count)
        self.assertEqual({"SAP", "NXPI"}, set(frame["reentry_inputs"]["charts"]["US"]))
        self.assertEqual(calendar, frame["session_calendar"])
        self.assertEqual([], portfolio.open_positions(frame["initial_state"], "US"))
        self.assertTrue(any(row["reason"] == "candidate_does_not_beat_cash_edge" for row in result["actions"]))

    def test_actual_replay_uses_raw_chart_for_virtual_only_stop_without_real_stop_features(self):
        real_book = portfolio.empty_state(self.now, self.policy)
        virtual_book = deepcopy(real_book)
        virtual_book["markets"]["US"]["closed_positions"] = [{
            "position_id": "virtual-only-stop", "symbol": "SAP", "status": "CLOSED",
            "closed_at": (self.now - timedelta(minutes=50)).isoformat(), "exit_reason": "stop_loss", "exit_price": 100,
        }]
        candidate = {"symbol": "SAP", "market_data_symbol": "SAP", "name": "SAP SE", "utility": 85,
                     "eligible": True, "evidence_status": "COMPLETE", "research_risk_plan": {
                         "reference_price": 95, "stop": 92.15, "target": 100.7, "risk_percent": .03, "reward_risk": 2, "atr_fraction": .02}}
        opportunity = {"generated_at": self.now.isoformat(), "decision": {"action": "BUY", "candidate": candidate}, "evaluated_candidates": [candidate]}
        decided = self.now + timedelta(seconds=1)
        quote = {"price": 95, "observed_at": decided.isoformat(), "received_at": decided.isoformat(), "decision_at": decided.isoformat(),
                 "market_state": "REGULAR", "capture_age_seconds": 0, "provider": "frozen quote", "is_realtime": True}
        raw = reentry.freeze_chart("US", "SAP", self.chart(), received_at=self.now)
        evidence = {"US": {"SAP": {"evidence": [{"source": "native bound receipt"}], "features": {"volume_ratio_20d": 1.2}}}}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            kwargs = dict(kind="ADMISSION", observed_at=decided, recorded_at=decided, policy=self.policy, initial_state=real_book,
                          opportunities={"US": opportunity}, quotes={"US": {"SAP": quote}},
                          benchmark={"US": {"price": 100, "observed_at": decided.isoformat(), "provider": "native benchmark"}})
            complete = replay.capture_frame(Path(directory), reentry_inputs={"evidence": evidence, "charts": {"US": {"SAP": raw}}}, **kwargs)["frame"]
            absent = replay.capture_frame(Path(directory), reentry_inputs={"evidence": evidence}, **kwargs)["frame"]
            cfg = bridge.load_config(Path(__file__).parent / "fixtures/stock_trading_bridge_config_v1.json")
            with patch.object(portfolio, "_chart", side_effect=AssertionError("replay network forbidden")):
                result = replay.replay_portfolio(virtual_book, [complete], policy=self.policy, bridge_config=cfg, markets=["US"])
                pending = replay.replay_portfolio(virtual_book, [absent], policy=self.policy, bridge_config=cfg, markets=["US"])
        self.assertEqual("COMPLETE", result["status"], result["diagnostics"])
        self.assertEqual("PENDING", pending["status"])
        self.assertTrue(any(row["reason"] == "native_reentry_features_missing" for row in pending["diagnostics"]))

    def test_prospective_legacy_economics_uses_frozen_mark_and_future_cost_reserve_without_history_backfill(self):
        # Large pre-registration profit must not count toward this trial.
        initial = deepcopy(self.state)
        initial["markets"]["US"]["open_positions"][0]["entry"] = 1
        original = json.dumps(initial, sort_keys=True)
        future = deepcopy(initial)
        future["markets"]["US"]["open_positions"][0]["last_mark"] = 110
        model = self.policy["markets"]["US"]["transaction_costs"]
        reserve_change = portfolio.transaction_cost(5500, model) - portfolio.transaction_cost(5000, model)
        result = analytics.prospective_period_economics(future, initial, "US", policy=self.policy, initial_capital=15000)
        self.assertEqual("COMPLETE", result["status"])
        self.assertAlmostEqual(500 - reserve_change, result["net_pnl_amount"])
        self.assertNotIn("entry_cost_amount", result["positions"][0])
        self.assertEqual("OUTSIDE_PROSPECTIVE_PERIOD_NOT_ESTIMATED", result["positions"][0]["historical_entry_cost_status"])
        self.assertEqual("UNSCORABLE", analytics.market_economics(future, "US", initial_capital=15000)["status"])
        closed = future["markets"]["US"]["open_positions"].pop()
        closed.update(status="CLOSED", exit_price=110)
        future["markets"]["US"]["closed_positions"].append(closed)
        closed_result = analytics.prospective_period_economics(future, initial, "US", policy=self.policy, initial_capital=15000)
        self.assertAlmostEqual(result["net_pnl_amount"], closed_result["net_pnl_amount"])
        self.assertEqual(original, json.dumps(initial, sort_keys=True))
        missing = deepcopy(initial); missing["markets"]["US"]["open_positions"][0].pop("last_mark")
        self.assertEqual("UNSCORABLE", analytics.prospective_period_economics(future, missing, "US", policy=self.policy, initial_capital=15000)["status"])
        new = {**self.position, "position_id": "opened-after-registration", "last_mark": 110,
               "transaction_cost_model": model, "entry_cost_amount": portfolio.transaction_cost(5000, model)}
        future["markets"]["US"]["open_positions"].append(new)
        combined = analytics.prospective_period_economics(future, initial, "US", policy=self.policy, initial_capital=15000)
        self.assertAlmostEqual(result["net_pnl_amount"] + analytics.position_economics(new)["net_pnl_amount"], combined["net_pnl_amount"])
        new.pop("entry_cost_amount")
        self.assertEqual("UNSCORABLE", analytics.prospective_period_economics(future, initial, "US", policy=self.policy, initial_capital=15000)["status"])

    def test_native_replay_requires_explicit_prospective_basis_for_legacy_initial_book(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            frame = replay.capture_frame(Path(directory), kind="ADMISSION", observed_at=self.now, recorded_at=self.now,
                    policy=self.policy, initial_state=self.state, benchmark={"US": {
                        "price": 100, "observed_at": self.now.isoformat(), "provider": "frozen benchmark"}})["frame"]
            strict = replay.replay_portfolio(self.state, [frame], policy=self.policy, bridge_config={}, markets=["US"])
            future = replay.replay_portfolio(self.state, [frame], policy=self.policy, bridge_config={}, markets=["US"], prospective_initial_basis=True)
        self.assertEqual("PENDING", strict["status"])
        self.assertEqual("COMPLETE", future["status"], future["diagnostics"])
        self.assertEqual(0, future["metrics_by_market"]["US"]["net_pnl_amount"])
        self.assertEqual(0, future["period_position_contributions_by_market"]["US"][0]["net_pnl_amount"])
        self.assertEqual(self.state, future["final_state"])

    def test_long_watch_retention_preserves_legacy_receipt_and_bounds_active_collection(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "watch.json"
            body = {"market": "US", "symbol": "OLD", "decision_at": self.now.isoformat(),
                    "expires_at": (self.now + timedelta(hours=168)).isoformat(), "purpose": "FUTURE_LIFECYCLE_INPUT_COLLECTION_ONLY"}
            legacy = {**body, "watch_id": replay.payload_sha256(body)}
            sourced._atomic(path, {"schema_version": "stock-trading-v2-replay-watchlist-v1", "watches": [legacy]})
            original = path.read_bytes()
            same = replay.register_replay_watch(market="US", symbol="OLD", decision_at=self.now + timedelta(hours=1), root=path)
            self.assertEqual("REGISTERED", same["status"])
            self.assertEqual(legacy, json.loads(path.read_text())["watches"][0])
            future_bytes = path.read_bytes()
            repeated = replay.register_replay_watch(market="US", symbol="OLD", decision_at=self.now + timedelta(hours=2), root=path)
            self.assertEqual("EXISTING", repeated["status"])
            self.assertEqual(future_bytes, path.read_bytes())
            new = replay.register_replay_watch(market="US", symbol="NEW", decision_at=self.now, root=path)["watch"]
            self.assertEqual(timedelta(days=180), replay._time(new["expires_at"]) - replay._time(new["decision_at"]))
            self.assertEqual(["OLD", "NEW"], [row["symbol"] for row in replay.active_replay_watches(now=self.now + timedelta(days=30), root=path)])
            payload = json.loads(path.read_text())
            self.assertEqual(legacy, payload["watches"][0])
            payload["watches"] = []
            for index in range(40):
                record = {**new, "symbol": f"T{index:02}"}; record.pop("watch_id")
                payload["watches"].append({**record, "watch_id": replay.payload_sha256(record)})
            sourced._atomic(path, payload)
            bounded = path.read_bytes()
            result = replay.register_replay_watch(market="US", symbol="OVER", decision_at=self.now, root=path)
            self.assertEqual("ACTIVE_CAPACITY_REACHED", result["status"])
            self.assertEqual(bounded, path.read_bytes())

    def test_aged_watch_appends_future_subscription_without_modifying_original(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "watch.json"
            first = replay.register_replay_watch(market="US", symbol="SAP", decision_at=self.now, root=path)["watch"]
            renewed_at = self.now + timedelta(days=120)
            second = replay.register_replay_watch(market="US", symbol="SAP", decision_at=renewed_at, root=path)
            self.assertEqual("REGISTERED", second["status"])
            self.assertEqual(first, json.loads(path.read_text())["watches"][0])
            self.assertEqual(2, len(replay.active_replay_watches(now=renewed_at, root=path)))
            stable = path.read_bytes()
            repeated = replay.register_replay_watch(market="US", symbol="SAP", decision_at=renewed_at + timedelta(hours=1), root=path)
            self.assertEqual("EXISTING", repeated["status"])
            self.assertEqual(stable, path.read_bytes())

    def test_official_listing_alias_is_distinct_from_quote_symbol(self):
        position = {**self.position, "symbol": "BRK-B", "ticker": "BRK.B", "name": "Berkshire Hathaway Inc. New Common Stock"}
        state = deepcopy(self.state); state["markets"]["US"]["open_positions"] = [position]
        universe = {"US": {"instruments": [{"market": "US", "listing_symbol": "BRK.B", "market_data_symbol": "BRK-B",
            "name": position["name"], "exchange": "NYSE", "source": {"provider": "Nasdaq Trader"}}]}}
        ledger = sourced.empty_ledger()
        report = sourced.collect(state, universe, ledger, now=self.now, nasdaq_fetcher=lambda **kwargs: (
            [{"symbol": "BRK.B", "name": position["name"], "sector": "Finance"}], {}))
        self.assertEqual([], report["errors"])
        self.assertEqual("BRK.B", ledger["observations"][0]["identity"]["listing_symbol"])
        self.assertEqual("BRK-B", ledger["observations"][0]["identity"]["market_data_symbol"])
        self.assertEqual("Finance", sourced.risk_sector(position, "US", ledger, now=self.now)["sector"])

    def test_frozen_anchor_calendar_handles_holiday_and_early_close_without_relaxing_real_session_gates(self):
        start = datetime(2026, 11, 25, 16, tzinfo=timezone.utc)
        frozen = calendar.build_receipt("US", now=start - timedelta(hours=1))
        state = deepcopy(self.state)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            def frame(at, *, observation=False, provider=None, later_calendar=None):
                return replay.capture_frame(root, kind="LIFECYCLE" if observation else "ADMISSION", observed_at=at, recorded_at=at,
                    policy=self.policy, initial_state=state, observations={"US": {}} if observation else {},
                    benchmark={"US": {"price": 100, "observed_at": at.isoformat(), "provider": "frozen benchmark"}},
                    session_calendar={"US": later_calendar or frozen}, market_sessions={"US": provider} if provider else {})["frame"]
            anchor = frame(start)
            holiday = frame(datetime(2026, 11, 26, 16, tzinfo=timezone.utc), observation=True)
            early = datetime(2026, 11, 27, 19, tzinfo=timezone.utc)
            session = {"date": "2026-11-27", "regular_start": "2026-11-27T14:30:00Z", "regular_end": "2026-11-27T18:00:00Z",
                       "source": "provider.currentTradingPeriod", "provider": "Yahoo", "closed": True, "state": "POST", "received_at": early.isoformat()}
            after_early = frame(early, observation=True, provider=session)
            result = replay.replay_portfolio(state, [anchor, holiday, after_early], policy=self.policy, bridge_config={}, markets=["US"], prospective_initial_basis=True)
            regular = frame(datetime(2026, 11, 27, 16, tzinfo=timezone.utc), observation=True)
            missing_regular = replay.replay_portfolio(state, [anchor, regular], policy=self.policy, bridge_config={}, markets=["US"], prospective_initial_basis=True)
            # A later refreshed calendar cannot change the anchor's early close.
            changed = deepcopy(frozen)
            for row in changed["sessions"]:
                if row["date"] == "2026-11-27": row["regular_end"] = "2026-11-27T21:00:00Z"
            changed.pop("calendar_sha256"); changed["calendar_sha256"] = replay.payload_sha256(changed)
            later = frame(early, observation=True, provider={**session, "regular_end": "2026-11-27T21:00:00Z"}, later_calendar=changed)
            disagreement = replay.replay_portfolio(state, [anchor, later], policy=self.policy, bridge_config={}, markets=["US"], prospective_initial_basis=True)
        self.assertEqual("COMPLETE", result["status"], result["diagnostics"])
        self.assertEqual("PENDING", missing_regular["status"])
        self.assertTrue(any(row["reason"] == "lifecycle_observation_missing" for row in missing_regular["diagnostics"]))
        self.assertTrue(any(row["reason"] == "provider_calendar_disagreement" for row in disagreement["diagnostics"]))
        self.assertEqual(1, len(portfolio.open_positions(result["final_state"], "US")))
        self.assertEqual(state, self.state)
        gpw_start = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        gpw_anchor = {"observed_at": gpw_start.isoformat(), "session_calendar": {
            "GPW": calendar.build_receipt("GPW", now=gpw_start - timedelta(hours=1))}}
        gpw_holiday = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
        self.assertTrue(portfolio._regular_session_open("GPW", gpw_holiday))
        self.assertEqual((False, None), replay.anchor_regular_interval(gpw_anchor, {}, "GPW", now=gpw_holiday))


if __name__ == "__main__":
    unittest.main()
