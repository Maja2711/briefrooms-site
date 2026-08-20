from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.belief_market_data_adapter import Bar
from scripts.daily_eurusd_experiment import (
    BELIEF_WEIGHTS,
    build_capture,
    empty_state,
    append_capture,
    performance_summary,
    resolve_outcomes,
    technical_snapshot,
    validate_state,
)


def bars(start: float = 1.16, drift: float = 0.00014, n: int = 90) -> list[Bar]:
    t0 = datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc)
    out: list[Bar] = []
    price = start
    for i in range(n):
        price *= 1.0 + drift
        out.append(
            Bar(
                timestamp=t0 + timedelta(minutes=30 * i),
                open=price * 0.9998,
                high=price * 1.0007,
                low=price * 0.9993,
                close=price,
                volume=1000 + i,
            )
        )
    return out


def belief_payload(observed_at: datetime, *, future: bool = False) -> dict:
    updated = observed_at + timedelta(minutes=5) if future else observed_at - timedelta(minutes=15)
    rows = []
    values = {
        "eurusd.trend.bullish": (0.66, 0.60),
        "eurusd.usd_environment.supportive": (0.61, 0.55),
        "eurusd.us_rates_pressure.supportive": (0.56, 0.50),
    }
    for belief_id in BELIEF_WEIGHTS:
        probability, confidence = values[belief_id]
        rows.append(
            {
                "belief_id": belief_id,
                "probability": probability,
                "confidence": confidence,
                "last_updated": updated.isoformat().replace("+00:00", "Z"),
            }
        )
    return {"schema_version": 2, "beliefs": rows}


class DailyEURUSDABCExperimentTests(unittest.TestCase):
    def test_arm_a_contains_requested_technical_indicator_families(self):
        snapshot = technical_snapshot(bars())
        indicators = snapshot["indicators"]
        self.assertIn("sma20", indicators)
        self.assertIn("sma50", indicators)
        self.assertIn("ema20", indicators)
        self.assertIn("rsi14", indicators)
        self.assertIn("trendline_slope", indicators)
        self.assertIn("trendline_r2", indicators)
        self.assertIn("support", indicators)
        self.assertIn("resistance", indicators)
        self.assertIn("atr14", indicators)
        self.assertEqual(
            set(snapshot["components"]),
            {"ma_structure", "rsi_momentum", "trendline", "support_resistance", "price_momentum"},
        )

    def test_three_arms_share_one_frozen_market_reference_and_c_controls_overlap(self):
        rows = bars()
        observed = rows[-1].timestamp
        capture = build_capture(rows, belief_payload(observed), captured_at=observed + timedelta(minutes=1))
        self.assertEqual(set(capture["arms"]), {"A", "B", "C"})
        self.assertTrue(capture["arms"]["A"]["available"])
        self.assertTrue(capture["arms"]["B"]["available"])
        self.assertTrue(capture["arms"]["C"]["available"])
        self.assertIn("technical", capture["arms"]["A"])
        self.assertNotIn("technical", capture["arms"]["B"])
        self.assertIn("technical", capture["arms"]["C"])
        self.assertEqual(
            capture["arms"]["C"]["overlap_control"]["excluded_from_hybrid_context"],
            ["eurusd.trend.bullish"],
        )
        self.assertFalse(capture["research_boundary"]["decision_influence"])
        self.assertFalse(capture["research_boundary"]["pnl_tuned_weights"])

    def test_future_belief_state_is_rejected_fail_closed_for_b_and_c(self):
        rows = bars()
        observed = rows[-1].timestamp
        capture = build_capture(rows, belief_payload(observed, future=True), captured_at=observed + timedelta(minutes=1))
        self.assertTrue(capture["arms"]["A"]["available"])
        self.assertFalse(capture["arms"]["B"]["available"])
        self.assertEqual(capture["arms"]["B"]["belief"]["reason"], "future_belief_state_rejected")
        self.assertFalse(capture["arms"]["C"]["available"])

    def test_duplicate_market_bar_never_rewrites_frozen_decision(self):
        rows = bars()
        observed = rows[-1].timestamp
        capture = build_capture(rows, belief_payload(observed), captured_at=observed + timedelta(minutes=1))
        state = append_capture(empty_state(observed), capture)
        again = append_capture(state, capture)
        self.assertEqual(len(again["captures"]), 1)
        self.assertEqual(again["captures"][0]["decision_sha256"], capture["decision_sha256"])
        validate_state(again)

    def test_forward_outcome_resolution_preserves_decision_hash(self):
        decision_rows = bars(n=90)
        observed = decision_rows[-1].timestamp
        capture = build_capture(
            decision_rows,
            belief_payload(observed),
            captured_at=observed + timedelta(minutes=1),
        )
        state = append_capture(empty_state(observed), capture)
        frozen_hash = capture["decision_sha256"]

        outcome_rows = bars(n=150)
        resolved = resolve_outcomes(state, outcome_rows)
        self.assertEqual(resolved["captures"][0]["decision_sha256"], frozen_hash)
        self.assertIsNotNone(resolved["captures"][0]["horizons"]["30m"]["outcome"])
        self.assertIsNotNone(resolved["captures"][0]["horizons"]["1440m"]["outcome"])
        validate_state(resolved)

    def test_performance_summary_keeps_signal_and_no_trade_semantics_separate(self):
        decision_rows = bars(n=90)
        observed = decision_rows[-1].timestamp
        capture = build_capture(
            decision_rows,
            belief_payload(observed),
            captured_at=observed + timedelta(minutes=1),
        )
        state = append_capture(empty_state(observed), capture)
        resolved = resolve_outcomes(state, bars(n=150))
        summary = performance_summary(resolved)
        self.assertEqual(set(summary), {"A", "B", "C"})
        self.assertEqual(summary["A"]["30m"]["matured_captures"], 1)
        self.assertGreaterEqual(summary["A"]["30m"]["available_captures"], 1)


if __name__ == "__main__":
    unittest.main()
