from __future__ import annotations

import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from belief_market_data_adapter import Bar
import daily_eurusd_event_overlay as event_risk
import daily_eurusd_lifecycle as lifecycle
import daily_eurusd_macro_event_risk as macro_risk
import daily_eurusd_spot_v14 as dynamic
import execution_price_engine as epe

OPENED = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)


def bar(hours: float, close: float, **values: float) -> Bar:
    return Bar(
        timestamp=OPENED + timedelta(hours=hours),
        open=close,
        high=values.get("high", close),
        low=values.get("low", close),
        close=close,
    )


def position(direction: str) -> dict:
    fill = epe.verify_daily_live_quotes(direction, [
        epe.Quote(1.1, OPENED, "Stooq:EURUSD"),
        epe.Quote(1.10002, OPENED, "Yahoo Finance:EURUSD"),
    ], now=OPENED)
    geometry = epe.recenter_geometry(
        direction, 1.1,
        1.09 if direction == "LONG" else 1.11,
        1.118 if direction == "LONG" else 1.082,
        fill["fill_price"], market_mid=fill["selected_mid_price"],
    )
    result = lifecycle.create_position({
        "direction": direction,
        "timestamp": OPENED.isoformat(),
        "entry": geometry["entry"],
        "stop": geometry["stop"],
        "target": geometry["target"],
        "score": 65 if direction == "LONG" else 35,
        "confidence": .6,
        "engine_version": "eurusd-daily-spot-v1.9.0",
        "metadata": {},
    })
    result["execution_price_engine"] = fill
    result["expires_at"] = (OPENED + timedelta(hours=27)).isoformat()
    return result


class DailyEURUSDExecutionCostIntegrationTests(unittest.TestCase):
    def test_new_daily_fill_freezes_contract_and_round_trip_charges_both_sides(self):
        for direction in ("LONG", "SHORT"):
            with self.subTest(direction=direction):
                p = position(direction)
                before = copy.deepcopy(p)
                execution = p["execution_price_engine"]
                self.assertEqual(execution["execution_cost_contract"], lifecycle.EXECUTION_COST_CONTRACT)
                exit_price = lifecycle.execution_exit_price(p, 1.1)
                sign = 1 if direction == "LONG" else -1
                self.assertAlmostEqual(sign * (exit_price - p["entry"]), -.00030)
                self.assertEqual(p, before)

    def test_sl_and_tp_use_bid_for_long_and_ask_for_short(self):
        for direction in ("LONG", "SHORT"):
            for reason in ("STOP_LOSS", "TAKE_PROFIT"):
                with self.subTest(direction=direction, reason=reason):
                    p = position(direction)
                    level = p["stop"] if reason == "STOP_LOSS" else p["target"]
                    # A LONG's BID is 1.5 pips below MID; a SHORT's ASK is above.
                    shift = .00015 if direction == "LONG" else -.00015
                    touch_mid = round(level + shift, 5)
                    touch = bar(1, touch_mid)
                    trade = lifecycle.evaluate_position(p, [touch], touch.timestamp)
                    self.assertIsNotNone(trade)
                    self.assertEqual(trade["exit_reason"], reason)
                    self.assertEqual(trade["exit_price"], level)
                    self.assertEqual(trade["monitor"]["execution_cost_contract"], lifecycle.EXECUTION_COST_CONTRACT)
                    self.assertEqual(trade["monitor"]["synthetic_exit_buffer_pips"], 1.5)

                    # Merely touching the target with MID cannot invent a fill.
                    if reason == "TAKE_PROFIT":
                        mid_only = bar(1, level)
                        self.assertIsNone(lifecycle.evaluate_position(p, [mid_only], mid_only.timestamp))

    def test_hard_horizon_exit_charges_buffer_once(self):
        for direction in ("LONG", "SHORT"):
            with self.subTest(direction=direction):
                p = position(direction)
                bars = [bar(0, 1.1), bar(27, 1.101)]
                trade = dynamic.evaluate_position(p, bars, bars[-1].timestamp)
                self.assertEqual(trade["exit_reason"], "TIME_EXIT_27H")
                self.assertEqual(trade["exit_price"], lifecycle.execution_exit_price(p, 1.101))
                self.assertEqual(trade["monitor"]["dynamic_exit"]["mark_price"], trade["exit_price"])

    def test_r_pace_diagnostics_and_dynamic_close_share_execution_basis(self):
        for direction in ("LONG", "SHORT"):
            with self.subTest(direction=direction):
                p = position(direction)
                sign = 1 if direction == "LONG" else -1
                bars = [
                    bar(0, 1.1),
                    bar(3, 1.1 + sign * .007, **({"high": 1.108} if sign == 1 else {"low": 1.092})),
                    bar(5, 1.1 + sign * .006),
                    bar(6, 1.1 + sign * .0035),
                ]
                diag = dynamic.dynamic_exit_diagnostics(p, bars, bars[-1].timestamp)
                expected_exit = lifecycle.execution_exit_price(p, bars[-1].close)
                risk = abs(p["entry"] - p["stop"])
                self.assertAlmostEqual(diag["current_r"], sign * (expected_exit - p["entry"]) / risk, places=4)
                self.assertEqual(diag["mark_price"], expected_exit)
                expected_best = lifecycle.execution_exit_price(p, 1.1 + sign * .008)
                self.assertAlmostEqual(diag["mfe_r"], sign * (expected_best - p["entry"]) / risk, places=4)
                trade = dynamic.evaluate_position(p, bars, bars[-1].timestamp)
                self.assertEqual(trade["exit_reason"], "DYNAMIC_PROFIT_EXIT")
                self.assertEqual(trade["exit_price"], expected_exit)
                self.assertEqual(trade["r_multiple"], diag["current_r"])

    def test_macro_and_event_exits_use_the_same_execution_price(self):
        for direction in ("LONG", "SHORT"):
            with self.subTest(direction=direction):
                p = position(direction)
                p["_management_candidate"] = {"direction": direction}
                p["_belief_macro_context"] = {
                    "available": True,
                    "score": -7.5 if direction == "LONG" else 7.5,
                    "macro_calendar": {"events": []},
                }
                bars = [bar(0, 1.1), bar(.5, 1.1001)]
                expected_exit = lifecycle.execution_exit_price(p, bars[-1].close)
                macro = macro_risk.maybe_close_position(p, bars, bars[-1].timestamp)
                self.assertEqual(macro["exit_price"], expected_exit)
                sign = 1 if direction == "LONG" else -1
                expected_r = sign * (expected_exit - p["entry"]) / abs(p["entry"] - p["stop"])
                self.assertAlmostEqual(macro["macro_event_risk"]["current_r"], expected_r, places=4)
                with patch.object(event_risk, "build_context", return_value={}), patch.object(event_risk, "position_decision", return_value="CLOSE"):
                    event = event_risk.maybe_close_position(p, bars, bars[-1].timestamp)
                self.assertEqual(event["exit_price"], expected_exit)

    def test_legacy_public_mid_positions_are_not_reinterpreted(self):
        for price_type in ("PUBLIC_CURRENT_MID_PLUS_CONSERVATIVE_BUFFER", "LIVE_CONSENSUS_MID_PLUS_CONSERVATIVE_BUFFER"):
            p = position("LONG")
            p["execution_price_engine"].pop("execution_cost_contract")
            p["execution_price_engine"]["price_type"] = price_type
            self.assertEqual(lifecycle.execution_exit_price(p, 1.1), 1.1)
            self.assertEqual(lifecycle.execution_risk_price(p, 1.1), 1.1)
            bars = [bar(1, 1.101)]
            trade = dynamic._dynamic_close(p, bars, "DYNAMIC_PROFIT_EXIT", {})
            self.assertEqual(trade["exit_price"], 1.101)
            self.assertNotIn("execution_cost_contract", trade["monitor"])

    def test_old_synthetic_contract_keeps_hard_exit_and_dynamic_legacy_semantics(self):
        p = position("SHORT")
        p["execution_price_engine"] = {
            "price_type": "MID_VERIFIED_SYNTHETIC_SPREAD",
            "synthetic_spread_pips": 1.5,
            "synthetic_half_spread_pips": .75,
        }
        self.assertEqual(lifecycle.execution_exit_price(p, 1.1), 1.10008)
        self.assertEqual(lifecycle.execution_risk_price(p, 1.1), 1.1)
        bars = [bar(1, 1.101)]
        self.assertEqual(dynamic._dynamic_close(p, bars, "DYNAMIC_RISK_EXIT", {})["exit_price"], 1.101)

    def test_invalid_versioned_cost_parameters_fail_closed(self):
        for invalid in (float("nan"), float("inf"), -1, 0, None):
            with self.subTest(invalid=invalid):
                p = position("LONG")
                p["execution_price_engine"]["synthetic_half_spread_pips"] = invalid
                with self.assertRaises(ValueError):
                    lifecycle.execution_exit_price(p, 1.1)
        p = position("LONG")
        p["execution_price_engine"]["synthetic_spread_pips"] = 9
        with self.assertRaises(ValueError):
            lifecycle.execution_exit_price(p, 1.1)
        p["execution_price_engine"]["execution_cost_contract"] = "UNKNOWN"
        with self.assertRaises(ValueError):
            lifecycle.execution_risk_price(p, 1.1)


if __name__ == "__main__":
    unittest.main()
