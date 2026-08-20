from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.belief_market_data_adapter import Bar
from scripts.daily_eurusd_experiment_v12 import (
    BELIEF_WEIGHTS,
    BOLLINGER_STDDEV_LEVELS,
    BOLLINGER_WINDOW,
    ENGINE_VERSION,
    MA_WINDOWS,
    append_capture,
    build_capture,
    empty_state,
    performance_summary,
    resolve_outcomes,
    technical_snapshot,
    validate_state,
)

UTC = timezone.utc
OBSERVED = datetime(2026, 8, 20, 18, 30, tzinfo=UTC)


def series_ending(end: datetime, *, start: float, drift: float, n: int, step: timedelta) -> list[Bar]:
    price = start
    out: list[Bar] = []
    first = end - step * (n - 1)
    for i in range(n):
        price *= 1.0 + drift
        out.append(Bar(
            timestamp=first + step * i,
            open=price * 0.9995,
            high=price * 1.0015,
            low=price * 0.9985,
            close=price,
            volume=1000 + i,
        ))
    return out


def market_sets():
    rows_30m = series_ending(OBSERVED, start=1.15, drift=0.00008, n=90, step=timedelta(minutes=30))
    h1 = series_ending(OBSERVED - timedelta(minutes=30), start=1.12, drift=0.00005, n=260, step=timedelta(hours=1))
    d1_end = OBSERVED.replace(hour=0, minute=0)
    d1 = series_ending(d1_end, start=1.05, drift=0.00035, n=260, step=timedelta(days=1))
    return rows_30m, h1, d1


def belief_payload(observed_at: datetime, *, future: bool = False) -> dict:
    updated = observed_at + timedelta(minutes=5) if future else observed_at - timedelta(minutes=15)
    values = {
        "eurusd.trend.bullish": (0.66, 0.60),
        "eurusd.usd_environment.supportive": (0.61, 0.55),
        "eurusd.us_rates_pressure.supportive": (0.56, 0.50),
    }
    return {
        "schema_version": 2,
        "beliefs": [
            {
                "belief_id": belief_id,
                "probability": values[belief_id][0],
                "confidence": values[belief_id][1],
                "last_updated": updated.isoformat().replace("+00:00", "Z"),
            }
            for belief_id in BELIEF_WEIGHTS
        ],
    }


def extend_30m(rows: list[Bar], future_bars: int = 60) -> list[Bar]:
    out = list(rows)
    price = float(rows[-1].close)
    ts = rows[-1].timestamp
    for i in range(future_bars):
        price *= 1.00008
        ts += timedelta(minutes=30)
        out.append(Bar(ts, price, open=price * .9995, high=price * 1.001, low=price * .999, volume=2000 + i))
    return out


class DailyEURUSDABCExperimentTests(unittest.TestCase):
    def test_requested_multitimeframe_technical_contract(self):
        rows_30m, h1, d1 = market_sets()
        snap = technical_snapshot(h1, d1, reference_price=rows_30m[-1].close, observed_at=OBSERVED)
        indicators = snap["indicators"]
        self.assertEqual(MA_WINDOWS, (30, 60, 100, 200))
        self.assertEqual(BOLLINGER_WINDOW, 30)
        self.assertEqual(BOLLINGER_STDDEV_LEVELS, (1.0, 2.0, 3.0))
        self.assertEqual(ENGINE_VERSION, "eurusd-daily-abc-v1.2.0")
        for tf in ("H1", "D1"):
            self.assertEqual(set(indicators[tf]["ma"]["values"]), {"ma30", "ma60", "ma100", "ma200"})
            self.assertIn("macd", indicators[tf])
            self.assertEqual(indicators[tf]["macd"]["parameters"], {"fast": 12, "slow": 26, "signal": 9})
            boll = indicators[tf]["bollinger"]
            self.assertEqual(
                boll["parameters"],
                {
                    "window": 30,
                    "stddev_levels": [1.0, 2.0, 3.0],
                    "score_reference_stddev": 2.0,
                    "dispersion": "population_standard_deviation",
                },
            )
            self.assertIn("sigma", boll)
            self.assertIn("z_score", boll)
            self.assertTrue({
                "upper_1sigma", "lower_1sigma",
                "upper_2sigma", "lower_2sigma",
                "upper_3sigma", "lower_3sigma",
            }.issubset(boll))
            self.assertEqual(boll["upper"], boll["upper_2sigma"])
            self.assertEqual(boll["lower"], boll["lower_2sigma"])
        pivot = indicators["pivot"]
        self.assertEqual(pivot["method"], "classic_floor_pivot")
        self.assertTrue({"pivot", "r1", "r2", "r3", "s1", "s2", "s3"}.issubset(pivot))
        self.assertNotIn("sma20", indicators["H1"])
        self.assertNotIn("sma50", indicators["H1"])
        self.assertNotIn("ema20", indicators["H1"])

    def test_three_arms_and_overlap_control(self):
        rows_30m, h1, d1 = market_sets()
        capture = build_capture(
            rows_30m,
            belief_payload(OBSERVED),
            hourly_rows=h1,
            daily_rows=d1,
            captured_at=OBSERVED + timedelta(minutes=1),
        )
        self.assertEqual(capture["engine_version"], "eurusd-daily-abc-v1.2.0")
        self.assertEqual(set(capture["arms"]), {"A", "B", "C"})
        self.assertTrue(capture["arms"]["A"]["available"])
        self.assertTrue(capture["arms"]["B"]["available"])
        self.assertTrue(capture["arms"]["C"]["available"])
        self.assertNotIn("technical", capture["arms"]["B"])
        self.assertEqual(capture["arms"]["C"]["overlap_control"]["excluded_from_hybrid_context"], ["eurusd.trend.bullish"])
        validate_state(append_capture(empty_state(OBSERVED), capture))

    def test_future_belief_fail_closed_for_b_and_c(self):
        rows_30m, h1, d1 = market_sets()
        capture = build_capture(
            rows_30m,
            belief_payload(OBSERVED, future=True),
            hourly_rows=h1,
            daily_rows=d1,
            captured_at=OBSERVED + timedelta(minutes=1),
        )
        self.assertTrue(capture["arms"]["A"]["available"])
        self.assertFalse(capture["arms"]["B"]["available"])
        self.assertFalse(capture["arms"]["C"]["available"])
        self.assertEqual(capture["arms"]["B"]["belief"]["reason"], "future_belief_state_rejected")

    def test_forward_outcome_resolution_preserves_frozen_decision(self):
        rows_30m, h1, d1 = market_sets()
        capture = build_capture(
            rows_30m,
            belief_payload(OBSERVED),
            hourly_rows=h1,
            daily_rows=d1,
            captured_at=OBSERVED + timedelta(minutes=1),
        )
        frozen = capture["decision_sha256"]
        state = append_capture(empty_state(OBSERVED), capture)
        resolved = resolve_outcomes(state, extend_30m(rows_30m))
        self.assertEqual(resolved["captures"][0]["decision_sha256"], frozen)
        self.assertIsNotNone(resolved["captures"][0]["horizons"]["30m"]["outcome"])
        self.assertIsNotNone(resolved["captures"][0]["horizons"]["1440m"]["outcome"])
        validate_state(resolved)

    def test_performance_summary_survives_multitimeframe_upgrade(self):
        rows_30m, h1, d1 = market_sets()
        capture = build_capture(
            rows_30m,
            belief_payload(OBSERVED),
            hourly_rows=h1,
            daily_rows=d1,
            captured_at=OBSERVED + timedelta(minutes=1),
        )
        state = resolve_outcomes(append_capture(empty_state(OBSERVED), capture), extend_30m(rows_30m))
        summary = performance_summary(state)
        self.assertEqual(set(summary), {"A", "B", "C"})
        self.assertEqual(summary["A"]["30m"]["matured_captures"], 1)


if __name__ == "__main__":
    unittest.main()
