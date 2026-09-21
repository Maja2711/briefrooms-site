#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import audit_intraday_risk_exits as risk


UTC = timezone.utc


def btc_week() -> dict:
    return {
        "week_id": "2026-W39",
        "market_window": {"exit_target_local": "2026-09-25T22:00:00+02:00"},
        "instruments": [
            {
                "instrument_id": "btcusd",
                "symbol": "BTC-USD",
                "direction": "short",
                "entry_price": 81593.9765625,
                "entry_captured_at": "2026-09-21T09:25:00+02:00",
                "trade_status": "open",
                "exit_price": None,
                "notional_usd": 10000,
                "continuous_exposure_active": True,
                "continuous_exposure_status": "open",
                "next_entry_status": "open",
                "pending_entry_decision": None,
                "risk_status": "open_multi_instrument_continuous_exposure",
                "risk_plan": {
                    "generated_at": "2026-09-21T09:26:26+02:00",
                    "direction": "short",
                    "stop_loss_price": 84024.33708186,
                    "take_profit_price": 76707.42716313,
                    "same_bar_rule": "stop_loss_first_conservative",
                },
            }
        ],
    }


class WeeklyRiskMonitorTests(unittest.TestCase):
    def test_live_coinbase_ticker_cross_closes_short_at_frozen_stop(self):
        now = datetime(2026, 9, 21, 9, 20, 30, tzinfo=UTC)
        point = risk.PricePoint(
            ts=datetime(2026, 9, 21, 9, 20, 26, tzinfo=UTC),
            price=84029.95,
            source="Coinbase Exchange:BTC-USD:ticker",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "2026-W39.json"
            path.write_text(json.dumps(btc_week()), encoding="utf-8")
            with patch.object(risk, "fetch_coinbase_bars", return_value=[]), \
                    patch.object(risk, "fetch_coinbase_ticker", return_value=point):
                report = risk.audit(path=path, now=now, persist_report="never")
            saved = json.loads(path.read_text(encoding="utf-8"))

        item = saved["instruments"][0]
        self.assertTrue(report["changed"])
        self.assertEqual("closed", item["trade_status"])
        self.assertEqual("stop_loss", item["exit_reason"])
        self.assertEqual(84024.33708186, item["exit_price"])
        self.assertEqual("Coinbase Exchange:BTC-USD:ticker", item["exit_source"])
        self.assertEqual("live_ticker", item["risk_exit_evidence"]["kind"])
        self.assertFalse(item["continuous_exposure_active"])
        self.assertLess(item["result_percent"], 0)

    def test_transient_historical_spike_is_not_lost_when_current_price_retreated(self):
        now = datetime(2026, 9, 21, 9, 20, 30, tzinfo=UTC)
        bars = [
            risk.PriceBar(
                ts=datetime(2026, 9, 21, 7, 35, tzinfo=UTC),
                high=84030.0,
                low=83800.0,
                source="Coinbase Exchange:BTC-USD:candles:5m",
            )
        ]
        current = risk.PricePoint(
            ts=datetime(2026, 9, 21, 9, 20, 26, tzinfo=UTC),
            price=83500.0,
            source="Coinbase Exchange:BTC-USD:ticker",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "2026-W39.json"
            path.write_text(json.dumps(btc_week()), encoding="utf-8")
            with patch.object(risk, "fetch_coinbase_bars", return_value=bars), \
                    patch.object(risk, "fetch_coinbase_ticker", return_value=current):
                report = risk.audit(path=path, now=now, persist_report="never")
            saved = json.loads(path.read_text(encoding="utf-8"))

        item = saved["instruments"][0]
        self.assertTrue(report["changed"])
        self.assertEqual("stop_loss", item["exit_reason"])
        self.assertEqual("5m_ohlc", item["risk_exit_evidence"]["kind"])
        self.assertEqual("2026-09-21T09:35:00+02:00", item["exit_captured_at"])

    def test_same_bar_sl_and_tp_executes_stop_first(self):
        after = datetime(2026, 9, 21, 7, 26, 26, tzinfo=UTC)
        bars = [
            risk.PriceBar(
                ts=datetime(2026, 9, 21, 7, 30, tzinfo=UTC),
                high=85000.0,
                low=76000.0,
                source="test",
            )
        ]
        hit = risk.first_hit(
            bars,
            "short",
            84024.33708186,
            76707.42716313,
            after,
        )
        self.assertIsNotNone(hit)
        self.assertEqual("stop_loss", hit[0])
        self.assertEqual(84024.33708186, hit[1])

    def test_missing_all_btc_market_data_never_implies_no_hit_and_never_closes(self):
        now = datetime(2026, 9, 21, 9, 20, 30, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "2026-W39.json"
            path.write_text(json.dumps(btc_week()), encoding="utf-8")
            with patch.object(risk, "fetch_coinbase_bars", side_effect=RuntimeError("down")), \
                    patch.object(risk, "fetch_coinbase_ticker", side_effect=RuntimeError("down")), \
                    patch.object(risk, "fetch_yahoo_bars", side_effect=RuntimeError("down")):
                report = risk.audit(path=path, now=now, persist_report="never")
            saved = json.loads(path.read_text(encoding="utf-8"))

        item = saved["instruments"][0]
        self.assertFalse(report["changed"])
        self.assertEqual("open", item["trade_status"])
        self.assertIsNone(item["exit_price"])
        self.assertGreaterEqual(len(report["errors"]), 3)

    def test_yahoo_is_used_only_when_coinbase_has_no_usable_evidence(self):
        start = datetime(2026, 9, 21, 7, 26, 26, tzinfo=UTC)
        end = datetime(2026, 9, 21, 9, 20, 30, tzinfo=UTC)
        yahoo = [
            risk.PriceBar(
                ts=datetime(2026, 9, 21, 8, 0, tzinfo=UTC),
                high=84050.0,
                low=83900.0,
                source="Yahoo Finance:BTC-USD:chart:5m",
            )
        ]
        with patch.object(risk, "fetch_coinbase_bars", side_effect=RuntimeError("down")), \
                patch.object(risk, "fetch_coinbase_ticker", side_effect=RuntimeError("down")), \
                patch.object(risk, "fetch_yahoo_bars", return_value=yahoo):
            hit, errors, authority = risk._btc_evidence(
                start,
                end,
                "short",
                84024.33708186,
                76707.42716313,
            )

        self.assertIsNotNone(hit)
        self.assertEqual("stop_loss", hit[0])
        self.assertEqual("yahoo_fallback", authority)
        self.assertTrue(any("coinbase_candles" in error for error in errors))
        self.assertTrue(any("coinbase_ticker" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
