from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "investments_wes_memory.py"
spec = importlib.util.spec_from_file_location("investments_wes_memory", MODULE_PATH)
assert spec and spec.loader
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)


def write_week(root: Path, name: str, payload: dict) -> Path:
    path = root / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


class WESHistoricalMemoryTests(unittest.TestCase):
    def test_legacy_complete_trade_is_market_memory_but_not_strategy_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_week(root, "2026-W25", {
                "week_id": "2026-W25", "method_version": "1.2.0",
                "instruments": [{
                    "instrument_id": "eurusd", "direction": "short",
                    "entry_price": 1.16, "entry_captured_at": "2026-06-15T08:00:00+02:00",
                    "exit_price": 1.15, "exit_captured_at": "2026-06-19T22:00:00+02:00",
                    "result_percent": 0.8, "exit_reason": "scheduled_week_close",
                }],
            })
            before = source.read_bytes()
            built = memory.build_memory(root, from_week="2026-W25", through_week="2026-W25")
            self.assertEqual(source.read_bytes(), before)
            row = built["records"][0]
            self.assertEqual(row["quality_grade"], "B")
            self.assertEqual(row["quality_weight"], 0.70)
            self.assertTrue(row["market_memory_eligible"])
            self.assertFalse(row["strategy_memory_eligible"])
            self.assertIsNone(row["strategy_id"])

    def test_canonical_leg_with_explicit_strategy_is_grade_a_strategy_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_week(root, "2026-W32", {
                "week_id": "2026-W32", "method_version": "5.0.0-experimental",
                "instruments": [{
                    "instrument_id": "eurusd", "direction": "short",
                    "entry_price": 99, "exit_price": 99, "result_percent": 999,
                    "position_legs": [{
                        "leg_id": "leg-1", "instrument_id": "eurusd", "direction": "short",
                        "strategy_id": "ema_mean_reversion", "entry_regime": "trend_up:vol_normal",
                        "entry_price": 1.15, "entry_captured_at": "2026-08-03T08:00:00+02:00",
                        "exit_price": 1.16, "exit_captured_at": "2026-08-07T22:00:00+02:00",
                        "gross_result_percent": -0.87, "estimated_round_trip_cost_percent": 0.02,
                        "net_result_percent": -0.89, "exit_reason": "scheduled_week_close",
                    }],
                }],
            })
            built = memory.build_memory(root, from_week="2026-W32", through_week="2026-W32")
            self.assertEqual(len(built["records"]), 1, "top-level position must not be double-counted")
            row = built["records"][0]
            self.assertEqual(row["quality_grade"], "A")
            self.assertEqual(row["quality_weight"], 1.0)
            self.assertEqual(row["strategy_id"], "ema_mean_reversion")
            self.assertTrue(row["market_memory_eligible"])
            self.assertTrue(row["strategy_memory_eligible"])
            self.assertAlmostEqual(row["net_result_percent"], -0.89)

    def test_reconstructed_week_is_downweighted_and_never_infers_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_week(root, "2026-W31", {
                "week_id": "2026-W31", "method_version": "5.0.1-reconstructed",
                "model_status": "paper_only_historical_reconstruction",
                "reconstruction": {"truthfulness_note": "exact first-hit bar not preserved"},
                "instruments": [{
                    "instrument_id": "sp500_futures", "direction": "long",
                    "entry_price": 7511.25, "entry_captured_at": "2026-07-27T08:55:00+02:00",
                    "exit_price": 7411.23, "exit_captured_at": "2026-07-30T09:00:00+02:00",
                    "result_percent": -1.3315, "exit_time_precision": "hour_observed_exact_first_hit_bar_not_preserved",
                }],
            })
            built = memory.build_memory(root, from_week="2026-W31", through_week="2026-W31")
            row = built["records"][0]
            self.assertEqual(row["quality_grade"], "C")
            self.assertEqual(row["quality_weight"], 0.40)
            self.assertTrue(row["reconstructed"])
            self.assertTrue(row["market_memory_eligible"])
            self.assertFalse(row["strategy_memory_eligible"])

    def test_no_trade_or_missing_execution_is_information_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_week(root, "2026-W24", {
                "week_id": "2026-W24", "method_version": "1.2.0",
                "instruments": [{
                    "instrument_id": "eurusd", "direction": "neutral",
                    "entry_price": None, "entry_captured_at": None,
                    "exit_price": 1.15, "exit_captured_at": "2026-06-12T22:00:00+02:00",
                    "result": "no_trade", "result_percent": 0.0,
                }],
            })
            built = memory.build_memory(root, from_week="2026-W24", through_week="2026-W24")
            row = built["records"][0]
            self.assertEqual(row["quality_grade"], "D")
            self.assertEqual(row["quality_weight"], 0.0)
            self.assertFalse(row["market_memory_eligible"])
            self.assertFalse(row["strategy_memory_eligible"])

    def test_same_week_same_instrument_cap_exposes_correlated_reentries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_week(root, "2026-W33", {
                "week_id": "2026-W33", "method_version": "5.3.0-experimental",
                "instruments": [{
                    "instrument_id": "eurusd",
                    "position_legs": [
                        {"leg_id": "a", "instrument_id": "eurusd", "direction": "long", "strategy_id": "base_v2", "entry_price": 1.0, "entry_captured_at": "2026-08-10T08:00:00+02:00", "exit_price": 1.01, "exit_captured_at": "2026-08-11T10:00:00+02:00", "net_result_percent": 1.0},
                        {"leg_id": "b", "instrument_id": "eurusd", "direction": "short", "strategy_id": "weekly_trend", "entry_price": 1.01, "entry_captured_at": "2026-08-11T11:00:00+02:00", "exit_price": 1.0, "exit_captured_at": "2026-08-12T10:00:00+02:00", "net_result_percent": 0.99},
                    ],
                }],
            })
            built = memory.build_memory(root, from_week="2026-W33", through_week="2026-W33")
            report = memory.build_report(built)
            self.assertEqual(report["market_memory"]["records"], 2)
            self.assertEqual(report["market_memory"]["effective_samples_raw"], 2.0)
            self.assertEqual(report["market_memory"]["effective_samples_week_instrument_capped"], 1.0)

    def test_entry_before_frozen_target_is_downweighted_and_not_strategy_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_week(root, "2026-W30", {
                "week_id": "2026-W30", "method_version": "5.0.0-experimental",
                "forecast_created_at": "2026-07-20T08:33:59+02:00",
                "late_forecast_recovery": True,
                "market_window": {"entry_target_local": "2026-07-20T08:00:00+02:00"},
                "instruments": [{
                    "instrument_id": "eurusd",
                    "continuous_entry_decision": {"strategy_id": "weekly_trend", "regime": "trend_down:vol_normal"},
                    "direction": "short",
                    "entry_price": 1.144, "entry_captured_at": "2026-07-20T08:25:00+02:00",
                    "exit_price": 1.137, "exit_captured_at": "2026-07-24T22:00:00+02:00",
                    "result_percent": 0.58,
                }],
            })
            built = memory.build_memory(root, from_week="2026-W30", through_week="2026-W30")
            row = built["records"][0]
            self.assertEqual(row["quality_grade"], "C")
            self.assertTrue(row["market_memory_eligible"])
            self.assertFalse(row["strategy_memory_eligible"])
            self.assertEqual(row["strategy_id"], "weekly_trend")

    def test_outputs_are_deterministic_and_decision_influence_is_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_week(root, "2026-W34", {
                "week_id": "2026-W34", "method_version": "5.6.0-experimental",
                "instruments": [{
                    "instrument_id": "eurusd",
                    "position_legs": [{
                        "leg_id": "wes-1", "instrument_id": "eurusd", "direction": "short",
                        "strategy_id": "weekly_trend", "entry_regime": "trend_down:vol_normal",
                        "entry_price": 1.16, "entry_captured_at": "2026-08-17T08:00:00+02:00",
                        "exit_price": 1.15, "exit_captured_at": "2026-08-18T10:00:00+02:00",
                        "net_result_percent": 0.84,
                        "risk_plan": {"wes_entry_class": "monday_weekly"},
                    }],
                }],
            })
            first = memory.build_memory(root, from_week="2026-W34", through_week="2026-W34")
            second = memory.build_memory(root, from_week="2026-W34", through_week="2026-W34")
            self.assertEqual(first, second)
            self.assertFalse(first["active_decision_influence"])
            self.assertTrue(first["records"][0]["wes_native"])
            self.assertEqual(first["records"][0]["entry_class"], "monday_weekly")


if __name__ == "__main__":
    unittest.main()
