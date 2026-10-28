import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import stock_trading_portfolio as portfolio
from scripts import stock_trading_v2_production_bridge as bridge
from scripts import stock_trading_v2_production_replay as replay
from scripts import stock_trading_v2_replay_capture as capture


class SessionCloseCaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.now = datetime(2026, 9, 17, 20, 5, tzinfo=timezone.utc)
        self.snapshots = {
            "policy": {"original_policy": {"preserved": True}},
            "state": {"original_closed_history": [{"arbitrary": "must remain raw"}]},
            "runtime": {"phase": "FULL", "existing": "raw"},
            "manifest": {"original_manifest": 1},
            "config": {"original_config": 2},
        }
        self.paths = {}
        self.original = {}
        for key, value in self.snapshots.items():
            path = self.root / f"{key}.json"
            path.write_text(json.dumps(value, indent=3), encoding="utf-8")
            self.paths[f"{key}_path"] = path
            self.original[key] = path.read_bytes()
        self.benchmark = {"US": {"symbol": "SPY", "price": 500, "observed_at": "2026-09-17T19:59:59Z", "received_at": self.now.isoformat(), "provider": "Yahoo", "status": "OBSERVED"}}
        self.sessions = {"US": {"date": "2026-09-17", "regular_start": "2026-09-17T13:30:00Z", "regular_end": "2026-09-17T20:00:00Z",
                               "state": "POST", "closed": True, "provider": "Yahoo", "received_at": self.now.isoformat(), "source": "PROVIDER_REGULAR_PERIOD"}}

    def collect(self, **kwargs):
        return capture.capture_close(root=self.root / "frames", now=self.now, **self.paths, **kwargs)

    def close_input(self, symbol="AAA", *, price=150, end=None):
        end = end or replay._time(self.sessions["US"]["regular_end"])
        return {"symbol": symbol, "market": "US", "currency": "USD", "provider": "Yahoo Finance chart",
                "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol,
                "received_at": self.now.isoformat(), "source_bars": [{"start": (end - timedelta(minutes=5)).isoformat(),
                    "end": end.isoformat(), "interval_seconds": 300, "high": price + 1, "low": price - 1,
                    "close": price, "observed_at": self.now.isoformat(), "provider": "Yahoo"}]}

    def test_actual_provider_close_freezes_raw_snapshots_without_any_trading_call(self):
        with patch.object(bridge, "_capture_benchmarks", return_value=(self.benchmark, self.sessions)) as provider, \
             patch.object(portfolio, "load_state", side_effect=AssertionError("normalization forbidden")), \
             patch.object(portfolio, "review_market", side_effect=AssertionError("lifecycle forbidden")), \
             patch.object(portfolio, "admit_candidate", side_effect=AssertionError("admission forbidden")), \
             patch.object(portfolio, "save_state", side_effect=AssertionError("portfolio persistence forbidden")), \
             patch.object(bridge, "process_market", side_effect=AssertionError("production admission forbidden")):
            result = self.collect()
        self.assertEqual("CAPTURED", result["status"])
        self.assertFalse(result["production_mutated"])
        provider.assert_called_once_with(self.now)
        frame = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        self.assertEqual("SESSION_CLOSE", frame["kind"])
        self.assertEqual({}, frame["observations"])
        self.assertEqual({}, frame["opportunities"])
        self.assertEqual({}, frame["quotes"])
        self.assertEqual(self.snapshots["state"], frame["initial_state"])
        self.assertEqual(self.snapshots["policy"], frame["policy"])
        self.assertEqual(self.snapshots["runtime"], frame["runtime"])
        self.assertEqual(self.snapshots["manifest"], frame["manifest"])
        self.assertEqual(self.snapshots["config"], frame["bridge_config"])
        self.assertEqual(self.sessions, frame["market_sessions"])
        self.assertEqual([], replay._validate_frame(frame, replay.production_code_sha256()))
        for key, original in self.original.items():
            self.assertEqual(original, self.paths[f"{key}_path"].read_bytes())

    def test_no_sourced_close_is_pending_and_does_not_create_frame(self):
        with patch.object(bridge, "_capture_benchmarks", return_value=({}, {})):
            result = self.collect()
        self.assertEqual("PENDING", result["status"])
        self.assertFalse((self.root / "frames").exists())

    def test_previous_session_metadata_cannot_collect_stale_close_on_holiday(self):
        stale = {"US": {**self.sessions["US"], "date": "2026-09-16", "regular_start": "2026-09-16T13:30:00Z", "regular_end": "2026-09-16T20:00:00Z"}}
        benchmark = {"US": {**self.benchmark["US"], "observed_at": "2026-09-16T19:59:59Z"}}
        with patch.object(bridge, "_capture_benchmarks", return_value=(benchmark, stale)), \
             patch.object(capture, "close_observation", side_effect=AssertionError("stale session must not collect")):
            result = self.collect()
        self.assertEqual("PENDING", result["status"])
        self.assertFalse((self.root / "frames").exists())

    def test_regular_future_or_unsourced_session_cannot_become_close_evidence(self):
        mutations = (
            {"state": "REGULAR", "closed": False},
            {"source": ""},
            {"regular_end": (self.now + timedelta(hours=1)).isoformat()},
            {"received_at": (self.now + timedelta(seconds=1)).isoformat()},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                sessions = {"US": {**self.sessions["US"], **mutation}}
                with patch.object(bridge, "_capture_benchmarks", return_value=(self.benchmark, sessions)):
                    result = self.collect()
                self.assertEqual("PENDING", result["status"])
        self.assertFalse((self.root / "frames").exists())

    def test_close_collector_reads_real_and_watched_bars_and_preserves_original_files_twice(self):
        state = {"markets": {"US": {"open_positions": [{"symbol": "REAL", "status": "OPEN"}],
                                    "closed_positions": [{"position_id": "history", "source": "original"}]}}}
        self.paths["state_path"].write_text(json.dumps(state, indent=3))
        self.original["state"] = self.paths["state_path"].read_bytes()
        watch = self.root / "watch.json"
        replay.register_replay_watch(market="US", symbol="WATCH", decision_at=self.now - timedelta(hours=1), root=watch)
        with patch.object(bridge, "_capture_benchmarks", return_value=(self.benchmark, self.sessions)), \
             patch.object(portfolio, "save_state", side_effect=AssertionError("no writes")), \
             patch.object(portfolio, "review_market", side_effect=AssertionError("no lifecycle")):
            for _ in range(2):
                collected = []
                def observed(symbol, market, session, **kwargs):
                    collected.append(symbol)
                    return self.close_input(symbol)
                result = self.collect(watch_path=watch, observation_fetcher=observed)
                frame = json.loads(Path(result["path"]).read_text())
                self.assertEqual(["REAL", "WATCH"], collected)
                self.assertEqual({"REAL", "WATCH"}, set(frame["close_observations"]["US"]))
                for key, original in self.original.items():
                    self.assertEqual(original, self.paths[f"{key}_path"].read_bytes())

    def test_native_close_bar_parser_uses_exact_formed_bar_and_rejects_wrong_issuer(self):
        end = replay._time(self.sessions["US"]["regular_end"])
        chart = {"meta": {"symbol": "AAA", "currency": "USD"}, "timestamp": [int((end - timedelta(minutes=n)).timestamp()) for n in (10, 5, 0)],
                 "indicators": {"quote": [{"high": [150, 151, 999], "low": [148, 149, 1], "close": [149, 150, 999]}]}}
        with patch.object(portfolio, "_chart", return_value=chart):
            value = capture.close_observation("AAA", "US", self.sessions["US"], now=self.now)
        self.assertEqual(2, len(value["source_bars"]))
        self.assertEqual(end, replay._time(value["source_bars"][-1]["end"]))
        self.assertEqual(150, value["source_bars"][-1]["close"])
        with patch.object(portfolio, "_chart", return_value={**chart, "meta": {"symbol": "OTHER", "currency": "USD"}}):
            with self.assertRaisesRegex(ValueError, "identity"):
                capture.close_observation("AAA", "US", self.sessions["US"], now=self.now)

    def test_virtual_runner_stays_open_and_uses_exact_close_nav_missing_terminal_bar_is_pending(self):
        policy = portfolio.load_policy()
        state = portfolio.empty_state(self.now, policy)
        position = {"position_id": "runner", "market": "US", "symbol": "AAA", "status": "OPEN", "name": "Runner",
                    "opened_at": "2026-09-17T14:00:00Z", "entry": 50, "last_mark": 100, "stop": 95,
                    "target": 160, "quantity": 50, "profit_runner_enabled": True, "source_execution_fact": {"original": 1}}
        state["markets"]["US"]["open_positions"] = [position]
        state["markets"]["US"]["closed_positions"] = [{"position_id": "historical", "original": {"immutable": 1}}]
        original = deepcopy(state)
        kwargs = dict(kind="SESSION_CLOSE", observed_at=self.now, recorded_at=self.now, policy=policy, initial_state=state,
                      benchmark=self.benchmark, market_sessions=self.sessions)
        complete = replay.capture_frame(self.root / "frames", close_observations={"US": {"AAA": self.close_input()}}, **kwargs)["frame"]
        missing = replay.capture_frame(self.root / "frames", close_observations={"US": {"AAA": self.close_input(end=replay._time(self.sessions["US"]["regular_end"]) - timedelta(minutes=5))}}, **kwargs)["frame"]
        result = replay.replay_portfolio(state, [complete], policy=policy, bridge_config={}, markets=["US"], prospective_initial_basis=True)
        pending = replay.replay_portfolio(state, [missing], policy=policy, bridge_config={}, markets=["US"], prospective_initial_basis=True)
        self.assertEqual("COMPLETE", result["status"], result["diagnostics"])
        runner = portfolio.open_positions(result["final_state"], "US")[0]
        self.assertEqual("OPEN", runner["status"])
        self.assertEqual(150, runner["last_mark"])
        self.assertEqual(95, runner["stop"])
        self.assertEqual(160, runner["target"])
        self.assertGreater(result["metrics_by_market"]["US"]["net_pnl_amount"], 2490)
        self.assertEqual(original, state)
        self.assertEqual(original["markets"]["US"]["closed_positions"], result["final_state"]["markets"]["US"]["closed_positions"])
        self.assertEqual("PENDING", pending["status"])
        self.assertTrue(any(row["reason"] == "native_exact_close_bar_missing_or_invalid" for row in pending["diagnostics"]))
        self.assertEqual(100, portfolio.open_positions(pending["final_state"], "US")[0]["last_mark"])


if __name__ == "__main__":
    unittest.main()
