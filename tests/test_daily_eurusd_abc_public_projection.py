from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.daily_eurusd_abc_public_projection import (
    DISALLOWED_PUBLIC_KEYS,
    HORIZONS,
    SCHEMA_VERSION,
    build_public_projection,
    validate_public_projection,
    _walk_keys,
)

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]


def private_state() -> dict:
    horizons = {}
    for key, minutes in (("30m", 30), ("60m", 60), ("120m", 120), ("240m", 240), ("1440m", 1440)):
        outcome = None
        if key == "30m":
            outcome = {
                "resolved_at": "2026-08-20T19:30:00Z",
                "price": 1.168,
                "raw_return_bps": 3.0,
                "arms": {
                    "A": {"available": True, "direction": "LONG", "directional_correct": True, "signed_return_bps": 3.0},
                    "B": {"available": True, "direction": "FLAT", "directional_correct": None, "signed_return_bps": 0.0},
                    "C": {"available": True, "direction": "LONG", "directional_correct": True, "signed_return_bps": 3.0},
                },
            }
        horizons[key] = {"minutes": minutes, "target_at": "2026-08-20T20:00:00Z", "outcome": outcome}

    trade_plan = {
        "schema_version": "eurusd-abc-virtual-trade-plan-v1",
        "mode": "research_shadow",
        "entry_basis": "frozen_reference_price_not_executable_quote",
        "signal_generated_at": "2026-08-20T19:01:12Z",
        "horizon_end_at": "2026-08-21T19:01:12Z",
        "risk_contract": {
            "source": "active_daily_eurusd_v1.2_parity",
            "atr_timeframe": "30m",
            "atr_window": 26,
            "atr_value": 0.0017,
            "atr_multiple": 1.35,
            "risk_floor_percent": 0.0027,
            "risk_distance": 0.00315,
            "reward_risk": 1.8,
            "position_horizon_minutes": 1440,
            "monitor_interval": "1m",
        },
        "arms": {
            "A": {"available": True, "direction": "LONG", "status": "TRACKED", "entry_price": 1.16765, "stop_price": 1.16450, "target_price": 1.17332},
            "B": {"available": True, "direction": "FLAT", "status": "NO_TRADE", "entry_price": None, "stop_price": None, "target_price": None},
            "C": {"available": True, "direction": "LONG", "status": "TRACKED", "entry_price": 1.16765, "stop_price": 1.16450, "target_price": 1.17332},
        },
    }
    trade_path = {
        "schema_version": "eurusd-abc-virtual-trade-path-v1",
        "source_plan_sha256": "private-plan-hash",
        "arms": {
            "A": {
                "status": "CLOSED", "mfe_bps": 51.2, "mfe_at": "2026-08-20T20:15:00Z",
                "mae_bps": -7.4, "mae_at": "2026-08-20T19:08:00Z",
                "first_touch": "TAKE_PROFIT", "first_touch_at": "2026-08-20T20:15:00Z",
                "minutes_to_first_touch": 73.8, "exit_reason": "TAKE_PROFIT",
                "exit_at": "2026-08-20T20:15:00Z", "exit_price": 1.17332, "realized_bps": 48.55,
            },
            "B": {
                "status": "NO_TRADE", "mfe_bps": None, "mfe_at": None, "mae_bps": None, "mae_at": None,
                "first_touch": None, "first_touch_at": None, "minutes_to_first_touch": None,
                "exit_reason": None, "exit_at": None, "exit_price": None, "realized_bps": None,
            },
            "C": {
                "status": "OPEN", "mfe_bps": 12.3, "mfe_at": "2026-08-20T19:40:00Z",
                "mae_bps": -4.2, "mae_at": "2026-08-20T19:20:00Z",
                "first_touch": None, "first_touch_at": None, "minutes_to_first_touch": None,
                "exit_reason": None, "exit_at": None, "exit_price": None, "realized_bps": None,
            },
        },
    }
    return {
        "mode": "research_shadow",
        "updated_at": "2026-08-20T20:15:00Z",
        "captures": [{
            "engine_version": "eurusd-daily-abc-v1.3.0",
            "market_observed_at": "2026-08-20T19:00:00Z",
            "captured_at": "2026-08-20T19:01:12Z",
            "reference_price": 1.16765,
            "decision_sha256": "private-decision-hash",
            "trade_plan_sha256": "private-plan-hash",
            "research_boundary": {"decision_influence": False, "trade_execution": False, "belief_writeback": False},
            "arms": {
                "A": {"available": True, "direction": "LONG", "score": 64.2, "confidence": .284, "technical": {"secret": True}},
                "B": {"available": True, "direction": "FLAT", "score": 54.1, "confidence": 0.0, "belief": {"beliefs": [{"secret": True}]}},
                "C": {"available": True, "direction": "LONG", "score": 61.3, "confidence": .226, "belief_context": {"secret": True}},
            },
            "horizons": horizons,
            "trade_plan": trade_plan,
            "trade_path": trade_path,
        }],
    }


def private_report() -> dict:
    metric = {
        "matured_captures": 4,
        "available_captures": 4,
        "signals": 3,
        "decision_rate": .75,
        "hit_rate": 2 / 3,
        "mean_signed_return_bps_signal_only": 2.5,
        "mean_strategy_return_bps_all_available": 1.875,
    }
    trade_metric = {
        "signals": 5,
        "open_trades": 1,
        "closed_trades": 3,
        "ambiguous_same_1m_bar": 1,
        "take_profit": 2,
        "stop_loss": 1,
        "time_exit_24h": 0,
        "win_rate": 2 / 3,
        "mean_realized_bps": 14.2,
        "mean_mfe_bps": 38.5,
        "mean_mae_bps": -12.7,
        "mean_minutes_to_first_touch": 84.5,
    }
    return {
        "mode": "research_shadow",
        "decision_influence": False,
        "engine_version": "eurusd-daily-abc-v1.3.0",
        "governance": {"active_daily_engine_influence": False},
        "performance": {arm: {key: dict(metric) for key in HORIZONS} for arm in ("A", "B", "C")},
        "trade_path": {
            "schema_version": "eurusd-abc-virtual-trade-path-v1",
            "prospective_from_engine_version": "eurusd-daily-abc-v1.3.0",
            "historical_backfill": False,
            "virtual_only": True,
            "performance": {arm: dict(trade_metric) for arm in ("A", "B", "C")},
        },
    }


class DailyEURUSDABCPublicProjectionTests(unittest.TestCase):
    def test_projection_exposes_sanitized_point_and_virtual_trade_contracts(self):
        payload = build_public_projection(
            private_state(),
            private_report(),
            now=datetime(2026, 8, 20, 20, 16, tzinfo=UTC),
        )
        validate_public_projection(payload)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(payload["language"], "pl")
        self.assertEqual(payload["mode"], "LIVE_SHADOW")
        self.assertEqual(payload["latest"]["signal_generated_at"], "2026-08-20T19:01:12Z")
        self.assertEqual(tuple(payload["latest"]["horizons"]), HORIZONS)
        self.assertEqual(payload["latest"]["horizons"]["30m"]["status"], "RESOLVED")
        self.assertEqual(payload["latest"]["horizons"]["60m"]["status"], "PENDING")

        trade = payload["latest"]["virtual_trade"]
        self.assertTrue(trade["available"])
        self.assertTrue(trade["virtual_only"])
        self.assertFalse(trade["spread_slippage_included"])
        self.assertEqual(trade["risk"]["atr_window"], 26)
        self.assertEqual(trade["risk"]["reward_risk"], 1.8)
        self.assertEqual(trade["arms"]["A"]["status"], "CLOSED")
        self.assertEqual(trade["arms"]["A"]["exit_reason"], "TAKE_PROFIT")
        self.assertEqual(trade["arms"]["A"]["mfe_bps"], 51.2)
        self.assertEqual(trade["arms"]["A"]["mae_bps"], -7.4)
        self.assertEqual(trade["arms"]["B"]["status"], "NO_TRADE")
        self.assertEqual(trade["arms"]["C"]["status"], "OPEN")
        self.assertIsNone(trade["arms"]["C"]["mfe_bps"])
        self.assertIsNone(trade["arms"]["C"]["mae_bps"])

        cumulative = payload["trade_comparison"]["arms"]["A"]
        self.assertEqual(cumulative["take_profit"], 2)
        self.assertEqual(cumulative["stop_loss"], 1)
        self.assertEqual(cumulative["ambiguous_same_1m_bar"], 1)
        self.assertEqual(cumulative["mean_realized_bps"], 14.2)
        self.assertFalse(DISALLOWED_PUBLIC_KEYS.intersection(_walk_keys(payload)))

    def test_pre_v13_capture_is_not_retroactively_backfilled(self):
        state = private_state()
        latest = state["captures"][0]
        latest["engine_version"] = "eurusd-daily-abc-v1.2.0"
        latest.pop("trade_plan")
        latest.pop("trade_plan_sha256")
        latest.pop("trade_path")
        report = private_report()
        report.pop("trade_path")
        payload = build_public_projection(state, report)
        trade = payload["latest"]["virtual_trade"]
        self.assertFalse(trade["available"])
        self.assertEqual(trade["arms"]["A"]["status"], "NOT_TRACKED_PRE_V13")
        self.assertEqual(payload["trade_comparison"]["arms"]["A"]["signals"], 0)

    def test_projection_generation_time_is_stable_when_private_state_did_not_change(self):
        payload = build_public_projection(private_state(), private_report())
        self.assertEqual(payload["generated_at"], "2026-08-20T20:15:00Z")

    def test_pl_frontend_only_contract(self):
        pl = (ROOT / "pl/inwestycje/daily-trading.html").read_text(encoding="utf-8")
        en = (ROOT / "en/investing/daily-trading.html").read_text(encoding="utf-8")
        js = (ROOT / "scripts/daily-eurusd-abc-lab-pl.js").read_text(encoding="utf-8")
        self.assertIn('id="eurusd-abc-lab-pl-root"', pl)
        self.assertIn('/scripts/daily-eurusd-abc-lab-pl.js?v=3', pl)
        self.assertNotIn("eurusd-abc-lab-pl-root", en)
        self.assertNotIn("daily-eurusd-abc-lab-pl.js", en)
        self.assertIn('/data/investments/eurusd_abc_public_pl.json', js)
        self.assertIn("Sygnał wygenerowany", js)
        self.assertIn("SYGNAŁ", js)
        self.assertIn("Wirtualna ścieżka pozycji", js)
        self.assertIn("MFE", js)
        self.assertIn("MAE", js)
        self.assertIn("TP", js)
        self.assertIn("SL", js)
        self.assertIn("Narastające porównanie kierunku", js)
        self.assertIn("Narastające wyniki wirtualnych pozycji", js)
        self.assertIn("30m", js)
        self.assertIn("1440m", js)


if __name__ == "__main__":
    unittest.main()
