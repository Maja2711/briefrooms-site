from __future__ import annotations

import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import daily_eurusd_reentry_safety as safety
import daily_eurusd_belief_decision as belief
import daily_eurusd_production_policy as policy
import daily_eurusd_spot_v19 as runtime
from belief_market_data_adapter import Bar, MarketSnapshot


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def fixture(direction="LONG", period=60, retracement=.30, now=None):
    now = now or datetime(2026, 10, 20, 10, 15, tzinfo=timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    level, opening, peak = 1.107, 1.100, 1.110
    closes = [level + .02] * 200
    if period == 60:
        closes[-60:] = [level] * 60
    elif period == 100:
        closes[-100:-60] = [level + .0075] * 40
        closes[-60:] = [level - .005] * 60
    elif period == 200:
        closes[:100] = [level + .005] * 100
        closes[100:] = [level - .005] * 100
    else:
        closes = [level + .03] * 200
    daily = [{"timestamp": stamp(start - timedelta(days=200 - i)),
              "open": price, "high": price + .002, "low": price - .002, "close": price}
             for i, price in enumerate(closes)]
    if period in ("swing", "single_swing"):
        for index in ([-40, -25] if period == "swing" else [-25]):
            daily[index]["low"] = level
    daily.append({"timestamp": stamp(start), "open": opening, "high": peak,
                  "low": opening, "close": level + .0006})
    touch_start, confirm_start = now - timedelta(minutes=10), now - timedelta(minutes=5)
    minute = []
    for i in range(int((now - start).total_seconds() // 60)):
        at = start + timedelta(minutes=i)
        row = {"timestamp": stamp(at), "open": 1.109, "high": 1.1091,
               "low": 1.1089, "close": 1.109}
        if i == 0:
            row.update(open=opening, low=opening)
        if at == max(start, touch_start - timedelta(minutes=5)):
            row["high"] = peak
        if touch_start <= at < confirm_start:
            row.update(open=level + .0003, high=level + .0004,
                       low=peak - retracement * (peak - opening), close=level + .0002)
        if confirm_start <= at:
            row.update(open=level + .0002, high=level + .0007,
                       low=level + .0002, close=level + .0006)
        minute.append(row)
    loss_at = now - timedelta(minutes=14, seconds=30)
    history = {"trades": [{
        "trade_id": "last-native-loss", "instrument": "EUR/USD", "direction": direction,
        "closed_at": stamp(loss_at), "r_multiple": -.35,
        "entry_score": 58.43 if direction == "LONG" else 41.57,
        "entry_confidence": .3936, "decision_source": "NATIVE",
        "entry_thesis": {"source": "NATIVE", "source_family": "NATIVE_BELIEF_FIRST",
                         "direction": direction, "score": 58.43 if direction == "LONG" else 41.57,
                         "confidence": .3936, "components": {}},
    }]}
    if direction == "SHORT":
        for row in daily + minute:
            old = dict(row)
            row.update(open=2.2 - old["open"], close=2.2 - old["close"],
                       high=2.2 - old["low"], low=2.2 - old["high"])
    score = 62.0 if direction == "LONG" else 38.0
    probability = .65 if direction == "LONG" else .35
    final = {
        "owner": "NATIVE_DAILY_EURUSD_BELIEF_FIRST_DECISION_ENGINE",
        "direction": direction, "decision_source": "NATIVE_BELIEF_FIRST",
        "score": score, "confidence": .55, "epistemic_aggregate_authoritative": True,
        "routing": ["RAW_SOURCES", "SPECIALIZED_ADAPTERS", "OBSERVATIONS",
                    "EVIDENCE_ASSESSMENT", "BELIEF_CORE", "NATIVE_DAILY_EURUSD_FINAL_DECISION"],
        "epistemic_consumer": {"available": True, "contract": "epistemic-consumer-interface-v1",
                               "aggregate_authoritative": True, "aggregate_probability": score / 100,
                               "source_sha256": "prospective-epistemic-facts"},
        "used_beliefs": [{"belief_id": name, "probability": probability,
                         "observed_at": stamp(now - timedelta(minutes=3))}
                        for name in (safety.ANCHOR, "eurusd.usd_environment.supportive",
                                     "eurusd.us_rates_pressure.supportive")],
    }
    payload = {"instrument": "EUR/USD", "direction": direction, "score": score,
               "confidence": .55, "timestamp": stamp(now), "metadata": {"final_decision": final}}
    return payload, history, {"daily_bars": daily, "minute_bars": minute}, now


def evaluate(data):
    payload, history, market, now = data
    return safety.evaluate(payload, history, observed_at=now, market_context=market)


def candidate(payload, now):
    metadata = copy.deepcopy(payload["metadata"])
    metadata["final_decision"] = belief.attach_decision_provenance(
        metadata["final_decision"], {}, observed_at=now)
    metadata.update(decision_source="NATIVE", market_snapshot_sha256="market-facts-sha")
    side = 1 if payload["direction"] == "LONG" else -1
    return runtime.DailyEngineOutput(
        instrument="EUR/USD", timestamp=stamp(now), direction=payload["direction"],
        score=payload["score"], confidence=payload["confidence"], entry=1.1,
        stop=1.1 - side * .003, target=1.1 + side * .0054,
        horizon="intraday_to_27h", engine_version=runtime.ENGINE_VERSION,
        status="SIGNAL", decision_mode="WITH", metadata=metadata,
    ).validate()


class PostLossTechnicalSafetyTests(unittest.TestCase):
    def test_all_three_conditions_pass_for_long_and_short(self):
        for direction in ("LONG", "SHORT"):
            with self.subTest(direction=direction):
                data = fixture(direction)
                frozen = copy.deepcopy(data[:3])
                result = evaluate(data)
                self.assertTrue(result["allowed"], result)
                self.assertTrue(result["required"])
                self.assertEqual("SMA_D1", result["support"]["kind"])
                self.assertEqual(result["prior_trade_id"], "last-native-loss")
                self.assertEqual(data[:3], frozen)

    def test_each_daily_ma_can_independently_supply_the_held_level(self):
        for period in (60, 100, 200):
            for direction in ("LONG", "SHORT"):
                with self.subTest(period=period, direction=direction):
                    result = evaluate(fixture(direction, period=period))
                    self.assertTrue(result["allowed"], result)
                    self.assertEqual(period, result["support"]["period"])

    def test_30_percent_boundary_is_inclusive_and_29_9_percent_is_blocked(self):
        for direction in ("LONG", "SHORT"):
            for fraction, allowed in ((.299, False), (.30, True), (.31, True)):
                with self.subTest(direction=direction, fraction=fraction):
                    result = evaluate(fixture(direction, retracement=fraction))
                    self.assertEqual(allowed, result["allowed"], result)
                    if not allowed:
                        self.assertEqual("daily_retracement_below_30_percent", result["reason"])

    def test_strong_signal_alone_cannot_replace_held_support(self):
        result = evaluate(fixture(period=None))
        self.assertFalse(result["allowed"])
        self.assertEqual("post_loss_strong_support_or_held_retest_missing", result["reason"])

    def test_two_confirmed_daily_swings_can_supply_structural_support(self):
        for direction in ("LONG", "SHORT"):
            with self.subTest(direction=direction):
                result = evaluate(fixture(direction, period="swing"))
                self.assertTrue(result["allowed"], result)
                self.assertEqual("RETESTED_D1_SWING", result["support"]["kind"])
                self.assertGreaterEqual(result["support"]["independent_retests"], 1)

    def test_single_daily_pivot_is_not_classified_as_strong_structural_support(self):
        self.assertFalse(evaluate(fixture(period="single_swing"))["allowed"])

    def test_merely_touching_a_level_without_confirmation_is_blocked(self):
        data = fixture()
        for row in data[2]["minute_bars"][-5:]:
            row.update(open=1.1072, close=1.1072, high=1.1073)
        self.assertFalse(evaluate(data)["allowed"])

    def test_broken_support_is_not_a_held_retest(self):
        for direction in ("LONG", "SHORT"):
            data = fixture(direction)
            row = data[2]["minute_bars"][-1]
            if direction == "LONG":
                row["low"] = 1.106
            else:
                row["high"] = 1.094
            self.assertFalse(evaluate(data)["allowed"])

    def test_strong_score_and_confidence_are_both_required(self):
        for direction in ("LONG", "SHORT"):
            for score, confidence, allowed in ((59.99 if direction == "LONG" else 40.01, .55, False),
                                               (60 if direction == "LONG" else 40, .4999, False),
                                               (60 if direction == "LONG" else 40, .50, True)):
                data = fixture(direction)
                data[0].update(score=score, confidence=confidence)
                data[0]["metadata"]["final_decision"].update(score=score, confidence=confidence)
                self.assertEqual(allowed, evaluate(data)["allowed"])

    def test_confidence_cannot_bypass_an_opposing_trend_anchor(self):
        data = fixture()
        data[0]["metadata"]["final_decision"]["used_beliefs"][0]["probability"] = .40
        self.assertFalse(evaluate(data)["allowed"])

    def test_three_aligned_beliefs_are_required(self):
        data = fixture()
        data[0]["metadata"]["final_decision"]["used_beliefs"].pop()
        self.assertFalse(evaluate(data)["allowed"])

    def test_refreshing_old_source_evidence_does_not_create_a_new_post_loss_signal(self):
        data = fixture()
        old = datetime.fromisoformat(data[1]["trades"][0]["closed_at"].replace("Z", "+00:00")) - timedelta(seconds=1)
        data[0]["metadata"]["final_decision"]["used_beliefs"][0]["observed_at"] = stamp(old)
        self.assertFalse(evaluate(data)["allowed"])

    def test_shadow_or_non_authoritative_signal_cannot_qualify(self):
        data = fixture()
        data[0]["metadata"]["final_decision"]["epistemic_aggregate_authoritative"] = False
        self.assertFalse(evaluate(data)["allowed"])

    def test_small_losses_also_require_the_full_technical_gate(self):
        data = fixture()
        data[1]["trades"][0]["r_multiple"] = -.01
        result = safety.evaluate(data[0], data[1], observed_at=data[3])
        self.assertFalse(result["allowed"])
        self.assertTrue(result["required"])
        self.assertTrue(evaluate(data)["allowed"])

    def test_switching_strategy_label_does_not_erase_the_latest_loss(self):
        data = fixture()
        data[1]["trades"][0]["entry_thesis"]["source_family"] = "NATIVE_COMPONENTS"
        result = evaluate(data)
        self.assertTrue(result["required"])
        self.assertEqual("last-native-loss", result["prior_trade_id"])

    def test_latest_small_loss_resets_the_post_loss_market_sequence(self):
        data = fixture()
        recent = copy.deepcopy(data[1]["trades"][0])
        recent.update(trade_id="new-small-loss", closed_at=stamp(data[3] - timedelta(minutes=2)), r_multiple=-.01)
        data[1]["trades"].append(recent)
        data[0]["metadata"]["final_decision"]["used_beliefs"][0]["observed_at"] = stamp(data[3] - timedelta(seconds=30))
        result = evaluate(data)
        self.assertFalse(result["allowed"])
        self.assertEqual("new-small-loss", result["prior_trade_id"])

    def test_correction_before_the_loss_cannot_be_reused(self):
        data = fixture()
        data[1]["trades"][0]["closed_at"] = stamp(data[3] - timedelta(minutes=5, seconds=30))
        self.assertFalse(evaluate(data)["allowed"])

    def test_incomplete_five_minute_confirmation_does_not_count(self):
        data = fixture()
        payload, history, market, now = data
        result = safety.evaluate(payload, history, market_context=market, observed_at=now - timedelta(seconds=1))
        self.assertFalse(result["allowed"])

    def test_future_and_in_progress_bars_do_not_supply_levels_or_extremes(self):
        data = fixture()
        before = evaluate(data)
        data[2]["daily_bars"][-1].update(high=99, low=.01)
        data[2]["daily_bars"].append({"timestamp": stamp(data[3] + timedelta(days=1)),
                                     "open": 99, "high": 100, "low": 98, "close": 99})
        data[2]["minute_bars"].append({"timestamp": stamp(data[3]),
                                     "open": 99, "high": 100, "low": .01, "close": 99})
        after = evaluate(data)
        self.assertTrue(after["allowed"], after)
        self.assertEqual(before["source_sha256"], after["source_sha256"])

    def test_a_new_daily_extreme_invalidates_the_old_pullback(self):
        data = fixture()
        data[2]["minute_bars"][-1]["high"] = 1.111
        self.assertFalse(evaluate(data)["allowed"])

    def test_missing_or_stale_market_data_fails_closed(self):
        data = fixture()
        self.assertFalse(safety.evaluate(data[0], data[1], observed_at=data[3])["allowed"])
        data[2]["minute_bars"] = data[2]["minute_bars"][:-4]
        self.assertEqual("post_loss_market_path_stale", evaluate(data)["reason"])

    def test_a_missing_minute_cannot_hide_the_daily_extreme(self):
        data = fixture()
        del data[2]["minute_bars"][3]
        self.assertEqual("incomplete_current_d1_intraday_path", evaluate(data)["reason"])

    def test_non_finite_prices_and_conflicting_duplicates_fail_closed(self):
        for variant in ("nan", "duplicate"):
            data = fixture()
            if variant == "nan":
                data[2]["minute_bars"][10]["high"] = float("nan")
            else:
                row = dict(data[2]["minute_bars"][10])
                row["high"] += .001
                data[2]["minute_bars"].append(row)
            self.assertFalse(evaluate(data)["allowed"])

    def test_yahoo_historical_d1_range_inconsistency_is_quarantined_not_repaired(self):
        # A real Yahoo 2y EURUSD 1d quote may have close outside its reported
        # high/low by a fraction of a pip. Closes support SMA; fabricated
        # extremes may never support ATR or two-pivot swing confirmation.
        data = fixture()
        d1 = data[2]["daily_bars"]
        d1[-50]["high"] = d1[-50]["close"] - .0002
        before = copy.deepcopy(d1)
        result = evaluate(data)
        self.assertTrue(result["allowed"], result)
        self.assertEqual(before, d1, "source historical candles must remain unchanged")
        self.assertEqual(result["d1_quality"]["unverified_completed_ohlc"], 1)
        self.assertEqual(len(result["d1_quality"]["recent_64_unverified_ohlc"]), 1)
        self.assertFalse(result["d1_quality"]["unverified_fields_used_for_pivots_or_atr"])

    def test_d1_inconsistency_inside_atr14_window_still_fails_closed(self):
        data = fixture()
        d1 = data[2]["daily_bars"]
        d1[-6]["high"] = d1[-6]["close"] - .0002
        result = evaluate(data)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"],
                         "post_loss_invalid_market_or_signal_context:recent_d1_ohlc_unverified_for_atr")

    def test_incomplete_active_d1_ohlc_cannot_be_repurposed_as_a_level(self):
        data = fixture()
        active = data[2]["daily_bars"][-1]
        active["high"] = active["open"] - .001
        result = evaluate(data)
        self.assertTrue(result["allowed"], result)
        self.assertEqual(result["session"]["start_provenance"], "SOURCE_CANONICAL_D1_START")
        self.assertFalse(data[2]["daily_bars"][-1]["high"] >= active["open"])

    def test_yahoo_live_d1_preview_requires_exact_minute_open(self):
        data = fixture(now=datetime(2026, 10, 26, 11, 45, tzinfo=timezone.utc))
        preview = data[2]["daily_bars"][-1]
        preview["timestamp"] = stamp(data[3] - timedelta(seconds=15))
        result = evaluate(data)
        self.assertTrue(result["allowed"], result)
        self.assertEqual(result["session"]["start_provenance"],
                         "D1_PREVIEW_MATCHED_TO_EXACT_1M_SESSION_OPEN")
        preview["open"] += .0002
        blocked = evaluate(data)
        self.assertFalse(blocked["allowed"], blocked)
        self.assertEqual(blocked["reason"],
                         "post_loss_invalid_market_or_signal_context:live_d1_session_open_not_independently_verified")

    def test_dst_weekend_live_preview_uses_proved_minute_reopen_not_stale_d1_hour(self):
        now = datetime(2026, 10, 26, 11, 40, tzinfo=timezone.utc)
        last_d1 = datetime(2026, 10, 22, 23, tzinfo=timezone.utc)
        daily = [{"timestamp": stamp(last_d1 - timedelta(days=200-i)),
                  "open": 1.10, "high": 1.101, "low": 1.099, "close": 1.10}
                 for i in range(201)]
        daily.append({"timestamp": stamp(now - timedelta(seconds=30)),
                      "open": 1.1000, "high": 1.102, "low": 1.099, "close": 1.1001})
        minute = [
            {"timestamp": stamp(datetime(2026, 10, 23, 20, 59, tzinfo=timezone.utc)),
             "open": 1.0998, "high": 1.0999, "low": 1.0997, "close": 1.0998},
            {"timestamp": stamp(datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)),
             "open": 1.1000, "high": 1.1001, "low": 1.0999, "close": 1.1000},
        ]
        rows, provenance = safety._validated_d1_source(daily, now, minute)
        self.assertEqual(provenance["session"]["timestamp"], "2026-10-26T00:00:00Z")
        self.assertEqual(provenance["active_session_source"],
                         "LIVE_D1_OPEN_INDEPENDENTLY_VERIFIED_1M")
        self.assertEqual(rows[-1]["timestamp"], "2026-10-22T23:00:00Z")
        # A missing first minute must never be treated as a complete session.
        with self.assertRaisesRegex(ValueError, "live_d1_session_open_not_independently_verified"):
            safety._validated_d1_source(daily, now, minute[:-1])

    def test_historical_d1_bad_close_and_future_preview_fail_closed(self):
        data = fixture()
        data[2]["daily_bars"][-50]["close"] = float("nan")
        result = evaluate(data)
        self.assertFalse(result["allowed"])
        self.assertIn("non_finite_number", result["reason"])
        data = fixture()
        data[2]["daily_bars"][-1]["timestamp"] = stamp(data[3] - timedelta(hours=3, minutes=15))
        result = evaluate(data)
        self.assertFalse(result["allowed"])
        self.assertIn("unverified_d1_preview_timestamp", result["reason"])

    def test_the_same_daily_candle_remains_protected_after_one_hour(self):
        data = fixture()
        data[1]["trades"][0]["closed_at"] = stamp(data[3] - timedelta(hours=2))
        self.assertTrue(evaluate(data)["required"])

    def test_first_hour_remains_protected_across_daily_rollover(self):
        now = datetime(2026, 10, 20, 0, 15, tzinfo=timezone.utc)
        data = fixture(now=now)
        data[1]["trades"][0]["closed_at"] = stamp(now - timedelta(minutes=20))
        result = evaluate(data)
        self.assertTrue(result["required"])
        self.assertTrue(result["allowed"], result)

    def test_previous_daily_loss_outside_first_hour_does_not_add_a_new_gate(self):
        data = fixture()
        data[1]["trades"][0]["closed_at"] = stamp(data[3] - timedelta(hours=20))
        result = evaluate(data)
        self.assertFalse(result["required"])
        self.assertTrue(result["allowed"])

    def test_without_a_same_direction_loss_the_gate_is_not_applied(self):
        data = fixture()
        data[1]["trades"][0]["direction"] = "SHORT"
        self.assertFalse(evaluate(data)["required"])
        self.assertTrue(safety.evaluate(data[0], {}, observed_at=data[3])["allowed"])

    def test_live_execution_must_stay_in_the_verified_zone_and_session(self):
        for direction in ("LONG", "SHORT"):
            proof = evaluate(fixture(direction))
            now = datetime(2026, 10, 20, 10, 15, tzinfo=timezone.utc)
            execution = {"fill_price": (proof["entry_zone"]["minimum"] + proof["entry_zone"]["maximum"]) / 2,
                         "verified_at": stamp(now),
                         "selected_quote": {"timestamp": stamp(now)}}
            self.assertIsNone(safety.check_execution(proof, execution))
            execution["fill_price"] = proof["entry_zone"]["maximum"] + .0001
            self.assertEqual("post_loss_execution_left_verified_support_zone", safety.check_execution(proof, execution))

    def test_old_quote_cannot_be_relabelled_as_post_confirmation_execution(self):
        proof = evaluate(fixture())
        now = datetime(2026, 10, 20, 10, 15, tzinfo=timezone.utc)
        execution = {"fill_price": 1.10775, "verified_at": stamp(now),
                     "selected_quote": {"timestamp": stamp(now - timedelta(seconds=1))}}
        self.assertEqual("post_loss_execution_quote_predates_confirmation", safety.check_execution(proof, execution))

    def test_execution_across_session_boundary_requires_a_new_decision(self):
        proof = evaluate(fixture())
        after = datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)
        execution = {"fill_price": 1.10775, "verified_at": stamp(after),
                     "selected_quote": {"timestamp": stamp(after)}}
        self.assertEqual("post_loss_daily_session_changed_before_execution", safety.check_execution(proof, execution))


class PostLossRuntimeAdmissionTests(unittest.TestCase):
    def admission(self, data):
        payload, history, market, now = data
        output = candidate(payload, now)
        with patch.object(belief, "calendar_safety", return_value={"blocked": False, "reason": "clear"}):
            admitted = runtime._execution_admission(output, history, observed_at=now, market_context=market)
        return output, admitted

    def test_all_conditions_reach_native_position_creation_in_both_directions(self):
        for direction in ("LONG", "SHORT"):
            with self.subTest(direction=direction):
                data = fixture(direction)
                output, admitted = self.admission(data)
                self.assertTrue(admitted["allowed"], admitted)
                metadata = dict(output.metadata)
                metadata["execution_admission"] = admitted
                output = runtime._clone(output, metadata=metadata)
                mid = data[2]["minute_bars"][-1]["close"]
                side = 1 if direction == "LONG" else -1
                fill = mid + side * .00015
                execution = {"status": "VERIFIED_FILL", "verified": True,
                             "verified_at": stamp(data[3]), "fill_price": fill,
                             "selected_quote": {"timestamp": stamp(data[3]), "source": "test-provider"},
                             "execution_cost_contract": "DAILY_EURUSD_BID_ASK_V1",
                             "synthetic_half_spread_pips": 1.5, "synthetic_spread_pips": 3}
                prepared = runtime._clone(output, entry=fill, stop=fill - side * .003,
                                          target=fill + side * .0054,
                                          metadata={**output.metadata, "execution_price_engine": execution})
                context = {"policy_state": policy.default_state(data[3] - timedelta(minutes=1)),
                           "decision_at": data[3]}
                with patch.object(runtime, "_cycle_context", context), patch.object(runtime, "_epe_prepare", return_value=prepared):
                    result = runtime.prepare_entry_candidate(output, [], data[3])
                self.assertEqual(direction, result.direction, result.metadata)
                position = runtime._create_position_v19(result.to_dict())
                proof = position["decision_manifest"]["execution_admission"]["post_loss_technical_guard"]
                self.assertTrue(proof["allowed"])
                self.assertEqual("last-native-loss", proof["prior_trade_id"])

    def test_failed_market_gate_never_calls_epe(self):
        data = fixture(retracement=.299)
        output, admitted = self.admission(data)
        self.assertFalse(admitted["allowed"])
        output = runtime._clone(output, metadata={**output.metadata, "execution_admission": admitted})
        with patch.object(runtime, "_epe_prepare") as epe:
            result = runtime.prepare_entry_candidate(output, [], data[3])
        epe.assert_not_called()
        self.assertEqual("FLAT", result.direction)
        self.assertEqual("LONG", result.metadata["final_decision"]["direction"])

    def test_verified_epe_quote_cannot_jump_out_of_the_admitted_support_zone(self):
        data = fixture()
        output, admitted = self.admission(data)
        self.assertTrue(admitted["allowed"], admitted)
        output = runtime._clone(output, metadata={**output.metadata, "execution_admission": admitted})
        execution = {"verified": True, "status": "VERIFIED_FILL", "fill_price": 1.12,
                     "verified_at": stamp(data[3]), "selected_quote": {"timestamp": stamp(data[3])}}
        prepared = runtime._clone(output, metadata={**output.metadata, "execution_price_engine": execution})
        with patch.object(runtime, "_epe_prepare", return_value=prepared):
            result = runtime.prepare_entry_candidate(output, [], data[3])
        self.assertEqual("FLAT", result.direction)
        self.assertIn("post_loss_execution_left_verified_support_zone",
                      result.metadata["execution_admission"]["reasons"])

    def test_runtime_fetches_d1_only_for_post_loss_admission(self):
        data = fixture()
        output = candidate(data[0], data[3])
        client = Mock()
        client.bars.return_value = data[2]["daily_bars"]
        context = {"market_client": client, "monitor_bars": data[2]["minute_bars"]}
        with patch.object(runtime, "_cycle_context", context), patch.object(
                belief, "calendar_safety", return_value={"blocked": False, "reason": "clear"}):
            result = runtime._execution_admission(output, data[1], observed_at=data[3])
        self.assertTrue(result["allowed"], result)
        client.bars.assert_called_once_with("EURUSD=X", "2y", "1d")
        client.reset_mock()
        with patch.object(runtime, "_cycle_context", context), patch.object(
                belief, "calendar_safety", return_value={"blocked": False, "reason": "clear"}):
            result = runtime._execution_admission(output, {}, observed_at=data[3])
        self.assertTrue(result["allowed"])
        client.bars.assert_not_called()

    def test_missing_d1_does_not_enter_the_open_position_risk_path(self):
        data = fixture()
        now = data[3]
        rows = [Bar(now - timedelta(minutes=30 * (30 - i)), 1.107, open=1.107,
                    high=1.108, low=1.106) for i in range(31)]
        snapshot = MarketSnapshot({"EURUSD=X": rows})
        final = copy.deepcopy(data[0]["metadata"]["final_decision"])
        consumer = final["epistemic_consumer"]
        with patch.object(belief, "load_effective_state", return_value=({}, consumer)), patch.object(
                belief, "synthesize", return_value=final), patch.object(
                runtime.legacy_macro_context, "context", return_value={}), patch.object(
                runtime, "_execution_admission", side_effect=AssertionError("risk must not call entry")):
            output = runtime.build_output(snapshot, data[1], allow_entry=False)
        self.assertEqual("NO_TRADE", output.status)
        self.assertEqual("DISABLED", output.metadata["execution_admission"]["status"])


if __name__ == "__main__":
    unittest.main()
