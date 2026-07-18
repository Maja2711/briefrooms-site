#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import investments_weekly_v3 as v3  # noqa: E402

TZ = ZoneInfo("Europe/Warsaw")


class ContinuousWeeklyExposureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = {
            "policy_version": "test",
            "minimum_minutes_after_exit": 5,
            "weekly_candle_model": {"tie_break_direction": "long"},
            "risk_and_costs": {"sp500_round_trip_cost_points": 1.0},
        }

    def test_directional_v2_signal_is_kept(self) -> None:
        out = v3.choose_direction(
            {"direction": "short", "score": -55, "data_quality": "passed"},
            {"score": 80, "regime": "trend_up:vol_normal", "data_quality": "passed"},
            {"regimes": {}},
            self.policy,
        )
        self.assertEqual(out["direction"], "short")
        self.assertEqual(out["reason"], "v2_directional_signal")

    def test_neutral_signal_uses_positive_weekly_score(self) -> None:
        out = v3.choose_direction(
            {"direction": "neutral", "score": 5, "data_quality": "passed"},
            {"score": 40, "regime": "trend_up:vol_normal", "data_quality": "passed"},
            {"regimes": {}},
            self.policy,
        )
        self.assertEqual(out["direction"], "long")
        self.assertGreater(out["combined_score"], 0)

    def test_neutral_signal_uses_negative_weekly_score(self) -> None:
        out = v3.choose_direction(
            {"direction": "neutral", "score": -4, "data_quality": "passed"},
            {"score": -50, "regime": "trend_down:vol_high", "data_quality": "passed"},
            {"regimes": {}},
            self.policy,
        )
        self.assertEqual(out["direction"], "short")
        self.assertLess(out["combined_score"], 0)

    def test_adaptive_adjustment_is_used_only_when_eligible(self) -> None:
        state = {
            "regimes": {
                "trend_flat:vol_normal": {
                    "eligible": True,
                    "score_adjustment": -12.0,
                }
            }
        }
        out = v3.choose_direction(
            {"direction": "neutral", "score": 0, "data_quality": "passed"},
            {"score": 10, "regime": "trend_flat:vol_normal", "data_quality": "passed"},
            state,
            self.policy,
        )
        self.assertEqual(out["adaptive_adjustment"], -12.0)
        self.assertEqual(out["direction"], "short")

    def test_zero_score_uses_explicit_tie_break(self) -> None:
        out = v3.choose_direction(
            {"direction": "neutral", "score": 0, "data_quality": "passed"},
            {"score": 0, "regime": "unknown", "data_quality": "failed"},
            {"regimes": {}},
            self.policy,
        )
        self.assertEqual(out["direction"], "long")

    def test_closed_leg_is_archived_once_and_cost_is_subtracted(self) -> None:
        item = {
            "instrument_id": "sp500_futures",
            "symbol": "ES=F",
            "direction": "long",
            "entry_price": 5000.0,
            "entry_captured_at": "2026-07-20T08:00:00+02:00",
            "entry_source": "test",
            "exit_price": 5050.0,
            "exit_captured_at": "2026-07-21T10:00:00+02:00",
            "exit_source": "test",
            "exit_reason": "take_profit",
            "result_percent": 1.0,
            "continuous_entry_regime": "trend_up:vol_normal",
            "continuous_entry_decision": {"direction": "long"},
            "risk_plan": {"stop_loss_price": 4950.0},
        }
        self.assertTrue(v3.archive_closed_leg(item, self.policy))
        self.assertFalse(v3.archive_closed_leg(item, self.policy))
        self.assertEqual(len(item["position_legs"]), 1)
        leg = item["position_legs"][0]
        self.assertAlmostEqual(leg["estimated_round_trip_cost_percent"], 0.02, places=6)
        self.assertAlmostEqual(leg["net_result_percent"], 0.98, places=6)

    def test_monday_before_target_is_not_active(self) -> None:
        week = {
            "market_window": {
                "entry_target_local": "2026-07-20T08:00:00+02:00",
                "exit_target_local": "2026-07-24T22:00:00+02:00",
            }
        }
        active, reason = v3._within_exposure_window(week, datetime(2026, 7, 20, 7, 59, tzinfo=TZ), self.policy)
        self.assertFalse(active)
        self.assertEqual(reason, "before_monday_08_00")

    def test_monday_after_target_is_active(self) -> None:
        week = {
            "market_window": {
                "entry_target_local": "2026-07-20T08:00:00+02:00",
                "exit_target_local": "2026-07-24T22:00:00+02:00",
            }
        }
        active, reason = v3._within_exposure_window(week, datetime(2026, 7, 20, 8, 5, tzinfo=TZ), self.policy)
        self.assertTrue(active)
        self.assertEqual(reason, "active_week_window")

    def test_friday_after_scheduled_close_is_inactive(self) -> None:
        week = {
            "market_window": {
                "entry_target_local": "2026-07-20T08:00:00+02:00",
                "exit_target_local": "2026-07-24T22:00:00+02:00",
            }
        }
        active, reason = v3._within_exposure_window(week, datetime(2026, 7, 24, 22, 1, tzinfo=TZ), self.policy)
        self.assertFalse(active)
        self.assertEqual(reason, "after_scheduled_friday_close")


if __name__ == "__main__":
    unittest.main()
