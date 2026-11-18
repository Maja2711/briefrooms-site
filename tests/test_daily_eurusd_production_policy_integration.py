from __future__ import annotations

import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import daily_eurusd_belief_decision as belief
import daily_eurusd_decision_record as records
import daily_eurusd_production_policy as policy
import daily_eurusd_spot_v19 as runtime
from belief_market_data_adapter import Bar
from tests import test_daily_eurusd_production_policy as fixtures


class ProductionPolicyRuntimeIntegrationTests(unittest.TestCase):
    def candidate(self, at, score=53.5, direction="LONG", confidence=.8):
        final = belief.attach_decision_provenance({
            "direction": direction, "score": score, "confidence": confidence,
            "decision_source": "NATIVE_BELIEF_FIRST", "used_beliefs": [],
        }, {}, observed_at=at)
        execution = {
            "status": "VERIFIED_FILL", "verified": True,
            "verified_at": policy._iso(at), "execution_cost_contract": "DAILY_EURUSD_BID_ASK_V1",
            "price_type": "LIVE_CONSENSUS_MID_PLUS_CONSERVATIVE_BUFFER",
            "synthetic_half_spread_pips": 1.5, "synthetic_spread_pips": 3.0,
        }
        return runtime.DailyEngineOutput(
            instrument="EUR/USD", timestamp=policy._iso(at), direction=direction,
            score=score, confidence=confidence, entry=1.10015,
            stop=1.09 if direction == "LONG" else 1.11030,
            target=1.118 if direction == "LONG" else 1.08230,
            horizon="intraday_to_27h", engine_version=runtime.ENGINE_VERSION,
            status="SIGNAL", decision_mode="WITH", metadata={
                "decision_source": "NATIVE", "final_decision": final,
                "market_snapshot_sha256": "market-facts-sha",
                "execution_admission": {"allowed": True},
                "execution_price_engine": execution,
                "risk": {"risk_plan_version": "initial-geometry-v1"},
            },
        ).validate()

    def closed_native_loss(self, direction="LONG", exit_reason="DYNAMIC_RISK_EXIT"):
        at = datetime(2026, 10, 20, 14, 42, 47, tzinfo=timezone.utc)
        score = 58.43 if direction == "LONG" else 41.57
        candidate = self.candidate(at, score=score, direction=direction, confidence=.3936)
        context = {"policy_state": policy.default_state(at - timedelta(minutes=1)), "decision_at": at}
        with patch.object(runtime, "_cycle_context", context), patch.object(runtime, "_epe_prepare", return_value=candidate):
            admitted = runtime.prepare_entry_candidate(candidate, [], at)
            position = runtime._create_position_v19(admitted.to_dict())
        sign = 1 if direction == "LONG" else -1
        risk = abs(position["entry"] - position["stop"])
        first_mid = position["entry"] + sign * (.00015 - .1 * risk)
        last_mid = (position["stop"] if exit_reason == "STOP_LOSS"
                    else position["entry"] + sign * (.00015 - .4 * risk))
        closed_at = datetime(2026, 10, 20, 14, 50, 28, tzinfo=timezone.utc)
        bars = [Bar(at + timedelta(minutes=1), first_mid), Bar(closed_at, last_mid)]
        trade = runtime.v18._evaluate_position(position, bars, closed_at)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], exit_reason)
        records.validate_trade_binding(trade)
        legacy = {
            "trade_id": "legacy-morning-loss", "direction": direction,
            "closed_at": policy._iso(closed_at - timedelta(hours=6)), "r_multiple": -.3521,
            "entry_thesis": {
                "source": "NATIVE", "source_family": "NATIVE_COMPONENTS", "direction": direction,
                "score": 53.12 if direction == "LONG" else 46.88, "confidence": .364,
                "components": {}, "weights": {},
            },
        }
        return trade, legacy, closed_at

    def admission_after_loss(self, candidate, history):
        with patch.object(belief, "calendar_safety", return_value={"blocked": False, "reason": "clear"}):
            return runtime._execution_admission(candidate, history, observed_at=policy._time(candidate.timestamp))

    def test_reentry_compares_latest_native_loss_when_legacy_loss_exists(self):
        for direction in ("LONG", "SHORT"):
            for exit_reason in ("DYNAMIC_RISK_EXIT", "STOP_LOSS"):
                with self.subTest(direction=direction, exit_reason=exit_reason):
                    trade, legacy, closed_at = self.closed_native_loss(direction, exit_reason)
                    candidate = self.candidate(closed_at + timedelta(seconds=62),
                        score=trade["entry_score"], direction=direction, confidence=.3936)
                    history = {"trades": [trade, legacy]}
                    frozen_history = copy.deepcopy(history)
                    result = self.admission_after_loss(candidate, history)
                    self.assertFalse(result["allowed"])
                    guard = result["same_thesis_reentry_guard"]
                    self.assertEqual(guard["prior_trade_id"], trade["trade_id"])
                    self.assertEqual(guard["current_thesis"]["source_family"], trade["entry_thesis"]["source_family"])
                    self.assertFalse(guard["change"]["material_change"])
                    self.assertEqual(history, frozen_history)

    def test_material_improvement_alone_cannot_bypass_post_loss_market_safety(self):
        for direction in ("LONG", "SHORT"):
            for improvement in ("score", "confidence"):
                with self.subTest(direction=direction, improvement=improvement):
                    trade, legacy, closed_at = self.closed_native_loss(direction)
                    score = trade["entry_score"]
                    confidence = .3936
                    if improvement == "score":
                        score += 3.1 if direction == "LONG" else -3.1
                    else:
                        confidence += .081
                    candidate = self.candidate(closed_at + timedelta(seconds=62),
                        score=score, direction=direction, confidence=confidence)
                    result = self.admission_after_loss(candidate, {"trades": [legacy, trade]})
                    self.assertFalse(result["allowed"])
                    self.assertFalse(result["post_loss_technical_guard"]["allowed"])
                    guard = result["same_thesis_reentry_guard"]
                    self.assertEqual(guard["prior_trade_id"], trade["trade_id"])
                    self.assertTrue(guard["change"]["material_change"])
                    self.assertFalse(guard["generic_loss_cooldown"])

    def _h1_priority_admission(self, direction, percent_b, *, strong=True,
                               h1_available=True, d1_trend=None, mtf_strategy="STRONG_TREND"):
        at = datetime(2026, 11, 18, 9, 31, tzinfo=timezone.utc)
        candidate = self.candidate(at, score=42.7 if direction == "SHORT" else 57.3,
                                   direction=direction)
        h1_at = (at-timedelta(minutes=31)).isoformat()
        d1_at = (at-timedelta(days=1, hours=9, minutes=31)).isoformat()
        h1_ctx = {
            "available": h1_available, "allowed": h1_available,
            "strong_trend_confirmed": strong, "exhaustion": False,
            "range_regime": False, "percent_b": percent_b,
            "last_closed_h1": h1_at,
        }
        mtf = {
            "available": True, "strategy": mtf_strategy, "block_new_entry": False,
            "direction": direction, "h1_volatility": {"last_completed_h1": h1_at},
        }
        d1_ctx = {
            "available": True,
            "trend": d1_trend or ("DOWN" if direction == "SHORT" else "UP"),
            "last_completed_d1": d1_at,
        }
        d1_touch = {"available": True, "last_completed_d1": d1_at,
                    "lower_short_blocked": direction == "SHORT",
                    "upper_long_blocked": direction == "LONG"}
        candidate = runtime._clone(candidate, metadata={
            **candidate.metadata, "bollinger_h1": h1_ctx,
            "multi_timeframe": mtf, "bollinger_d1": d1_ctx,
            "bollinger_d1_touch": d1_touch,
            "h1_volatility_geometry": {"allowed": True},
        })
        context = {"policy_state": policy.default_state(at), "decision_at": at}
        with patch.object(runtime, "_cycle_context", context), patch.object(
            runtime.belief_decision, "calendar_safety",
            return_value={"blocked": False, "reason": "clear"},
        ):
            result = runtime._execution_admission(candidate, {"trades": []}, observed_at=at)
        return result

    def test_actual_admission_strong_short_uses_h1_instead_of_stale_d1_wait(self):
        result = self._h1_priority_admission("SHORT", .25)
        self.assertEqual(result["strong_h1_entry_priority"]["status"], "H1_PRIMARY_READY")
        self.assertNotIn("bollinger_d1_lower_touch_waiting_for_half_retracement", result["reasons"])
        self.assertTrue(result["bollinger_d1_touch"]["lower_short_blocked"])
        self.assertEqual(result["final_direction_preserved"], "SHORT")

    def test_actual_admission_avoids_chasing_h1_lower_band_in_strong_short(self):
        result = self._h1_priority_admission("SHORT", -.0153)
        self.assertIn("h1_strong_trend_waiting_for_hourly_pullback", result["reasons"])
        self.assertNotIn("bollinger_d1_lower_touch_waiting_for_half_retracement", result["reasons"])
        self.assertFalse(result["allowed"])

    def test_actual_admission_strong_long_symmetry_and_weak_trend_d1_fallback(self):
        result = self._h1_priority_admission("LONG", .75)
        self.assertEqual(result["strong_h1_entry_priority"]["status"], "H1_PRIMARY_READY")
        self.assertNotIn("bollinger_d1_upper_touch_waiting_for_half_retracement", result["reasons"])
        weak = self._h1_priority_admission("SHORT", .25, strong=False)
        self.assertIn("bollinger_d1_lower_touch_waiting_for_half_retracement", weak["reasons"])
        conflict = self._h1_priority_admission("LONG", .75, d1_trend="DOWN")
        self.assertIn("bollinger_d1_upper_touch_waiting_for_half_retracement", conflict["reasons"])
        stale = self._h1_priority_admission("SHORT", .25, h1_available=False)
        self.assertIn("bollinger_d1_lower_touch_waiting_for_half_retracement", stale["reasons"])

    def test_strong_signal_alone_cannot_reenter_without_daily_correction_and_support(self):
        for direction in ("LONG", "SHORT"):
            with self.subTest(direction=direction):
                trade, legacy, closed_at = self.closed_native_loss(direction)
                candidate = self.candidate(
                    closed_at + timedelta(seconds=62),
                    score=62.0 if direction == "LONG" else 38.0,
                    direction=direction, confidence=.55,
                )
                result = self.admission_after_loss(candidate, {"trades": [legacy, trade]})
                self.assertFalse(result["allowed"])

    def test_unchanged_signal_cannot_reach_epe_after_native_loss(self):
        trade, legacy, closed_at = self.closed_native_loss()
        at = closed_at + timedelta(seconds=62)
        candidate = self.candidate(at, score=trade["entry_score"], confidence=.3936)
        metadata = {**candidate.metadata, "execution_admission": self.admission_after_loss(candidate, {"trades": [legacy, trade]})}
        candidate = runtime._clone(candidate, metadata=metadata)
        with patch.object(runtime, "_epe_prepare", return_value=candidate) as epe:
            output = runtime.prepare_entry_candidate(candidate, [], at)
        epe.assert_not_called()
        self.assertEqual(output.direction, "FLAT")
        self.assertEqual(output.metadata["final_decision"]["direction"], "LONG")

    def test_legacy_loss_still_requires_material_improvement(self):
        _, legacy, closed_at = self.closed_native_loss()
        for score, allowed in ((53.12, False), (58.43, True)):
            with self.subTest(score=score):
                candidate = self.candidate(closed_at + timedelta(seconds=62), score=score, confidence=.364)
                result = self.admission_after_loss(candidate, {"trades": [legacy]})
                self.assertFalse(result["allowed"])
                self.assertEqual(not result["same_thesis_reentry_guard"]["blocked"], allowed)
                self.assertFalse(result["post_loss_technical_guard"]["allowed"])

    def promoted(self):
        helper = fixtures.ProductionAdmissionPolicyTests()
        helper.setUp()
        state = policy.default_state(helper.start)
        code_hash = records.runtime_code_hash()
        for index in range(policy.PRIMARY_N):
            state, _, at = helper.actual_case(state, index, code_hash=code_hash)
        at += timedelta(hours=22)
        return policy.advance_governance(state, at), at

    def test_admission_filter_consumes_promoted_policy_and_preserves_final_direction(self):
        state, promoted = self.promoted()
        at = promoted + timedelta(days=2)
        candidate = self.candidate(at)
        context = {"policy_state": state, "decision_at": at}
        # Only the external verified price acquisition is replaced. Position
        # creation, manifest persistence contract and policy filter are real.
        with patch.object(runtime, "_cycle_context", context), patch.object(runtime, "_epe_prepare", return_value=candidate):
            output = runtime.prepare_entry_candidate(candidate, [], at)
        self.assertEqual(output.direction, "FLAT")
        self.assertEqual(output.metadata["final_decision"]["direction"], "LONG")
        self.assertEqual(output.metadata["production_policy"]["minimum_score_distance"], 4)
        self.assertEqual(len(context["policy_state"]["cases"]), 41)
        case = context["policy_state"]["cases"][context["policy_state"]["active_case_id"]]
        self.assertFalse(case["frozen"]["production_admitted"])
        self.assertEqual(case["mode"], "PROSPECTIVE_COUNTERFACTUAL")
        manifest = output.metadata["decision_manifest"]
        records.validate_manifest(manifest)
        self.assertEqual(case["frozen"]["manifest_sha256"], manifest["manifest_sha256"])

    def test_actual_position_uses_native_version_and_preserves_exact_manifest_to_close(self):
        at = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        candidate = self.candidate(at, score=60)
        context = {"policy_state": policy.default_state(at - timedelta(minutes=1)), "decision_at": at}
        with patch.object(runtime, "_cycle_context", context), patch.object(runtime, "_epe_prepare", return_value=candidate):
            output = runtime.prepare_entry_candidate(candidate, [], at)
            position = runtime._create_position_v19(output.to_dict())
        self.assertEqual(position["engine_version"], policy.ENGINE_VERSION)
        bars = [Bar(at + timedelta(minutes=1), 1.09, high=1.1000, low=1.0890)]
        trade = runtime.v18._evaluate_position(position, bars, at + timedelta(minutes=1))
        self.assertIsNotNone(trade)
        self.assertEqual(trade["decision_manifest"], position["decision_manifest"])
        state = policy.bind_actual_history(context["policy_state"], {"trades": [trade]}, now=at + timedelta(minutes=2))
        case = next(iter(state["cases"].values()))
        self.assertEqual(case["outcome"]["source"], "CANONICAL_ACTUAL")
        self.assertAlmostEqual(case["outcome"]["net_r"], (trade["exit_price"] - position["entry"]) / (position["entry"] - position["stop"]))

    def test_counterfactual_callback_uses_same_v18_exit_on_complete_common_path(self):
        state, promoted = self.promoted()
        at = promoted + timedelta(days=2)
        candidate = self.candidate(at)
        context = {"policy_state": state, "decision_at": at}
        with patch.object(runtime, "_cycle_context", context), patch.object(runtime, "_epe_prepare", return_value=candidate):
            output = runtime.prepare_entry_candidate(candidate, [], at)
        state = context["policy_state"]
        case = state["cases"][state["active_case_id"]]
        position = case["runtime_position"]
        rows = [Bar(at + timedelta(minutes=i), 1.09, high=1.1000, low=1.0890) for i in (1, 2)]
        reference = runtime.v18._evaluate_position(copy.deepcopy(position), rows, rows[-1].timestamp)
        evaluator = runtime._counterfactual_evaluator(rows, {}, rows[-1].timestamp)
        result = evaluator(position, case)
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["closed_trade"]["exit_price"], reference["exit_price"])
        self.assertEqual(result["closed_trade"]["closed_at"], reference["closed_at"])
        settled = policy.settle_cases(state, evaluator=evaluator, now=rows[-1].timestamp)
        outcome = settled["cases"][case["case_id"]]["outcome"]
        self.assertEqual(outcome["source"], "PROSPECTIVE_COUNTERFACTUAL")
        self.assertFalse(outcome["live_executed"])



    def _factor_probe_manifest(self, at, *, direction="SHORT", code_hash="a"*64):
        return {
            "decision_at": policy._iso(at),
            "runtime_code_sha256": code_hash,
            "decision_id": "prospective-test-id",
            "manifest_sha256": "f"*64,
            "final_decision": {"direction": direction},
            "execution_admission": {
                "allowed": False, "reasons": ["macro_guard"],
                "entry_multifactor": {
                    "schema_version": "eurusd-daily-multifactor-entry-v1",
                    "regime": "STRONG_TREND",
                    "factors": {
                        factor: {"available": True, "confirmed": factor != "BOLLINGER_H1_LOCATION"}
                        for factor in policy.FACTOR_GROUPS
                    },
                },
            },
        }

    def test_frozen_factor_probe_settles_only_on_future_market_price_not_paper_fill(self):
        at = datetime(2026, 11, 18, 9, 30, tzinfo=timezone.utc)
        state = policy.default_state(at)
        manifest = self._factor_probe_manifest(at)
        current_bar = Bar(at-timedelta(minutes=2), 1.1000)
        future_bar = Bar(at+timedelta(hours=3)-timedelta(minutes=1), 1.0990)
        # A future bar already present in the supplied input must NOT be
        # used for an outcome while the decision is still being made.
        state = policy.factor_study_step(state, minute_bars=[future_bar, current_bar],
            now=at, decision_manifest=manifest, runtime_code_sha256="a"*64)
        cases = state["entry_factor_study"]["cases"]
        self.assertEqual(len(cases), 1)
        first = next(iter(cases.values()))
        self.assertEqual(first["status"], "PENDING")
        self.assertFalse(first["frozen"]["canonical_entry_allowed"])
        self.assertIsNone(first["outcome"])
        later = policy.factor_study_step(state, minute_bars=[current_bar, future_bar],
            now=at+timedelta(hours=3))
        outcome = next(iter(later["entry_factor_study"]["cases"].values()))
        self.assertEqual(outcome["status"], "SETTLED")
        self.assertAlmostEqual(outcome["outcome"]["directional_move_pips"], 10.0)
        self.assertTrue(outcome["outcome"]["favorable_direction"])
        self.assertIsNone(outcome["outcome"]["trade_pnl"])
        self.assertFalse(outcome["outcome"]["execution_authority"])
        self.assertEqual(first["frozen"], outcome["frozen"])
        report = policy.factor_study_summary(later, runtime_code_sha256="a"*64,
                                             history={"trades": []})
        self.assertEqual(report["settled_price_proxy_signals"], 1)
        self.assertEqual(report["canonical_actual_closed_trades_current_code"], 0)
        self.assertIsNone(report["validated_best_factor"])
        self.assertTrue(report["not_eligible_to_claim_best_factor"])

    def test_factor_probe_gap_is_not_a_loss_and_immutability_is_validated(self):
        at = datetime(2026, 11, 18, 9, 30, tzinfo=timezone.utc)
        state = policy.factor_study_step(
            policy.default_state(at),
            minute_bars=[Bar(at-timedelta(minutes=2), 1.1000)],
            now=at, decision_manifest=self._factor_probe_manifest(at),
            runtime_code_sha256="a"*64,
        )
        later = policy.factor_study_step(state,
            minute_bars=[Bar(at-timedelta(minutes=2), 1.1000)],
            now=at+timedelta(hours=3,minutes=6))
        result = next(iter(later["entry_factor_study"]["cases"].values()))
        self.assertEqual(result["status"], "DATA_GAP")
        self.assertIsNone(result["outcome"])
        still_missing = policy.factor_study_step(later,
            minute_bars=[Bar(at+timedelta(hours=3), 1.099)],
            now=at+timedelta(hours=3,minutes=7))
        persisted = next(iter(still_missing["entry_factor_study"]["cases"].values()))
        self.assertEqual(persisted["status"], "DATA_GAP")
        self.assertIsNone(persisted["outcome"])
        tampered = copy.deepcopy(later)
        next(iter(tampered["entry_factor_study"]["cases"].values()))["frozen"]["reference_mid"] = 1.3
        with self.assertRaises(ValueError):
            policy.validate_state(tampered)

    def test_factor_probe_is_purged_by_three_hours_and_requires_source_provenance(self):
        at = datetime(2026, 11, 18, 9, 30, tzinfo=timezone.utc)
        state = policy.factor_study_step(
            policy.default_state(at), minute_bars=[Bar(at-timedelta(minutes=2),1.1)],
            now=at, decision_manifest=self._factor_probe_manifest(at),
            runtime_code_sha256="a"*64,
        )
        early = at+timedelta(hours=1)
        state = policy.factor_study_step(state,
            minute_bars=[Bar(early-timedelta(minutes=2),1.101)],
            now=early, decision_manifest=self._factor_probe_manifest(early),
            runtime_code_sha256="a"*64)
        self.assertEqual(len(state["entry_factor_study"]["cases"]), 1)
        after = at+timedelta(hours=3,minutes=10)
        late = policy.factor_study_step(state, minute_bars=[
            Bar(after-timedelta(minutes=8),1.102)],
            now=after, decision_manifest=self._factor_probe_manifest(after),
            runtime_code_sha256="a"*64)
        self.assertEqual(len(late["entry_factor_study"]["cases"]), 1)

    def test_native_admission_freezes_reflection_and_cannot_use_it_to_bypass_technical_guard(self):
        trade, legacy, closed_at = self.closed_native_loss()
        at = closed_at + timedelta(seconds=62)
        state = policy.default_state(closed_at)
        state = policy.reflect_on_history(state, {"trades": [legacy, trade]}, at,
                                         runtime_code_sha256="a" * 64)
        candidate = self.candidate(at, score=trade["entry_score"], confidence=.3936)
        with patch.object(runtime, "_cycle_context", {"policy_state": state, "decision_at": at}):
            result = self.admission_after_loss(candidate, {"trades": [legacy, trade]})
        learned = result["agent_reflection"]
        self.assertTrue(learned["available"])
        self.assertEqual(learned["recent_similar_loss_ids"], [trade["trade_id"]])
        self.assertFalse(result["allowed"])
        self.assertFalse(result["post_loss_technical_guard"]["allowed"])
        self.assertFalse(learned["may_override_direction_risk_or_safety"])


if __name__ == "__main__":
    unittest.main()
