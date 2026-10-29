"""Private Saxo Weekly monitoring contracts: no order and no public price."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import saxo_private_weekly_cb_d1 as weekly

NOW = datetime(2026, 10, 29, 10, 10, tzinfo=timezone.utc)
POSITION = {
    "instrument_id": "eurusd", "position_id": "weekly:existing",
    "direction": "short", "entry_price": 1.12000,
    "stop_loss": 1.13, "initial_stop_loss": 1.13,
    "take_profit": 1.10000, "opened_at": "2026-10-26T11:00:00Z",
}


class FakeD1:
    def __init__(self):
        self.events = {}

    def query(self, sql, params=None):
        if sql == weekly.CREATE_SQL:
            return {"results": []}
        if sql == weekly.INSERT_SQL:
            key, _, _, digest, payload = params
            self.events.setdefault(key, {"sha256": digest, "payload": payload})
            return {"results": []}
        if sql == weekly.SELECT_SQL:
            return {"results": [self.events[params[0]]]} if params[0] in self.events else {"results": []}
        raise AssertionError("unexpected D1 operation")


def quote(stamp=None, *, bid=1.11500, ask=1.11520):
    return {
        "bid": bid, "ask": ask, "mid": (bid + ask) / 2,
        "observed_at": stamp or NOW - timedelta(seconds=2),
        "redistributable": False,
    }


def assessment(direction="short", q=None):
    position = dict(POSITION, direction=direction)
    with patch.dict(os.environ, {"BRIEFROOMS_PRIVATE_RUNTIME": "1", "SAXO_PRIVATE_PRIMARY": "1"}), \
         patch.object(weekly, "load_wire", return_value={}), \
         patch.object(weekly, "load_policy", return_value={}), \
         patch.object(weekly, "validate_policy", return_value={}), \
         patch.object(weekly, "assess_pre_event", return_value=SimpleNamespace(to_dict=lambda: {
             "action": "NO_ACTION_UNVERIFIED", "mode": "SHADOW",
             "execution_authority": False,
         })) as risk:
        report = weekly.private_assessment(
            root=Path("/tmp"), now=NOW, quote=q or quote(),
            week_id="2026-W44", position=position,
        )
    args = risk.call_args
    assert args.args[1]["verified"] is False
    assert args.kwargs["scope"] == "EURUSD_WEEKLY"
    return report


class SaxoPrivateWeeklyTests(unittest.TestCase):
    def test_short_buyback_uses_ask_not_mid_or_bid(self):
        report = assessment("short")
        self.assertEqual(report["quote"]["close_side"], "ASK")
        self.assertAlmostEqual(report["economics"]["gross_pips_at_indicative_close_side"], 48.0)
        self.assertIsNone(report["economics"]["estimated_net_pips"])
        self.assertFalse(report["executable_broker_fill_confirmed"])
        self.assertFalse(report["execution_authority"])
        self.assertFalse(report["broker_order_sent"])
        self.assertFalse(report["canonical_weekly_state_mutated"])
        self.assertFalse(report["redistributable"])

    def test_long_close_uses_bid(self):
        report = assessment("long")
        self.assertEqual(report["quote"]["close_side"], "BID")
        self.assertAlmostEqual(report["economics"]["gross_pips_at_indicative_close_side"], -50.0)

    def test_no_stale_future_or_invalid_spread_passes_to_risk(self):
        for q in (
            quote(NOW - timedelta(seconds=16)),
            quote(NOW + timedelta(seconds=1)),
            quote(bid=1.11520, ask=1.11500),
            quote(bid=1.11500, ask=1.11660),
        ):
            with self.subTest(quote=q), self.assertRaises(weekly.PrivateStateFailure):
                assessment(q=q)

    def test_off_reconciliation_does_not_create_any_trade(self):
        report = assessment()
        self.assertFalse(report["exit_intent_created"])
        self.assertFalse(report["economics"]["simulated_fill_executed"])
        self.assertEqual(report["risk"]["action"], "NO_ACTION_UNVERIFIED")

    def test_private_d1_records_are_content_addressed_and_idempotent(self):
        d1 = FakeD1()
        report = assessment()
        digest = weekly.persist_private(d1, report)
        self.assertEqual(len(d1.events), 1)
        self.assertEqual(weekly.persist_private(d1, report), digest)
        self.assertEqual(len(d1.events), 1)
        self.assertEqual(json.loads(d1.events[digest]["payload"])["position_id"], "weekly:existing")

    def test_d1_corrupted_readback_fails_closed(self):
        d1 = FakeD1()
        report = assessment()
        digest = weekly.persist_private(d1, report)
        d1.events[digest]["payload"] = "{}"
        with self.assertRaisesRegex(weekly.PrivateStateFailure, "readback_mismatch"):
            weekly.persist_private(d1, report)

    def test_only_canonical_weekly_eurusd_is_read(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / "data" / "investments" / "weekly"
            directory.mkdir(parents=True)
            weekly_file = directory / "2026-W44.json"
            original = {
                "week_id": "2026-W44",
                "instruments": [
                    {"instrument_id": "eurusd", "trade_status": "open",
                     "direction": "short", "entry_price": 1.12,
                     "entry_captured_at": "2026-10-26T10:00:00Z",
                     "risk_plan": {"stop_loss_price": 1.13, "take_profit_price": 1.10}},
                    {"instrument_id": "btcusd", "trade_status": "open",
                     "direction": "long", "entry_price": 100},
                ],
            }
            weekly_file.write_text(json.dumps(original), encoding="utf-8")
            week_id, pos = weekly.current_weekly_eurusd(root, NOW)
            self.assertEqual(week_id, "2026-W44")
            self.assertEqual(pos["instrument_id"], "eurusd")
            self.assertEqual(json.loads(weekly_file.read_text()), original)
            original["instruments"][0]["trade_status"] = "closed"
            weekly_file.write_text(json.dumps(original), encoding="utf-8")
            _, pos = weekly.current_weekly_eurusd(root, NOW)
            self.assertIsNone(pos)

    def test_no_private_quote_or_exception_text_goes_to_logs(self):
        with patch.dict(os.environ, {
            "BRIEFROOMS_PRIVATE_RUNTIME": "1", "SAXO_PRIVATE_PRIMARY": "1",
            "SAXO_PRIVATE_AUTO_ENABLED": "true", "SAXO_PRIVATE_BASIC_PASSWORD": "SENSITIVE",
        }), patch("daily_eurusd_cycle_watchdog.fx_market_active", return_value=True), \
           patch.object(weekly, "current_weekly_eurusd", return_value=("2026-W44", POSITION)), \
           patch.object(weekly, "fetch_private_saxo_quote", side_effect=RuntimeError("SECRET_1.11500")), \
           contextlib.redirect_stdout(io.StringIO()) as out:
            code = weekly.main()
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue().strip(), "PRIVATE_WEEKLY_SAXO=RECOVERY_REQUIRED_FAIL_CLOSED")
        self.assertNotIn("SECRET", out.getvalue())


if __name__ == "__main__":
    unittest.main()
