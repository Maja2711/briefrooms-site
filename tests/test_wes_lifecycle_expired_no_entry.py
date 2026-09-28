from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import investments_wes_lifecycle as lifecycle


class ExpiredNoEntryLifecycleTests(unittest.TestCase):
    def run_case(self, row):
        with tempfile.TemporaryDirectory() as tmp:
            weekly_dir = Path(tmp)
            week = {
                "week_id": "2026-W37",
                "market_window": {"exit_target_local": "2026-09-11T22:00:00+02:00"},
                "instruments": [row],
            }
            path = weekly_dir / "2026-W37.json"
            path.write_text(json.dumps(week), encoding="utf-8")
            with patch.object(lifecycle, "WEEKLY_DIR", weekly_dir):
                changed = lifecycle.settle_due_positions(
                    datetime.fromisoformat("2026-09-14T13:00:00+02:00")
                )
            saved = json.loads(path.read_text(encoding="utf-8"))["instruments"][0]
            return changed, saved

    def test_directional_decision_without_entry_expires_after_deadline(self):
        changed, row = self.run_case({
            "instrument_id": "sp500_futures",
            "symbol": "ES=F",
            "direction": "short",
            "entry_price": None,
            "exit_price": None,
            "trade_status": "planned",
            "pending_entry_decision": {"decision": {"direction": "short"}},
            "continuous_exposure_active": True,
            "continuous_exposure_status": "open",
            "next_entry_status": "pending",
        })
        self.assertTrue(changed)
        self.assertEqual("expired_no_entry", row["trade_status"])
        self.assertEqual("no_entry", row["execution_outcome"])
        self.assertEqual("short", row["expired_entry_direction"])
        self.assertIsNone(row["pending_entry_decision"])
        self.assertFalse(row["continuous_exposure_active"])
        self.assertEqual("closed", row["continuous_exposure_status"])
        self.assertIsNone(row["entry_price"])
        self.assertIsNone(row["exit_price"])
        self.assertNotEqual("no_trade", row.get("result"))

    def test_neutral_forecast_with_expired_wes_authorization_is_terminalized(self):
        changed, row = self.run_case({
            "instrument_id": "sp500_futures",
            "symbol": "ES=F",
            "direction": "neutral",
            "entry_price": None,
            "exit_price": None,
            "trade_status": "planned",
            "result": "no_trade",
            "result_value": 0.0,
            "result_percent": 0.0,
            "pending_entry_decision": None,
            "wes_entry_authorization": {
                "authorized_at": "2026-09-08T06:39:37+02:00",
                "expires_at": "2026-09-08T06:59:37+02:00",
                "candidate": {"direction": "long", "entry_class": "midweek_trigger"},
            },
            "continuous_exposure_active": True,
            "continuous_exposure_status": "open",
            "next_entry_status": "pending",
        })
        self.assertTrue(changed)
        self.assertEqual("expired_no_entry", row["trade_status"])
        self.assertEqual("no_entry", row["execution_outcome"])
        self.assertEqual("long", row["expired_entry_direction"])
        self.assertEqual("no_trade", row["result"])
        self.assertEqual(0.0, row["result_value"])
        self.assertEqual(0.0, row["result_percent"])
        self.assertIsNone(row["entry_price"])
        self.assertIsNone(row["exit_price"])
        self.assertFalse(row["continuous_exposure_active"])
        self.assertEqual("closed", row["continuous_exposure_status"])

    def test_closed_leg_with_unfilled_reentry_returns_to_closed_after_deadline(self):
        changed, row = self.run_case({
            "instrument_id": "btcusd",
            "symbol": "BTC-USD",
            "direction": "short",
            "entry_price": 81593.9765625,
            "entry_captured_at": "2026-09-21T09:25:00+02:00",
            "exit_price": 84024.33708186,
            "exit_captured_at": "2026-09-21T10:35:00+02:00",
            "exit_reason": "stop_loss",
            "result": "loss",
            "result_value": -297.86028599,
            "result_percent": -2.9786,
            "trade_status": "pending",
            "pending_entry_decision": {
                "decision": {"direction": "long", "strategy_id": "base_v2"},
            },
            "continuous_exposure_active": False,
            "continuous_exposure_status": "closed",
            "next_entry_status": "pending",
            "risk_status": "stop_loss_hit",
        })
        self.assertTrue(changed)
        self.assertEqual("closed", row["trade_status"])
        self.assertEqual("expired_no_entry", row["pending_reentry_outcome"])
        self.assertEqual("governed_deadline_elapsed_without_reentry", row["pending_reentry_expiry_reason"])
        self.assertIsNone(row["pending_entry_decision"])
        self.assertEqual("closed", row["next_entry_status"])
        self.assertEqual(81593.9765625, row["entry_price"])
        self.assertEqual(84024.33708186, row["exit_price"])
        self.assertEqual(-297.86028599, row["result_value"])

    def test_plain_neutral_no_trade_closes_without_fake_directional_expiry(self):
        changed, row = self.run_case({
            "instrument_id": "sp500_futures",
            "symbol": "ES=F",
            "direction": "neutral",
            "entry_price": None,
            "exit_price": None,
            "trade_status": "planned",
            "result": "no_trade",
            "result_value": 0.0,
            "result_percent": 0.0,
            "pending_entry_decision": None,
            "continuous_exposure_active": False,
            "continuous_exposure_status": "closed",
        })
        self.assertTrue(changed)
        self.assertEqual("no_trade", row["trade_status"])
        self.assertIsNone(row.get("execution_outcome"))
        self.assertIsNone(row.get("expired_entry_direction"))
        self.assertIsNone(row["entry_price"])
        self.assertIsNone(row["exit_price"])


if __name__ == "__main__":
    unittest.main()
