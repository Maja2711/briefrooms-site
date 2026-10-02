from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.belief_market_data_adapter import Bar, MarketSnapshot
from scripts.daily_eurusd_lifecycle import append_trade, empty_history
from scripts import daily_eurusd_spot as base
from scripts.daily_eurusd_spot_v12 import (
    ENGINE_VERSION,
    direct_entry_gate,
    direct_learning_state,
)


def aligned_snapshot(*, proxy_lag_minutes: int = 0) -> MarketSnapshot:
    end = datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc)
    def rows(symbol: str, lag: int, drift: float) -> list[Bar]:
        out=[]
        start=end-timedelta(minutes=30*79)-timedelta(minutes=lag)
        price=1.10 if symbol=="EURUSD=X" else 25.0 if symbol=="UUP" else 90.0
        for i in range(80):
            price *= 1.0 + drift
            out.append(Bar(
                timestamp=start+timedelta(minutes=30*i),
                open=price,
                high=price*1.0005,
                low=price*0.9995,
                close=price,
                volume=1000+i,
            ))
        return out
    return MarketSnapshot({
        "EURUSD=X": rows("EURUSD=X",0,0.0008),
        "UUP": rows("UUP",proxy_lag_minutes,-0.0008),
        "TLT": rows("TLT",proxy_lag_minutes,0.0008),
    })


class DailyEurusdDirectSignalAdmissionTests(unittest.TestCase):
    def _loss_history(self):
        return append_trade(empty_history(), {
            "trade_id": "loss-direct-1",
            "direction": "LONG",
            "opened_at": "2026-08-20T10:00:00Z",
            "closed_at": "2026-08-20T11:00:00Z",
            "r_multiple": -1.0,
            "exit_reason": "STOP_LOSS",
            "entry_components": {
                "trend": 0.4,
                "broad_usd_environment": 0.2,
                "us_rates_pressure_proxy": 0.1,
            },
        })

    def test_engine_version_is_v12(self):
        self.assertEqual(ENGINE_VERSION, "eurusd-daily-spot-v1.2.0")

    def test_long_is_admitted_despite_all_legacy_veto_conditions(self):
        history = self._loss_history()
        gate = direct_entry_gate(
            direction="LONG",
            score=60.0,
            confidence=0.0,
            history=history,
            observed_at=datetime(2026, 8, 20, 11, 1, tzinfo=timezone.utc),
            previous_score=10.0,
            stretch_atr=99.0,
            shock_ratio=99.0,
        )
        self.assertTrue(gate["accepted"])
        self.assertEqual(gate["reasons"], [])

    def test_short_is_admitted_despite_all_legacy_veto_conditions(self):
        history = self._loss_history()
        gate = direct_entry_gate(
            direction="SHORT",
            score=40.0,
            confidence=0.0,
            history=history,
            observed_at=datetime(2026, 8, 20, 11, 1, tzinfo=timezone.utc),
            previous_score=90.0,
            stretch_atr=99.0,
            shock_ratio=99.0,
        )
        self.assertTrue(gate["accepted"])
        self.assertEqual(gate["reasons"], [])

    def test_flat_remains_no_trade(self):
        gate = direct_entry_gate(
            direction="FLAT",
            score=50.0,
            confidence=0.0,
            history=empty_history(),
            observed_at=datetime(2026, 8, 20, 11, 1, tzinfo=timezone.utc),
            previous_score=50.0,
            stretch_atr=0.0,
            shock_ratio=0.0,
        )
        self.assertFalse(gate["accepted"])
        self.assertEqual(gate["reasons"], ["raw_score_neutral"])

    def test_cross_asset_timestamp_gap_blocks_even_direct_native_signal(self):
        output = base.build_output(aligned_snapshot(proxy_lag_minutes=180), empty_history())
        self.assertEqual(output.direction, "FLAT")
        self.assertFalse(output.metadata["candidate"]["accepted"])
        self.assertIn("cross_asset_data_misaligned", output.metadata["candidate"]["gate_reasons"])
        alignment = output.metadata["data"]["cross_asset_alignment"]
        self.assertFalse(alignment["passed"])
        self.assertGreater(alignment["max_gap_minutes"], 90.0)

    def test_aligned_cross_asset_inputs_do_not_create_alignment_block(self):
        output = base.build_output(aligned_snapshot(proxy_lag_minutes=0), empty_history())
        alignment = output.metadata["data"]["cross_asset_alignment"]
        self.assertTrue(alignment["passed"])
        self.assertNotIn("cross_asset_data_misaligned", output.metadata["candidate"]["gate_reasons"])
        self.assertIn(output.direction, {"LONG", "SHORT", "FLAT"})

    def test_learning_keeps_weights_but_has_no_admission_limits(self):
        state = direct_learning_state(self._loss_history()["trades"])
        self.assertEqual(state["entry_thresholds"], {
            "long": 60.0,
            "short": 40.0,
            "min_confidence": 0.0,
        })
        self.assertIsNone(state["cooldown_until"])
        self.assertIsNone(state["policy"]["daily_entry_limit"])
        self.assertEqual(state["policy"]["loss_cooldown_hours"], 0)
        self.assertEqual(state["policy"]["blocking_filters"], [])
        self.assertTrue(state["policy"]["one_open_position_at_a_time"])
        self.assertIn("adaptive_weights", state)


if __name__ == "__main__":
    unittest.main()
