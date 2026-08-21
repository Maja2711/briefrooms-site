from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.belief_market_data_adapter import Bar
from scripts import daily_eurusd_experiment_v13 as abc

UTC = timezone.utc
OBSERVED = datetime(2026, 8, 20, 18, 30, tzinfo=UTC)


def series_ending(end: datetime, *, start: float, drift: float, n: int, step: timedelta) -> list[Bar]:
    price = start
    rows: list[Bar] = []
    first = end - step * (n - 1)
    for i in range(n):
        price *= 1.0 + drift
        rows.append(Bar(
            timestamp=first + step * i,
            open=price * 0.9995,
            high=price * 1.0015,
            low=price * 0.9985,
            close=price,
            volume=1000 + i,
        ))
    return rows


def market_sets() -> tuple[list[Bar], list[Bar], list[Bar]]:
    rows_30m = series_ending(OBSERVED, start=1.15, drift=0.00008, n=90, step=timedelta(minutes=30))
    h1 = series_ending(OBSERVED - timedelta(minutes=30), start=1.12, drift=0.00005, n=260, step=timedelta(hours=1))
    d1 = series_ending(OBSERVED.replace(hour=0, minute=0), start=1.05, drift=0.00035, n=260, step=timedelta(days=1))
    return rows_30m, h1, d1


def belief_payload() -> dict:
    updated = OBSERVED - timedelta(minutes=15)
    values = {
        "eurusd.trend.bullish": (0.66, 0.60),
        "eurusd.usd_environment.supportive": (0.61, 0.55),
        "eurusd.us_rates_pressure.supportive": (0.56, 0.50),
    }
    return {
        "beliefs": [
            {
                "belief_id": belief_id,
                "probability": probability,
                "confidence": confidence,
                "last_updated": updated.isoformat().replace("+00:00", "Z"),
            }
            for belief_id, (probability, confidence) in values.items()
        ]
    }


def plan(direction: str) -> dict:
    if direction == "LONG":
        return {
            "available": True,
            "direction": "LONG",
            "status": "TRACKED",
            "entry_price": 1.00000,
            "stop_price": 0.99000,
            "target_price": 1.01800,
        }
    return {
        "available": True,
        "direction": "SHORT",
        "status": "TRACKED",
        "entry_price": 1.00000,
        "stop_price": 1.01000,
        "target_price": 0.98200,
    }


def minute_bar(minutes: int, *, close: float, high: float, low: float) -> Bar:
    return Bar(
        timestamp=OBSERVED + timedelta(minutes=minutes),
        open=1.0,
        high=high,
        low=low,
        close=close,
        volume=1,
    )


class DailyEURUSDABCTradePathTests(unittest.TestCase):
    def test_v13_capture_freezes_active_engine_parity_risk_plan(self):
        rows_30m, h1, d1 = market_sets()
        capture = abc.build_capture(
            rows_30m,
            belief_payload(),
            hourly_rows=h1,
            daily_rows=d1,
            captured_at=OBSERVED + timedelta(minutes=1),
        )
        self.assertEqual(capture["engine_version"], "eurusd-daily-abc-v1.3.0")
        risk = capture["trade_plan"]["risk_contract"]
        self.assertEqual(risk["atr_window"], 26)
        self.assertEqual(risk["atr_multiple"], 1.35)
        self.assertEqual(risk["risk_floor_percent"], 0.0027)
        self.assertEqual(risk["reward_risk"], 1.8)
        self.assertEqual(risk["position_horizon_minutes"], 1440)
        self.assertEqual(risk["monitor_interval"], "1m")
        self.assertEqual(
            abc.base._canonical_sha(capture["trade_plan"]),
            capture["trade_plan_sha256"],
        )
        state = abc.append_capture(abc.empty_state(OBSERVED), capture)
        abc.validate_state(state)
        self.assertFalse(capture["research_boundary"]["trade_execution"])
        self.assertFalse(capture["research_boundary"]["decision_influence"])

    def test_long_take_profit_first_records_mfe_mae_and_realized_result(self):
        rows = [
            minute_bar(1, close=1.004, high=1.006, low=0.998),
            minute_bar(2, close=1.018, high=1.019, low=1.002),
        ]
        result = abc.evaluate_arm_trade_path(
            plan("LONG"), rows,
            signal_generated_at=OBSERVED,
            horizon_end_at=OBSERVED + timedelta(hours=24),
            as_of=OBSERVED + timedelta(minutes=3),
        )
        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual(result["exit_reason"], "TAKE_PROFIT")
        self.assertEqual(result["first_touch"], "TAKE_PROFIT")
        self.assertEqual(result["minutes_to_first_touch"], 2.0)
        self.assertGreater(result["mfe_bps"], 0)
        self.assertLessEqual(result["mae_bps"], 0)
        self.assertGreater(result["realized_bps"], 0)

    def test_short_take_profit_first_is_directionally_symmetric(self):
        rows = [
            minute_bar(1, close=0.996, high=1.002, low=0.994),
            minute_bar(2, close=0.982, high=0.998, low=0.981),
        ]
        result = abc.evaluate_arm_trade_path(
            plan("SHORT"), rows,
            signal_generated_at=OBSERVED,
            horizon_end_at=OBSERVED + timedelta(hours=24),
            as_of=OBSERVED + timedelta(minutes=3),
        )
        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual(result["exit_reason"], "TAKE_PROFIT")
        self.assertGreater(result["realized_bps"], 0)
        self.assertGreater(result["mfe_bps"], 0)
        self.assertLessEqual(result["mae_bps"], 0)

    def test_same_one_minute_bar_touching_tp_and_sl_is_fail_closed_ambiguous(self):
        rows = [minute_bar(1, close=1.001, high=1.020, low=0.988)]
        result = abc.evaluate_arm_trade_path(
            plan("LONG"), rows,
            signal_generated_at=OBSERVED,
            horizon_end_at=OBSERVED + timedelta(hours=24),
            as_of=OBSERVED + timedelta(minutes=2),
        )
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["exit_reason"], "AMBIGUOUS_SAME_1M_BAR")
        self.assertIsNone(result["realized_bps"])
        self.assertEqual(result["minutes_to_first_touch"], 1.0)

    def test_time_exit_uses_last_available_close_at_or_before_horizon(self):
        horizon = OBSERVED + timedelta(minutes=10)
        rows = [
            minute_bar(i, close=1.0000 + i * 0.0001, high=1.0005 + i * 0.0001, low=0.9995 + i * 0.0001)
            for i in range(1, 11)
        ]
        result = abc.evaluate_arm_trade_path(
            plan("LONG"), rows,
            signal_generated_at=OBSERVED,
            horizon_end_at=horizon,
            as_of=horizon + timedelta(minutes=1),
        )
        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual(result["exit_reason"], "TIME_EXIT_24H")
        self.assertEqual(result["exit_price"], rows[-1].close)
        self.assertIsNotNone(result["realized_bps"])

    def test_terminal_trade_path_is_append_only_and_frozen_identity_survives(self):
        rows_30m, h1, d1 = market_sets()
        capture = abc.build_capture(
            rows_30m,
            belief_payload(),
            hourly_rows=h1,
            daily_rows=d1,
            captured_at=OBSERVED + timedelta(minutes=1),
        )
        state = abc.append_capture(abc.empty_state(OBSERVED), capture)
        frozen_decision = capture["decision_sha256"]
        frozen_plan = capture["trade_plan_sha256"]

        tracked = [
            arm_id for arm_id, row in capture["trade_plan"]["arms"].items()
            if row["status"] == "TRACKED"
        ]
        self.assertTrue(tracked)
        arm_plan = capture["trade_plan"]["arms"][tracked[0]]
        direction = arm_plan["direction"]
        target = float(arm_plan["target_price"])
        stop = float(arm_plan["stop_price"])
        if direction == "LONG":
            first = Bar(OBSERVED + timedelta(minutes=2), target, open=target, high=target * 1.0001, low=(target + stop) / 2, volume=1)
        else:
            first = Bar(OBSERVED + timedelta(minutes=2), target, open=target, high=(target + stop) / 2, low=target * 0.9999, volume=1)

        updated = abc.update_trade_paths(
            state,
            [first],
            as_of=OBSERVED + timedelta(minutes=3),
        )
        terminal_before = updated["captures"][0]["trade_path"]["arms"][tracked[0]].copy()
        updated_again = abc.update_trade_paths(
            updated,
            [],
            as_of=OBSERVED + timedelta(days=4),
        )
        self.assertEqual(updated_again["captures"][0]["trade_path"]["arms"][tracked[0]], terminal_before)
        self.assertEqual(updated_again["captures"][0]["decision_sha256"], frozen_decision)
        self.assertEqual(updated_again["captures"][0]["trade_plan_sha256"], frozen_plan)
        abc.validate_state(updated_again)


if __name__ == "__main__":
    unittest.main()
