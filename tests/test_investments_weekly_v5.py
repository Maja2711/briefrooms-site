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
import audit_intraday_risk_exits as risk_market
import investments_weekly_v5_finalize as finalize


class GovernedWeeklyModelTests(unittest.TestCase):
    def setUp(self):
        self._ledger_tmp = tempfile.TemporaryDirectory()
        self._ledger_patch = patch.object(
            v5.decision_ledger,
            "LEDGER_PATH",
            Path(self._ledger_tmp.name) / "wes_decision_ledger.json",
        )
        self._ledger_patch.start()

    def tearDown(self):
        self._ledger_patch.stop()
        self._ledger_tmp.cleanup()

    def method(self, enabled=True):
        return {"instruments": [{"id": "x", "enabled_for_new_positions": enabled,
                                  "validation_gate_reason": "failed_validation"}]}

    def test_canonical_live_short_limit_is_immediately_marketable(self):
        now = datetime(2026, 10, 6, 14, 6, 50, tzinfo=v5.legacy.TZ)
        pending = {"decision": {"direction": "short"}, "entry_price_plan": {
            "instrument_id": "eurusd", "direction": "short", "execution_mode": "limit_pullback",
            "target_price": 1.12656,
            "entry_not_before": (now - timedelta(minutes=5)).isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }}
        live = {"prices": {"eurusd": {
            "price": 1.12701,
            "timestamp": (now - timedelta(seconds=3)).isoformat(),
            "source": "canonical-test",
        }}}
        with patch.object(v5, "read", return_value=live):
            point = v5._canonical_live_entry_point(pending, now)
        self.assertIsNotNone(point)
        self.assertEqual(1.12656, point["price"])
        self.assertEqual(1.12701, point["canonical_live_price"])

    def test_market_now_short_hits_fresh_live_price_even_below_archived_sell_limit(self):
        now = datetime(2026, 10, 7, 14, 5, 1, tzinfo=v5.legacy.TZ)
        start = now - timedelta(minutes=2)
        pending = {"decision": {"direction": "short"}, "entry_price_plan": {
            "instrument_id": "eurusd", "direction": "short", "execution_mode": "market_now",
            "target_price": 1.12190247,
            "entry_not_before": start.isoformat(),
            "expires_at": (now + timedelta(minutes=12)).isoformat(),
        }}
        live = {"prices": {"eurusd": {
            "price": 1.11894369,
            "timestamp": (now - timedelta(seconds=3)).isoformat(),
            "source": "canonical-test",
        }}}
        with patch.object(v5, "read", return_value=live):
            point = v5._canonical_live_entry_point(pending, now)
        self.assertIsNotNone(point)
        self.assertEqual(1.11894369, point["price"])
        self.assertEqual(1.11894369, point["canonical_live_price"])
        verified, audit = v5.epe_verified_entry_point(pending, point, now)
        self.assertIsNotNone(verified)
        self.assertEqual(1.11894369, verified["price"])
        self.assertEqual("MARKET_NOW", audit["mode"])
        self.assertTrue(audit["verified"])

    def test_expired_market_now_does_not_trap_recovery_on_new_live_price(self):
        now = datetime(2026, 10, 7, 16, 29, 0, tzinfo=v5.legacy.TZ)
        pending = {"decision": {"strategy_id": "base_v2", "direction": "short"}, "entry_price_plan": {
            "instrument_id": "eurusd", "direction": "short", "execution_mode": "market_now",
            "target_price": 1.12190247,
            "entry_not_before": "2026-10-07T14:02:28+02:00",
            "expires_at": "2026-10-07T14:17:28+02:00",
        }, "authorization_basis": {
            "strategy_id": "base_v2", "direction": "short", "directional_admission_passed": True,
        }}
        live = {"prices": {"eurusd": {
            "price": 1.11750,
            "timestamp": (now - timedelta(seconds=3)).isoformat(),
            "source": "canonical-test",
        }}}
        with patch.object(v5, "read", return_value=live), \
             patch.object(v5, "_yahoo_market_entry", return_value=None):
            point = v5.entry_point("EURUSD=X", pending, now)
        self.assertIsNone(point)

    def test_market_now_epe_accepts_fresh_completed_bar_without_limit_touch(self):
        now = datetime(2026, 10, 7, 14, 10, 0, tzinfo=v5.legacy.TZ)
        start = now - timedelta(minutes=8)
        pending = {"decision": {"direction": "short"}, "entry_price_plan": {
            "instrument_id": "eurusd", "direction": "short", "execution_mode": "market_now",
            "target_price": 1.12190247,
            "entry_not_before": start.isoformat(),
            "expires_at": (now + timedelta(minutes=7)).isoformat(),
        }}
        point = {
            "price": 1.11880,
            "timestamp": (now - timedelta(minutes=5)).isoformat(),
            "source": "Yahoo Finance:EURUSD=X:5m:market_now_completed_bar",
            "observed_high": 1.11910,
            "observed_low": 1.11860,
        }
        verified, audit = v5.epe_verified_entry_point(pending, point, now)
        self.assertIsNotNone(verified)
        self.assertEqual(1.11880, verified["price"])
        self.assertEqual("MARKET_NOW", audit["mode"])
        self.assertTrue(audit["verified"])

    def test_canonical_live_price_triggers_short_stop_loss(self):
        now = datetime(2026, 10, 6, 14, 6, 50, tzinfo=v5.legacy.TZ)
        live = {"prices": {"eurusd": {
            "price": 1.13000,
            "timestamp": (now - timedelta(seconds=3)).isoformat(),
            "source": "canonical-test",
        }}}
        with patch.object(risk_market, "read_json", return_value=live):
            hit = risk_market.canonical_live_risk_hit(
                "eurusd", "short", 1.12900, 1.12000,
                (now - timedelta(minutes=20)).astimezone(risk_market.UTC),
                now.astimezone(risk_market.UTC),
            )
        self.assertIsNotNone(hit)
        self.assertEqual("stop_loss", hit[0])
        self.assertEqual(1.12900, hit[1])

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

    def test_expired_unfilled_reentry_restores_closed_historical_leg(self):
        item = {
            "instrument_id": "btcusd",
            "direction": "short",
            "trade_status": "pending",
            "entry_price": 81593.9765625,
            "entry_captured_at": "2026-09-21T09:25:00+02:00",
            "exit_price": 84024.33708186,
            "exit_captured_at": "2026-09-21T10:35:00+02:00",
            "exit_reason": "stop_loss",
            "result": "loss",
            "result_value": -297.86028599,
            "result_percent": -2.9786,
            "risk_plan": {
                "model_version": "WES-1.0.0",
                "direction": "short",
                "wes_entry_class": "monday_weekly",
                "stop_loss_price": 84024.33708186,
                "take_profit_price": 76707.42716313,
            },
            "pending_entry_decision": {
                "decision": {"strategy_id": "base_v2", "direction": "long"},
            },
            "wes_entry_authorization": {"authorization_type": "early_close_reentry"},
            "next_entry_status": "waiting_for_entry_target",
            "entry_quality_status": "wes_1_2_waiting_for_frozen_entry_target",
        }
        blocked = {
            "strategy_id": "no_trade",
            "direction": "neutral",
            "reason_codes": ["wes_entry_authorization_expired"],
        }
        v5.settle_unfilled_reentry(item, blocked, "wes_entry_authorization_expired")
        self.assertEqual("closed", item["trade_status"])
        self.assertEqual("short", item["direction"])
        self.assertEqual(81593.9765625, item["entry_price"])
        self.assertEqual(84024.33708186, item["exit_price"])
        self.assertEqual(-297.86028599, item["result_value"])
        self.assertEqual(-2.9786, item["result_percent"])
        self.assertIsNone(item["pending_entry_decision"])
        self.assertIsNone(item["wes_entry_authorization"])
        self.assertEqual("no_trade", item["next_entry_status"])
        self.assertEqual("closed_waiting_new_trigger", item["wes_status"])
        self.assertEqual("wes_monday_weekly", item["entry_quality_status"])

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
        self.assertTrue(str(pending["decision_id"]).startswith("wes-dec-"))
        self.assertEqual(64, len(pending["payload_hash"]))
        self.assertTrue(v5.decision_ledger.assert_pending_integrity(pending))

    def test_strong_aligned_trend_chooses_market_entry(self):
        now = datetime(2026, 10, 5, 8, 5, tzinfo=v5.legacy.TZ)
        item = {
            "instrument_id": "eurusd",
            "validation_gate": "enabled_for_paper_trading",
            "wes_entry_authorization": {
                "authorized_at": now.isoformat(timespec="seconds"),
                "directional_admission_passed": True,
                "candidate": {
                    "strategy_id": "daily_weekly_blend",
                    "direction": "short",
                    "execution_authority": "champion_execution",
                },
            },
        }
        decision = {
            "strategy_id": "daily_weekly_blend",
            "direction": "short",
            "raw_score": -70.0,
            "utility": 12.0,
            "directional_admission": {"passed": True, "confirmations": 3},
        }
        fresh = {
            "score": -72.0,
            "data_quality": "passed",
            "signals": {
                "last_close": 1.1250,
                "atr14": 0.0060,
                "ema20": 1.1320,
                "ret5_pct": -1.2,
                "ret20_pct": -3.0,
                "range55_position": 0.20,
            },
        }
        weekly = {"score": -45.0, "data_quality": "passed"}
        policy = {"entry_price_engine": {
            "enabled": True,
            "require_for_all_new_entries": True,
            "version": "WES-1.3.0",
            "max_wait_minutes": 60,
            "minimum_pullback_atr": 0.10,
            "base_pullback_atr": 0.12,
            "overextension_extra_pullback_atr": 0.48,
            "maximum_pullback_atr": 0.75,
            "ret5_full_scale_percent": 8.0,
            "ema_distance_full_scale_atr": 2.5,
            "structural_ema20_buffer_atr": 0.25,
            "max_target_distance_percent": {"eurusd": 0.75},
            "market_entry": {
                "enabled": True,
                "max_wait_minutes": 15,
                "min_daily_abs_score": 55,
                "min_weekly_abs_score": 20,
                "min_selected_utility": 8,
                "min_confirmations": 2,
                "minimum_absolute_momentum_pct": 0.15,
                "require_daily_weekly_alignment": True,
                "require_momentum_alignment": True,
                "maximum_overextension_score": 0.90,
                "block_immediate_market_after_stop": True,
                "allow_limit_to_market_promotion": True,
            },
        }}
        pending = v5.freeze_decision(item, decision, fresh, weekly, now, policy)
        plan = pending["entry_price_plan"]
        self.assertEqual("market_now", plan["execution_mode"])
        self.assertEqual("market", plan["order_type"])
        self.assertEqual("waiting_for_market_entry", item["next_entry_status"])
        self.assertTrue(plan["market_entry_diagnostics"]["eligible"])
        self.assertEqual("strong_aligned_trend_continuation", plan["market_entry_reason"])

    def test_wes_1_3_1_spx_like_signal_uses_composite_market_score(self):
        decision = {
            "direction": "long",
            "utility": 14.1271,
            "directional_admission": {"passed": True, "confirmations": 2},
        }
        fresh = {
            "score": 29.0,
            "data_quality": "passed",
            "signals": {"ret5_pct": 0.2969, "ret20_pct": 0.6184},
        }
        weekly = {"score": 61.0, "data_quality": "passed"}
        plan = {"inputs": {"overextension_score": 0.37031, "post_stop_reversal": False}}
        policy = {
            "directional_admission": {"daily_min_abs_score": 25, "weekly_min_abs_score": 15},
            "entry_price_engine": {"market_entry": {
                "enabled": True,
                "version": "WES-1.3.1",
                "min_selected_utility": 8,
                "min_confirmations": 2,
                "minimum_absolute_momentum_pct": 0.15,
                "maximum_overextension_score": 0.90,
                "require_weekly_primary_alignment": True,
                "block_strong_daily_opposition": True,
                "block_opposed_momentum": True,
                "block_immediate_market_after_stop": True,
                "scoring": {
                    "model": "weekly_primary_daily_confirmation_modifier",
                    "minimum_score": 0.70,
                    "primary_weights": {"weekly": 0.45, "utility": 0.25, "confirmations": 0.15, "momentum": 0.15},
                    "full_strength_reference": {"weekly_abs_score": 55, "utility": 14, "confirmations": 4},
                    "overextension_penalty_weight": 0.20,
                    "daily_confirmation_modifier": {
                        "neutral_abs_score_below": 25,
                        "full_strength_abs_score": 55,
                        "aligned_bonus_max": 0.08,
                        "opposed_penalty_max": 0.10,
                        "strong_opposition_veto_abs_score": 55,
                    },
                },
            }},
        }
        eligible, diagnostics = v5._entry_market_mode(decision, fresh, weekly, policy, plan)
        self.assertTrue(eligible)
        self.assertGreaterEqual(diagnostics["market_score"], 0.70)
        self.assertEqual("weekly_primary_daily_confirmation_modifier", diagnostics["scoring_model"])
        self.assertTrue(diagnostics["daily_aligned"])
        self.assertTrue(diagnostics["weekly_aligned"])
        self.assertTrue(diagnostics["momentum_aligned"])
        self.assertGreater(diagnostics["daily_confirmation_modifier"], 0.0)
        self.assertNotIn("daily", diagnostics["primary_score_components"])

    def test_daily_is_bounded_modifier_not_primary_weight(self):
        decision = {
            "direction": "long",
            "utility": 14.0,
            "directional_admission": {"passed": True, "confirmations": 3},
        }
        weekly = {"score": 60.0, "data_quality": "passed"}
        plan = {"inputs": {"overextension_score": 0.25, "post_stop_reversal": False}}
        policy = {
            "directional_admission": {"daily_min_abs_score": 25, "weekly_min_abs_score": 15},
            "entry_price_engine": {"market_entry": {
                "enabled": True,
                "min_selected_utility": 8,
                "min_confirmations": 2,
                "minimum_absolute_momentum_pct": 0.15,
                "maximum_overextension_score": 0.90,
                "require_weekly_primary_alignment": True,
                "block_strong_daily_opposition": True,
                "block_opposed_momentum": True,
                "scoring": {
                    "model": "weekly_primary_daily_confirmation_modifier",
                    "minimum_score": 0.70,
                    "primary_weights": {"weekly": 0.45, "utility": 0.25, "confirmations": 0.15, "momentum": 0.15},
                    "full_strength_reference": {"weekly_abs_score": 55, "utility": 14, "confirmations": 4},
                    "overextension_penalty_weight": 0.20,
                    "daily_confirmation_modifier": {
                        "neutral_abs_score_below": 25,
                        "full_strength_abs_score": 55,
                        "aligned_bonus_max": 0.08,
                        "opposed_penalty_max": 0.10,
                        "strong_opposition_veto_abs_score": 55,
                    },
                },
            }},
        }
        neutral_daily = {
            "score": 0.0,
            "data_quality": "passed",
            "signals": {"ret5_pct": 0.5, "ret20_pct": 1.0},
        }
        aligned_daily = {
            "score": 55.0,
            "data_quality": "passed",
            "signals": {"ret5_pct": 0.5, "ret20_pct": 1.0},
        }
        _, neutral_diag = v5._entry_market_mode(decision, neutral_daily, weekly, policy, plan)
        _, aligned_diag = v5._entry_market_mode(decision, aligned_daily, weekly, policy, plan)
        self.assertEqual(neutral_diag["primary_market_score"], aligned_diag["primary_market_score"])
        self.assertEqual(0.0, neutral_diag["daily_confirmation_modifier"])
        self.assertEqual(0.08, aligned_diag["daily_confirmation_modifier"])
        self.assertAlmostEqual(
            aligned_diag["market_score"] - neutral_diag["market_score"],
            0.08,
            places=4,
        )

    def test_moderate_opposed_daily_penalizes_but_does_not_own_weekly_thesis(self):
        decision = {
            "direction": "long",
            "utility": 14.0,
            "directional_admission": {"passed": True, "confirmations": 3},
        }
        fresh = {
            "score": -35.0,
            "data_quality": "passed",
            "signals": {"ret5_pct": 0.5, "ret20_pct": 1.0},
        }
        weekly = {"score": 65.0, "data_quality": "passed"}
        plan = {"inputs": {"overextension_score": 0.15, "post_stop_reversal": False}}
        policy = {
            "directional_admission": {"daily_min_abs_score": 25, "weekly_min_abs_score": 15},
            "entry_price_engine": {"market_entry": {
                "enabled": True,
                "min_selected_utility": 8,
                "min_confirmations": 2,
                "minimum_absolute_momentum_pct": 0.15,
                "maximum_overextension_score": 0.90,
                "require_weekly_primary_alignment": True,
                "block_strong_daily_opposition": True,
                "block_opposed_momentum": True,
                "scoring": {
                    "minimum_score": 0.70,
                    "primary_weights": {"weekly": 0.45, "utility": 0.25, "confirmations": 0.15, "momentum": 0.15},
                    "full_strength_reference": {"weekly_abs_score": 55, "utility": 14, "confirmations": 4},
                    "overextension_penalty_weight": 0.20,
                    "daily_confirmation_modifier": {
                        "neutral_abs_score_below": 25,
                        "full_strength_abs_score": 55,
                        "aligned_bonus_max": 0.08,
                        "opposed_penalty_max": 0.10,
                        "strong_opposition_veto_abs_score": 55,
                    },
                },
            }},
        }
        eligible, diagnostics = v5._entry_market_mode(decision, fresh, weekly, policy, plan)
        self.assertTrue(eligible)
        self.assertTrue(diagnostics["weekly_aligned"])
        self.assertTrue(diagnostics["daily_opposed"])
        self.assertLess(diagnostics["daily_confirmation_modifier"], 0.0)
        self.assertNotIn("strong_daily_opposition_veto", diagnostics["reasons"])

    def test_strong_opposed_daily_can_veto_market_without_changing_weekly_direction(self):
        decision = {
            "direction": "long",
            "utility": 14.0,
            "directional_admission": {"passed": True, "confirmations": 3},
        }
        fresh = {
            "score": -60.0,
            "data_quality": "passed",
            "signals": {"ret5_pct": 0.5, "ret20_pct": 1.0},
        }
        weekly = {"score": 65.0, "data_quality": "passed"}
        plan = {"inputs": {"overextension_score": 0.10, "post_stop_reversal": False}}
        policy = {
            "directional_admission": {"daily_min_abs_score": 25, "weekly_min_abs_score": 15},
            "entry_price_engine": {"market_entry": {
                "enabled": True,
                "min_selected_utility": 8,
                "min_confirmations": 2,
                "minimum_absolute_momentum_pct": 0.15,
                "maximum_overextension_score": 0.90,
                "require_weekly_primary_alignment": True,
                "block_strong_daily_opposition": True,
                "block_opposed_momentum": True,
                "scoring": {
                    "minimum_score": 0.70,
                    "primary_weights": {"weekly": 0.45, "utility": 0.25, "confirmations": 0.15, "momentum": 0.15},
                    "full_strength_reference": {"weekly_abs_score": 55, "utility": 14, "confirmations": 4},
                    "overextension_penalty_weight": 0.20,
                    "daily_confirmation_modifier": {
                        "neutral_abs_score_below": 25,
                        "full_strength_abs_score": 55,
                        "aligned_bonus_max": 0.08,
                        "opposed_penalty_max": 0.10,
                        "strong_opposition_veto_abs_score": 55,
                    },
                },
            }},
        }
        eligible, diagnostics = v5._entry_market_mode(decision, fresh, weekly, policy, plan)
        self.assertFalse(eligible)
        self.assertTrue(diagnostics["weekly_aligned"])
        self.assertIn("strong_daily_opposition_veto", diagnostics["reasons"])

    def test_wes_1_3_1_persistent_limit_reaffirmation_never_moves_target(self):
        now = datetime(2026, 10, 5, 9, 5, tzinfo=v5.legacy.TZ)
        pending = {
            "entry_price_plan": {
                "execution_mode": "limit_pullback",
                "target_price": 7747.8049694,
                "expires_at": (now - timedelta(minutes=5)).isoformat(),
            }
        }
        policy = {"entry_price_engine": {"persistent_plan": {
            "enabled": True,
            "reaffirmation_extension_minutes": 60,
            "max_reaffirmation_gap_minutes": 20,
        }}}
        original_target = pending["entry_price_plan"]["target_price"]
        self.assertTrue(v5.renew_persistent_entry_plan(pending, now, policy))
        self.assertEqual(original_target, pending["entry_price_plan"]["target_price"])
        self.assertEqual(1, pending["entry_price_plan"]["reaffirmation_count"])
        self.assertGreater(v5.parse_dt(pending["entry_price_plan"]["expires_at"]), now)

    def test_wes_1_3_1_stale_limit_plan_fails_closed_instead_of_reaffirming(self):
        now = datetime(2026, 10, 5, 10, 0, tzinfo=v5.legacy.TZ)
        pending = {
            "entry_price_plan": {
                "execution_mode": "limit_pullback",
                "target_price": 100.0,
                "expires_at": (now - timedelta(minutes=21)).isoformat(),
            }
        }
        policy = {"entry_price_engine": {"persistent_plan": {
            "enabled": True,
            "reaffirmation_extension_minutes": 60,
            "max_reaffirmation_gap_minutes": 20,
        }}}
        self.assertFalse(v5.renew_persistent_entry_plan(pending, now, policy))
        self.assertEqual(100.0, pending["entry_price_plan"]["target_price"])

    def test_existing_limit_can_promote_to_market_when_same_trend_strengthens(self):
        now = datetime(2026, 10, 5, 8, 5, tzinfo=v5.legacy.TZ)
        item = {
            "instrument_id": "eurusd",
            "validation_gate": "enabled_for_paper_trading",
            "wes_entry_authorization": {
                "authorized_at": now.isoformat(timespec="seconds"),
                "directional_admission_passed": True,
                "candidate": {
                    "strategy_id": "daily_weekly_blend",
                    "direction": "short",
                    "execution_authority": "champion_execution",
                },
            },
        }
        weak_decision = {
            "strategy_id": "daily_weekly_blend",
            "direction": "short",
            "raw_score": -42.0,
            "utility": 7.0,
            "directional_admission": {"passed": True, "confirmations": 2},
        }
        weak_fresh = {
            "score": -42.0,
            "data_quality": "passed",
            "signals": {
                "last_close": 1.1250, "atr14": 0.0060, "ema20": 1.1290,
                "ret5_pct": -0.5, "ret20_pct": -1.0, "range55_position": 0.35,
            },
        }
        policy = {"entry_price_engine": {
            "enabled": True, "require_for_all_new_entries": True, "version": "WES-1.3.0",
            "max_wait_minutes": 60, "minimum_pullback_atr": 0.10, "base_pullback_atr": 0.12,
            "overextension_extra_pullback_atr": 0.48, "maximum_pullback_atr": 0.75,
            "ret5_full_scale_percent": 8.0, "ema_distance_full_scale_atr": 2.5,
            "structural_ema20_buffer_atr": 0.25, "max_target_distance_percent": {"eurusd": 0.75},
            "market_entry": {
                "enabled": True, "max_wait_minutes": 15, "min_daily_abs_score": 55,
                "min_weekly_abs_score": 20, "min_selected_utility": 8, "min_confirmations": 2,
                "minimum_absolute_momentum_pct": 0.15, "require_daily_weekly_alignment": True,
                "require_momentum_alignment": True, "maximum_overextension_score": 0.90,
                "block_immediate_market_after_stop": True, "allow_limit_to_market_promotion": True,
            },
        }}
        pending = v5.freeze_decision(
            item, weak_decision, weak_fresh,
            {"score": -25.0, "data_quality": "passed"}, now, policy
        )
        self.assertEqual("limit_pullback", pending["entry_price_plan"]["execution_mode"])
        original_target = pending["entry_price_plan"]["target_price"]

        strong_decision = dict(
            weak_decision,
            raw_score=-72.0,
            utility=12.0,
            directional_admission={"passed": True, "confirmations": 3},
        )
        strong_fresh = {
            **weak_fresh,
            "score": -72.0,
            "signals": {**weak_fresh["signals"], "ret5_pct": -1.3, "ret20_pct": -3.2},
        }
        predecessor_id = pending["decision_id"]
        promoted = v5.promote_pending_decision(
            item,
            pending,
            strong_decision,
            strong_fresh,
            {"score": -44.0, "data_quality": "passed"},
            policy,
            now + timedelta(minutes=20),
            week_id="2026-W41",
        )
        self.assertIsNotNone(promoted)
        self.assertNotEqual(predecessor_id, promoted["decision_id"])
        self.assertEqual(predecessor_id, promoted["predecessor_decision_id"])
        self.assertEqual("limit_pullback", pending["entry_price_plan"]["execution_mode"])
        self.assertEqual("market_now", promoted["entry_price_plan"]["execution_mode"])
        self.assertEqual(original_target, promoted["entry_price_plan"]["original_target_price"])
        self.assertIn("promoted_from_limit_at", promoted["entry_price_plan"])

    def test_market_entry_uses_freshest_completed_5m_bar_after_decision(self):
        now = datetime(2026, 10, 5, 9, 20, tzinfo=v5.legacy.TZ)
        decided = now - timedelta(minutes=15)
        pending = {
            "entry_not_before": decided.isoformat(),
            "decision": {"direction": "long"},
            "entry_price_plan": {
                "instrument_id": "sp500_futures",
                "direction": "long",
                "execution_mode": "market_now",
                "order_type": "market",
                "entry_not_before": decided.isoformat(),
                "expires_at": (now + timedelta(minutes=10)).isoformat(),
            },
        }
        idx = pd.DatetimeIndex([
            now - timedelta(minutes=10),
            now - timedelta(minutes=5),
        ])
        bars = pd.DataFrame(
            {
                "Open": [7800.0, 7804.0],
                "High": [7806.0, 7810.0],
                "Low": [7798.0, 7802.0],
                "Close": [7804.0, 7808.0],
            },
            index=idx,
        )
        with patch.object(v5.v2, "intraday_bars", return_value=bars):
            point = v5.entry_point("ES=F", pending, now)
        self.assertIsNotNone(point)
        self.assertEqual(7808.0, point["price"])
        self.assertEqual(7810.0, point["observed_high"])
        self.assertEqual(7802.0, point["observed_low"])
        self.assertIn("market_now_completed_bar", point["source"])

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

    def test_recent_touch_of_just_expired_frozen_plan_is_recovered_before_refresh(self):
        now = datetime(2026, 9, 21, 9, 10, tzinfo=v5.legacy.TZ)
        decided = now - timedelta(minutes=65)
        touch_at = now - timedelta(minutes=10)
        item = {
            "instrument_id": "sp500_futures",
            "pending_entry_decision": {
                "decided_at": decided.isoformat(),
                "entry_not_before": decided.isoformat(),
                "decision": {"strategy_id": "base_v2", "direction": "long"},
                "fresh_signal": {"risk_distance": {"stop_price_distance": 2.0, "take_price_distance": 3.0}},
                "weekly_signal": {"regime": "test"},
                "entry_price_plan": {
                    "instrument_id": "sp500_futures",
                    "direction": "long",
                    "target_price": 100.0,
                    "entry_not_before": decided.isoformat(),
                    "expires_at": (now - timedelta(minutes=5)).isoformat(),
                },
                "authorization_basis": {
                    "strategy_id": "base_v2",
                    "direction": "long",
                    "directional_admission_passed": True,
                },
            },
        }
        bars = pd.DataFrame(
            {"High": [101.0], "Low": [99.5]},
            index=pd.DatetimeIndex([touch_at]),
        )
        with patch.object(v5.v2, "intraday_bars", return_value=bars):
            recovered = v5.recover_frozen_pending_touch(item, "ES=F", now)
        self.assertIsNotNone(recovered)
        pending, point = recovered
        self.assertEqual(100.0, point["price"])
        self.assertEqual("base_v2", pending["decision"]["strategy_id"])

    def test_entry_replay_never_reconstructs_touch_older_than_global_live_lag(self):
        now = datetime(2026, 9, 21, 9, 10, tzinfo=v5.legacy.TZ)
        decided = now - timedelta(minutes=50)
        pending = {
            "entry_not_before": decided.isoformat(),
            "decision": {"direction": "long"},
            "entry_price_plan": {
                "instrument_id": "sp500_futures",
                "direction": "long",
                "target_price": 100.0,
                "entry_not_before": decided.isoformat(),
                "expires_at": (now + timedelta(minutes=10)).isoformat(),
            },
        }
        old_touch = pd.DataFrame(
            {"High": [101.0], "Low": [99.5]},
            index=pd.DatetimeIndex([now - timedelta(minutes=25)]),
        )
        with patch.object(v5.v2, "intraday_bars", return_value=old_touch):
            self.assertIsNone(v5.entry_point("ES=F", pending, now))

    def test_short_sell_limit_executes_when_bar_high_touches_frozen_target(self):
        now = datetime(2026, 9, 21, 9, 10, tzinfo=v5.legacy.TZ)
        decided = now - timedelta(minutes=5)
        pending = {
            "entry_not_before": decided.isoformat(),
            "decision": {"direction": "short"},
            "entry_price_plan": {
                "instrument_id": "eurusd",
                "direction": "short",
                "target_price": 1.1372,
                "entry_not_before": decided.isoformat(),
                "expires_at": (now + timedelta(minutes=55)).isoformat(),
            },
        }
        touched = pd.DataFrame(
            {"High": [1.13725], "Low": [1.1360]},
            index=pd.DatetimeIndex([now]),
        )
        with patch.object(v5.v2, "intraday_bars", return_value=touched):
            point = v5.entry_point("EURUSD=X", pending, now)
        self.assertIsNotNone(point)
        self.assertEqual(1.1372, point["price"])

    def test_btc_frozen_limits_execute_on_coinbase_5m_touch(self):
        now = datetime(2026, 9, 21, 9, 10, tzinfo=v5.legacy.TZ)
        decided = now - timedelta(minutes=5)

        long_pending = {
            "entry_not_before": decided.isoformat(),
            "decision": {"direction": "long"},
            "entry_price_plan": {
                "instrument_id": "btcusd",
                "direction": "long",
                "target_price": 83000.0,
                "entry_not_before": decided.isoformat(),
                "expires_at": (now + timedelta(minutes=55)).isoformat(),
            },
        }
        long_touch = [
            risk_market.PriceBar(
                ts=now,
                high=83100.0,
                low=82950.0,
                source="test:coinbase:5m",
            )
        ]
        with patch.object(risk_market, "fetch_coinbase_bars", return_value=long_touch):
            point = v5.entry_point("BTC-USD", long_pending, now)
        self.assertIsNotNone(point)
        self.assertEqual(83000.0, point["price"])

        short_pending = {
            "entry_not_before": decided.isoformat(),
            "decision": {"direction": "short"},
            "entry_price_plan": {
                "instrument_id": "btcusd",
                "direction": "short",
                "target_price": 84000.0,
                "entry_not_before": decided.isoformat(),
                "expires_at": (now + timedelta(minutes=55)).isoformat(),
            },
        }
        short_touch = [
            risk_market.PriceBar(
                ts=now,
                high=84050.0,
                low=83900.0,
                source="test:coinbase:5m",
            )
        ]
        with patch.object(risk_market, "fetch_coinbase_bars", return_value=short_touch):
            point = v5.entry_point("BTC-USD", short_pending, now)
        self.assertIsNotNone(point)
        self.assertEqual(84000.0, point["price"])

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
