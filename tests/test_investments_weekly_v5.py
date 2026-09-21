import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import investments_weekly_v5 as v5
import investments_weekly_v5_finalize as finalize


class GovernedWeeklyModelTests(unittest.TestCase):
    def method(self, enabled=True):
        return {"instruments": [{"id": "x", "enabled_for_new_positions": enabled,
                                  "validation_gate_reason": "failed_validation"}]}

    def test_finalizer_preserves_active_runtime_version(self):
        self.assertEqual(finalize.VERSION, v5.VERSION)

    def test_common_gate_blocks_new_entry_for_every_layer(self):
        item = {"instrument_id": "x", "direction": "long", "trade_status": "planned"}
        allowed, changed = v5.gate(item, self.method(False), "x")
        self.assertFalse(allowed)
        self.assertTrue(changed)
        self.assertEqual(item["direction"], "neutral")
        self.assertEqual(item["trade_status"], "no_trade")

    def test_common_gate_preserves_existing_open_position(self):
        item = {"instrument_id": "x", "direction": "long", "entry_price": 100.0,
                "exit_price": None, "trade_status": "open"}
        allowed, _ = v5.gate(item, self.method(False), "x")
        self.assertFalse(allowed)
        self.assertEqual(item["direction"], "long")
        self.assertEqual(item["entry_price"], 100.0)
        self.assertEqual(item["validation_gate"], "grandfathered_existing_position_no_new_entries")

    def test_freeze_decision_creates_frozen_price_target_not_market_entry(self):
        now = datetime(2026, 9, 21, 8, 5, tzinfo=v5.legacy.TZ)
        item = {
            "instrument_id": "btcusd",
            "direction": "long",
            "trade_status": "planned",
            "validation_gate": "enabled_for_paper_trading",
            "wes_entry_authorization": {
                "authorized_at": now.isoformat(timespec="seconds"),
                "directional_admission_passed": True,
                "candidate": {
                    "strategy_id": "base_v2",
                    "direction": "long",
                    "execution_authority": "champion_execution",
                },
            },
        }
        decision = {"strategy_id": "base_v2", "direction": "long", "raw_score": 50.0}
        fresh = {"score": 50.0, "signals": {
            "last_close": 100.0, "atr14": 4.0, "ema20": 96.0,
            "ret5_pct": 5.0, "ret20_pct": 8.0, "range55_position": 0.9,
        }}
        policy = {"entry_price_engine": {
            "enabled": True, "require_for_all_new_entries": True, "version": "WES-1.2.0",
            "max_wait_minutes": 60, "minimum_pullback_atr": 0.10, "base_pullback_atr": 0.12,
            "overextension_extra_pullback_atr": 0.48, "maximum_pullback_atr": 0.75,
            "ret5_full_scale_percent": 8.0, "ema_distance_full_scale_atr": 2.5,
            "structural_ema20_buffer_atr": 0.25,
            "max_target_distance_percent": {"btcusd": 3.5},
        }}
        pending = v5.freeze_decision(item, decision, fresh, {"score": 40.0}, now, policy)
        self.assertEqual("pending", item["trade_status"])
        self.assertEqual("waiting_for_entry_target", item["next_entry_status"])
        self.assertEqual(now.isoformat(timespec="seconds"), pending["decided_at"])
        self.assertEqual("long", pending["decision"]["direction"])
        self.assertEqual("buy_limit", pending["entry_price_plan"]["order_type"])
        self.assertLess(pending["entry_price_plan"]["target_price"], 100.0)

    def test_entry_executes_only_when_frozen_limit_target_is_touched(self):
        decided = datetime(2026, 7, 20, 8, 35, tzinfo=v5.legacy.TZ)
        pending = {
            "entry_not_before": decided.isoformat(),
            "decision": {"direction": "long"},
            "entry_price_plan": {
                "instrument_id": "sp500_futures",
                "direction": "long",
                "target_price": 100.0,
                "entry_not_before": decided.isoformat(),
                "expires_at": (decided + timedelta(hours=1)).isoformat(),
            },
        }
        index = pd.DatetimeIndex([decided + timedelta(minutes=5)])
        missed = pd.DataFrame({"High": [102.0], "Low": [100.5]}, index=index)
        touched = pd.DataFrame({"High": [102.0], "Low": [99.8]}, index=index)
        with patch.object(v5.v2, "intraday_bars", return_value=missed):
            self.assertIsNone(v5.entry_point("ES=F", pending, decided + timedelta(minutes=10)))
        with patch.object(v5.v2, "intraday_bars", return_value=touched):
            point = v5.entry_point("ES=F", pending, decided + timedelta(minutes=10))
        self.assertIsNotNone(point)
        self.assertEqual(100.0, point["price"])
        self.assertIn("frozen_entry_target_touch", point["source"])

    def test_strong_btc_rally_requires_material_pullback_before_long_entry(self):
        now = datetime(2026, 9, 21, 12, 52, 50, tzinfo=v5.legacy.TZ)
        item = {
            "instrument_id": "btcusd",
            "direction": "short",
            "trade_status": "closed",
            "entry_price": 81593.9765625,
            "exit_price": 84024.33708186,
            "exit_captured_at": "2026-09-21T10:35:00+02:00",
            "exit_reason": "stop_loss",
        }
        decision = {"instrument_id": "btcusd", "strategy_id": "base_v2", "direction": "long"}
        fresh = {"signals": {
            "last_close": 84151.8984375,
            "atr14": 2256.57756696,
            "ema20": 78542.52929038,
            "ret5_pct": 10.5076,
            "ret20_pct": 8.7183,
            "range55_position": 1.0,
        }}
        policy = {"entry_price_engine": {
            "enabled": True, "version": "WES-1.2.0", "max_wait_minutes": 60,
            "minimum_pullback_atr": 0.10, "base_pullback_atr": 0.12,
            "overextension_extra_pullback_atr": 0.48, "maximum_pullback_atr": 0.75,
            "post_stop_reversal_extra_pullback_atr": 0.15,
            "post_stop_reversal_min_completed_bars": 3,
            "ret5_full_scale_percent": 8.0, "ema_distance_full_scale_atr": 2.5,
            "overextension_weights": {"momentum_5d": 0.40, "range_55d": 0.35, "ema20_distance": 0.25},
            "structural_ema20_buffer_atr": 0.25,
            "max_target_distance_percent": {"btcusd": 3.5},
        }}
        plan = v5.build_entry_price_plan(item, decision, fresh, now, policy)
        self.assertIsNotNone(plan)
        self.assertTrue(plan["inputs"]["post_stop_reversal"])
        self.assertGreater(plan["inputs"]["overextension_score"], 0.95)
        self.assertGreaterEqual(plan["inputs"]["pullback_atr_fraction"], 0.70)
        self.assertLess(plan["target_price"], 83000.0)
        self.assertGreater(plan["target_price"], 81000.0)
        self.assertEqual("buy_limit", plan["order_type"])

    def test_same_authorized_thesis_does_not_chase_market_by_moving_target(self):
        now = datetime(2026, 9, 21, 12, 52, 50, tzinfo=v5.legacy.TZ)
        item = {
            "instrument_id": "btcusd",
            "wes_entry_authorization": {
                "authorized_at": now.isoformat(timespec="seconds"),
                "directional_admission_passed": True,
                "candidate": {"strategy_id": "base_v2", "direction": "long", "execution_authority": "champion_execution"},
            },
        }
        decision = {"strategy_id": "base_v2", "direction": "long"}
        fresh = {"signals": {"last_close": 84000, "atr14": 2000, "ema20": 79000, "ret5_pct": 9, "ret20_pct": 8, "range55_position": 1}}
        policy = {"entry_price_engine": {
            "enabled": True, "require_for_all_new_entries": True, "version": "WES-1.2.0",
            "max_wait_minutes": 60, "minimum_pullback_atr": 0.10, "base_pullback_atr": 0.12,
            "overextension_extra_pullback_atr": 0.48, "maximum_pullback_atr": 0.75,
            "ret5_full_scale_percent": 8.0, "ema_distance_full_scale_atr": 2.5,
            "structural_ema20_buffer_atr": 0.25, "max_target_distance_percent": {"btcusd": 3.5},
        }}
        pending = v5.freeze_decision(item, decision, fresh, {}, now, policy)
        target = pending["entry_price_plan"]["target_price"]
        item["wes_entry_authorization"] = {
            "authorized_at": (now + timedelta(minutes=5)).isoformat(timespec="seconds"),
            "directional_admission_passed": True,
            "candidate": {"strategy_id": "base_v2", "direction": "long", "execution_authority": "champion_execution"},
        }
        self.assertTrue(v5.pending_matches_wes_authorization(item, pending, now + timedelta(minutes=6)))
        self.assertEqual(target, pending["entry_price_plan"]["target_price"])

    def test_thesis_exit_blocks_same_week_reentry(self):
        now = datetime(2026, 7, 21, 10, 0, tzinfo=v5.legacy.TZ)
        item = {"direction": "long", "entry_price": 100, "exit_price": 95,
                "exit_reason": "daily_model_directional_invalidation"}
        week = {"market_window": {"exit_target_local": "2026-07-24T22:00:00+02:00"}}
        blocked, changed = v5.lock_reentry(item, week, now)
        self.assertTrue(blocked)
        self.assertTrue(changed)
        self.assertTrue(item["reentry_lock"]["active"])

    def test_strategy_switch_does_not_create_thesis_lock(self):
        now = datetime(2026, 7, 21, 10, 0, tzinfo=v5.legacy.TZ)
        item = {"direction": "long", "entry_price": 100, "exit_price": 99,
                "exit_reason": "v4_daily_strategy_direction_switch"}
        blocked, _ = v5.lock_reentry(item, {"market_window": {}}, now)
        self.assertFalse(blocked)

    def test_no_trade_is_first_class_decision(self):
        policy = {"no_trade": {"enabled": True, "minimum_directional_raw_score": 35,
                                "minimum_directional_utility": 6, "conflict_no_trade_below_raw_score": 45}}
        decision = {"strategy_id": "base_v2", "direction": "long", "raw_score": 18,
                    "utility": 4, "candidates": {}}
        result = v5.no_trade(decision, {"score": 18, "data_quality": "passed"},
                             {"score": 12, "data_quality": "passed"}, policy)
        self.assertEqual(result["strategy_id"], "no_trade")
        self.assertEqual(result["direction"], "neutral")

    def test_strong_aligned_signal_remains_directional(self):
        policy = {"no_trade": {"enabled": True, "minimum_directional_raw_score": 35,
                                "minimum_directional_utility": 6, "conflict_no_trade_below_raw_score": 45}}
        decision = {"strategy_id": "weekly_trend", "direction": "long", "raw_score": 65,
                    "utility": 11, "candidates": {}}
        result = v5.no_trade(decision, {"score": 55, "data_quality": "passed"},
                             {"score": 65, "data_quality": "passed"}, policy)
        self.assertEqual(result["strategy_id"], "weekly_trend")
        self.assertEqual(result["direction"], "long")

    def test_close_normalizes_v5_exposure_metadata(self):
        item = {
            "continuous_exposure_active": True,
            "continuous_exposure_status": "open",
            "next_entry_status": "open",
            "pending_entry_decision": {"decision": {"direction": "long"}},
            "risk_status": "open_multi_instrument_continuous_exposure",
            "exit_reason": "scheduled_week_close",
        }
        self.assertTrue(v5.v2.mark_exposure_closed(item))
        self.assertFalse(item["continuous_exposure_active"])
        self.assertEqual(item["continuous_exposure_status"], "closed")
        self.assertEqual(item["next_entry_status"], "closed")
        self.assertIsNone(item["pending_entry_decision"])
        self.assertEqual(item["risk_status"], "closed_scheduled_week_close")

    def test_close_does_not_add_v5_metadata_to_legacy_row(self):
        item = {"trade_status": "closed", "exit_price": 100.0}
        self.assertFalse(v5.v2.mark_exposure_closed(item))
        self.assertNotIn("continuous_exposure_active", item)
        self.assertNotIn("continuous_exposure_status", item)

    def test_due_week_is_closed_at_first_bar_and_exposure_is_cleared(self):
        now = datetime(2026, 8, 7, 22, 15, tzinfo=v5.legacy.TZ)
        payload = {
            "week_id": "2026-W32",
            "market_window": {"exit_target_local": "2026-08-07T22:00:00+02:00"},
            "instruments": [{
                "instrument_id": "eurusd",
                "symbol": "EURUSD=X",
                "direction": "short",
                "entry_price": 1.15,
                "exit_price": None,
                "trade_status": "open",
                "continuous_exposure_active": True,
                "continuous_exposure_status": "open",
                "next_entry_status": "open",
                "pending_entry_decision": {"decision": {"direction": "short"}},
                "risk_status": "open_multi_instrument_continuous_exposure",
                "notional_eur": 10000,
            }],
        }
        point = {
            "price": 1.16,
            "timestamp": "2026-08-07T22:00:00+02:00",
            "source": "test:first_bar_at_or_after_target",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "2026-W32.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(v5.v2, "WEEKLY_DIR", Path(temp_dir)), \
                    patch.object(v5.v2.legacy, "now_local", return_value=now), \
                    patch.object(v5.v2, "first_bar_at_or_after", return_value=point):
                self.assertTrue(v5.v2.close_due_weeks())
            saved = json.loads(path.read_text(encoding="utf-8"))

        item = saved["instruments"][0]
        self.assertEqual(1.16, item["exit_price"])
        self.assertEqual("scheduled_week_close", item["exit_reason"])
        self.assertEqual("closed", item["trade_status"])
        self.assertFalse(item["continuous_exposure_active"])
        self.assertEqual("closed", item["continuous_exposure_status"])
        self.assertIsNone(item["pending_entry_decision"])
        self.assertEqual("closed_scheduled_week_close", item["risk_status"])

    def test_scheduled_close_is_archived_as_next_week_learning_sample(self):
        week = {
            "week_id": "2026-W32",
            "instruments": [{
                "instrument_id": "eurusd",
                "symbol": "EURUSD=X",
                "direction": "long",
                "entry_price": 1.10,
                "entry_captured_at": "2026-08-03T08:05:00+02:00",
                "entry_source": "test",
                "exit_price": 1.12,
                "exit_captured_at": "2026-08-07T22:00:00+02:00",
                "exit_source": "test",
                "exit_reason": "scheduled_week_close",
                "result_percent": 1.8181818,
                "continuous_entry_decision": {
                    "strategy_id": "daily_weekly_blend",
                    "regime": "trend",
                },
            }],
        }
        policy = {
            "instruments": [{
                "instrument_id": "eurusd",
                "enabled": True,
                "round_trip_cost": 0.8,
                "cost_unit": "pips",
            }]
        }
        archived = v5.archive_closed_learning_samples(week, policy)
        self.assertEqual(1, archived)
        legs = week["instruments"][0]["position_legs"]
        self.assertEqual(1, len(legs))
        self.assertEqual("daily_weekly_blend", legs[0]["strategy_id"])
        self.assertEqual("trend", legs[0]["entry_regime"])
        self.assertIsNotNone(legs[0]["net_result_percent"])
        self.assertEqual(0, v5.archive_closed_learning_samples(week, policy))


if __name__ == "__main__":
    unittest.main()
