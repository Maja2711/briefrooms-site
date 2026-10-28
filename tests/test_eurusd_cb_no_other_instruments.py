"""Acceptance boundaries: EURUSD facts cannot reach unrelated engine state.

Fixtures are synthetic. Git checks only read local objects and apply Git's
configured checkout line endings before comparing exact on-disk bytes.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import daily_eurusd_fast_lifecycle as daily_fast
import eurusd_central_bank_event_risk as risk
import eurusd_central_bank_wire as wire
import eurusd_daily_cb_event_adapter as daily
import eurusd_weekly_cb_event_adapter as weekly
import eurusd_cb_shadow_observe as observer
from belief_market_data_adapter import Bar

NOW = datetime(2026, 10, 28, 18, 5, tzinfo=timezone.utc)
UNRELATED = ("btcusd", "sp500_futures", "stock_trading", "gbpusd")


def position(iid):
    return {"instrument_id": iid, "position_id": "synthetic:" + iid, "direction": "short",
        "status": "OPEN", "trade_status": "open", "entry_price": 1.11869,
        "entry_captured_at": (NOW - timedelta(hours=2)).isoformat(), "exit_price": None,
        "risk_plan": {"stop_loss_price": 1.12487, "take_profit_price": 1.10220},
        "position_legs": [{"leg_id": "closed-original", "exit_price": 1.12, "pnl": -1.25}],
        "pending_entry_decision": {"frozen_target": 1.12, "created_at": "synthetic-original"}}


class NoOtherInstrumentTests(unittest.TestCase):
    def test_weekly_rejects_other_instruments_before_any_fact_or_policy_read(self):
        for iid in UNRELATED:
            with self.subTest(instrument=iid):
                item = position(iid)
                before = copy.deepcopy(item)
                with patch.object(wire, "load_wire", side_effect=AssertionError("wire read")) as load, \
                     patch.object(risk, "load_policy", side_effect=AssertionError("policy read")) as policy, \
                     patch.object(weekly, "_read", side_effect=AssertionError("file read")) as read:
                    self.assertIsNone(weekly.evaluate_weekly_cb_event(item, week_id="synthetic-W44", now=NOW))
                self.assertEqual(item, before)
                load.assert_not_called()
                policy.assert_not_called()
                read.assert_not_called()

    def test_mixed_non_eurusd_week_never_persists_a_risk_assessment(self):
        week = {"week_id": "synthetic-W44", "instruments": [position(iid) for iid in UNRELATED]}
        original = json.dumps(week, sort_keys=True, separators=(",", ":")).encode()
        with tempfile.TemporaryDirectory() as td, \
             patch.object(weekly, "evaluate_weekly_cb_event", side_effect=AssertionError("unrelated assessment")), \
             patch.object(weekly, "persist_weekly_assessment", side_effect=AssertionError("unrelated write")):
            self.assertIsNone(weekly.evaluate_current_week(root=Path(td), now=NOW, week=week, persist=True))
            self.assertEqual(list(Path(td).iterdir()), [])
        self.assertEqual(json.dumps(week, sort_keys=True, separators=(",", ":")).encode(), original)

    def test_daily_rejects_explicit_other_instrument_before_io(self):
        for iid in UNRELATED:
            with self.subTest(instrument=iid):
                item = position(iid)
                before = copy.deepcopy(item)
                with patch.object(wire, "load_wire", side_effect=AssertionError("wire read")) as load, \
                     patch.object(risk, "load_policy", side_effect=AssertionError("policy read")) as policy:
                    with self.assertRaisesRegex(ValueError, "EURUSD"):
                        daily.evaluate_daily(item, (), NOW)
                load.assert_not_called()
                policy.assert_not_called()
                self.assertEqual(item, before)

    def test_common_assessor_rejects_foreign_identity_and_scope_without_mutation(self):
        for iid in UNRELATED:
            with self.subTest(instrument=iid):
                item = position(iid)
                before = copy.deepcopy(item)
                with patch.object(wire, "validate_snapshot", side_effect=AssertionError("foreign facts consumed")) as validate:
                    with self.assertRaisesRegex(ValueError, "EURUSD"):
                        risk.assess_pre_event(item, {}, {}, policy=risk.DEFAULT_POLICY, now=NOW, scope="EURUSD_DAILY")
                validate.assert_not_called()
                self.assertEqual(item, before)
        with self.assertRaises(ValueError):
            risk.assess_pre_event(None, {}, {}, policy=risk.DEFAULT_POLICY, now=NOW, scope="STOCK_TRADING")

    def test_public_daily_observer_rejects_other_instrument_before_wire_read(self):
        for instrument in ("BTC/USD", "S&P 500", "GBP/USD", "STOCK_TRADING"):
            with self.subTest(instrument=instrument), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                state = root / "data/investments/eurusd_daily_spot.json"
                state.parent.mkdir(parents=True)
                state.write_text(json.dumps({"instrument": instrument, "metadata": {"position": None}}), encoding="utf-8")
                original = state.read_bytes()
                with patch.object(observer, "load_wire", side_effect=AssertionError("foreign wire read")) as load:
                    with self.assertRaisesRegex(ValueError, "EURUSD"):
                        observer.observe_daily(root=root, now=NOW)
                load.assert_not_called()
                self.assertEqual(state.read_bytes(), original)

    def test_daily_hard_stop_survives_wire_exception_without_state_write(self):
        opened = NOW - timedelta(minutes=1)
        payload = {"instrument": "EUR/USD", "metadata": {"position": {
            "trade_id": "synthetic-hard-stop", "status": "OPEN", "direction": "SHORT",
            "opened_at": opened.isoformat(), "expires_at": (opened + timedelta(hours=27)).isoformat(),
            "entry": 1.125, "stop": 1.128, "target": 1.119, "entry_score": 35, "entry_confidence": .4}}}
        bars = [Bar(timestamp=NOW, open=1.125, high=1.1281, low=1.124, close=1.1275)]
        class Client:
            def bars(self, *args):
                return bars
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "daily.json"
            state.write_text(json.dumps(payload), encoding="utf-8")
            original = state.read_bytes()
            with patch.object(wire, "load_wire", side_effect=RuntimeError("synthetic feed outage")):
                result = daily_fast.probe(state, Client())
            self.assertTrue(result["exit_triggered"])
            self.assertEqual(result["exit_reason"], "STOP_LOSS")
            self.assertEqual(result["central_bank_event_risk"]["risk_protection_status"], "PROTECTION_PENDING")
            self.assertEqual(state.read_bytes(), original)
            self.assertEqual(sorted(path.name for path in Path(td).iterdir()), ["daily.json"])

    def test_existing_weekly_daily_history_and_stock_state_match_head_bytes(self):
        env = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"}
        names = subprocess.check_output(["git", "ls-files", "-z", "--",
            "data/investments/weekly", "data/investments/eurusd_daily_history.json",
            "data/investments/stock_trading_portfolio.json", "data/investments/stock_trading_policy.json"], cwd=ROOT, env=env)
        paths = [name.decode("utf-8") for name in names.split(b"\0") if name]
        self.assertGreaterEqual(len(paths), 5)
        self.assertIn("data/investments/eurusd_daily_history.json", paths)
        self.assertIn("data/investments/stock_trading_portfolio.json", paths)
        for relative in paths:
            with self.subTest(path=relative):
                # --filters reproduces checkout CRLF/LF without altering Git
                # objects, tracked files, the index or any decision history.
                expected = subprocess.check_output(["git", "cat-file", "--filters", "HEAD:" + relative], cwd=ROOT, env=env)
                self.assertEqual((ROOT / relative).read_bytes(), expected)


if __name__ == "__main__":
    unittest.main()
