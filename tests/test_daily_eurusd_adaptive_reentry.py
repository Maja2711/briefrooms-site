from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests import test_daily_eurusd_reentry_safety as fx

policy, safety, runtime = fx.policy, fx.safety, fx.runtime
learning = policy.reentry_learning


class LearnableCriteriaTests(unittest.TestCase):
    def test_initial_30_percent_is_a_starting_value_and_can_move_both_directions(self):
        for direction in ("LONG", "SHORT"):
            data = fx.fixture(direction, retracement=.299)
            payload, history, market, now = data
            self.assertFalse(safety.evaluate(payload, history, observed_at=now, market_context=market)["allowed"])
            criteria = safety.normalize_criteria()
            criteria["minimum_retracement"] = .25
            proof = safety.evaluate(payload, history, observed_at=now, market_context=market, criteria=criteria)
            self.assertTrue(proof["allowed"], proof)
            criteria["minimum_retracement"] = .40
            proof = safety.evaluate(payload, history, observed_at=now, market_context=market, criteria=criteria)
            self.assertFalse(proof["allowed"])

    def test_strength_threshold_can_be_learned_without_changing_native_direction(self):
        payload, history, market, now = fx.fixture()
        payload["confidence"] = .45
        payload["metadata"]["final_decision"]["confidence"] = .45
        self.assertFalse(safety.evaluate(payload, history, observed_at=now, market_context=market)["allowed"])
        criteria = safety.normalize_criteria()
        criteria["minimum_confidence"] = .40
        proof = safety.evaluate(payload, history, observed_at=now, market_context=market, criteria=criteria)
        self.assertTrue(proof["allowed"], proof)
        self.assertEqual(payload["direction"], "LONG")

    def test_support_definition_can_be_replaced_by_confirmed_price_rejection(self):
        payload, history, market, now = fx.fixture(period="no_ma")
        self.assertFalse(safety.evaluate(payload, history, observed_at=now, market_context=market)["allowed"])
        criteria = safety.normalize_criteria()
        criteria["support_mode"] = "PRICE_REJECTION"
        proof = safety.evaluate(payload, history, observed_at=now, market_context=market, criteria=criteria)
        self.assertTrue(proof["allowed"], proof)
        self.assertEqual(proof["support"]["kind"], "CONFIRMED_INTRADAY_REJECTION")

    def test_post_loss_source_requirement_can_be_changed_but_source_time_stays_real(self):
        payload, history, market, now = fx.fixture()
        anchor = payload["metadata"]["final_decision"]["used_beliefs"][0]
        anchor["observed_at"] = history["trades"][0]["closed_at"]
        self.assertFalse(safety.evaluate(payload, history, observed_at=now, market_context=market)["allowed"])
        criteria = safety.normalize_criteria()
        criteria["require_post_loss_trend"] = False
        proof = safety.evaluate(payload, history, observed_at=now, market_context=market, criteria=criteria)
        self.assertTrue(proof["allowed"], proof)
        anchor["observed_at"] = fx.stamp(now + timedelta(minutes=1))
        self.assertFalse(safety.evaluate(payload, history, observed_at=now, market_context=market, criteria=criteria)["allowed"])

    def test_wait_and_confirmation_interval_are_part_of_the_criteria(self):
        payload, history, market, now = fx.fixture()
        criteria = safety.normalize_criteria()
        criteria["minimum_wait_minutes"] = 30
        proof = safety.evaluate(payload, history, observed_at=now, market_context=market, criteria=criteria)
        self.assertEqual(proof["reason"], "post_loss_learned_wait_not_complete")
        proposals = dict(learning._proposals(safety.normalize_criteria()))
        self.assertIn("CONFIRMATION_1", proposals)
        self.assertIn("CONFIRMATION_15", proposals)

    def test_same_thesis_thresholds_are_also_learnable(self):
        guard = {"blocked": True, "reason": "recent_same_thesis_loss_without_material_new_evidence",
                 "change": {"directional_score_strength_improvement": 2, "confidence_improvement": .02}}
        criteria = safety.normalize_criteria()
        self.assertFalse(safety.thesis_allows(guard, criteria))
        criteria["material_score_improvement"] = 1
        self.assertTrue(safety.thesis_allows(guard, criteria))
        criteria["material_score_improvement"] = 3
        criteria["require_material_change"] = False
        self.assertTrue(safety.thesis_allows(guard, criteria))
        guard["reason"] = "rapid_opposite_reentry_requires_primary_or_fresh_external_evidence"
        self.assertFalse(safety.thesis_allows(guard, criteria))

    def test_invalid_parameters_cannot_disable_market_or_authority_checks(self):
        for key, value in (("minimum_retracement", -1), ("minimum_confidence", 2),
                           ("ma_periods", [0]), ("confirmation_minutes", True),
                           ("support_mode", "IGNORE_ALL_PRICE_DATA")):
            with self.subTest(key=key):
                criteria = safety.normalize_criteria()
                criteria[key] = value
                with self.assertRaises(ValueError):
                    safety.normalize_criteria(criteria)

    def test_epe_rechecks_the_exact_frozen_learned_parameters(self):
        proof = fx.evaluate(fx.fixture())
        proof["criteria"]["minimum_retracement"] = .25
        execution = {"fill_price": 1.10775, "verified_at": proof["confirmation"]["end"],
                     "selected_quote": {"timestamp": proof["confirmation"]["end"]}}
        self.assertEqual(safety.check_execution(proof, execution), "post_loss_execution_criteria_proof_invalid")


class AdaptiveReentryGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 20, tzinfo=timezone.utc)
        self.code = "a" * 64

    def initial(self):
        return learning.advance(None, now=self.start, code=self.code, memory={}, stats=policy._paired_metrics)

    def opportunity(self, state, index, *, at=None, active_vote=False, target="RETRACEMENT_LOWER"):
        at = at or self.start + timedelta(days=2 * index + 1)
        criteria = learning.contenders(state, at)
        active = learning.context(state, at)["version_id"]
        evaluations = {}
        for key, params in criteria.items():
            if key == active:
                vote = active_vote
            elif key in state["candidates"]:
                # Consistent broad qualifying opportunity for budget tests;
                # after a relaxed promotion the parent's correction can fail.
                if active_vote:
                    ceiling = criteria[active]["minimum_retracement"] + .01 if state["versions"][active]["parent_version_id"] else .60
                    vote = params["minimum_retracement"] <= ceiling
                else:
                    vote = state["candidates"][key]["metadata"]["axis"] == target
            else:
                vote = False
            proof = {"required": True, "prior_trade_id": "loss-" + str(index), "allowed": bool(vote),
                     "criteria": copy.deepcopy(params), "criteria_sha256": safety._digest(params)}
            evaluations[key] = {"technical": proof, "thesis_allowed": True, "execution_allowed": True, "allowed": bool(vote)}
        manifest = {
            "schema_version": "eurusd-production-decision-manifest-v1", "instrument": "EUR/USD",
            "decision_id": "adaptive-decision-" + str(index), "decision_at": fx.stamp(at),
            "runtime_code_sha256": state["runtime_code_sha256"],
            "execution_price_engine": {"verified": True, "status": "VERIFIED_FILL",
                                       "execution_cost_contract": "DAILY_EURUSD_BID_ASK_V1"},
            "final_decision": {"direction": "LONG", "score": 62},
            "execution_geometry": {"entry": 1.1, "stop": 1.09, "target": 1.118},
        }
        manifest["manifest_sha256"] = policy.sha256(manifest)
        position = {"trade_id": "adaptive-trade-" + str(index), "direction": "LONG",
                    "opened_at": fx.stamp(at), "entry": 1.1, "stop": 1.09, "target": 1.118,
                    "decision_manifest": manifest}
        state, cid = learning.freeze(state, manifest=manifest, position=position, evaluations=evaluations,
                                     canonical_admitted=active_vote, now=at + timedelta(seconds=1))
        return state, cid, position, at

    def trade(self, position, at, value=1):
        return {"trade_id": position["trade_id"], "direction": "LONG", "entry": position["entry"],
                "exit_price": position["entry"] + value * .01, "closed_at": fx.stamp(at + timedelta(hours=1)),
                "decision_manifest": position["decision_manifest"], "r_multiple": -9876}

    def resolved(self, state, index, *, active_vote=False, value=1, at=None, target="RETRACEMENT_LOWER"):
        state, cid, position, at = self.opportunity(state, index, at=at, active_vote=active_vote, target=target)
        trade = self.trade(position, at, value)
        history = {"trades": [trade]} if active_vote else {"trades": []}
        state = learning.settle(state, history, now=at + timedelta(hours=2),
                                evaluator=lambda *_: {"coverage_complete": True, "closed_trade": trade})
        return state, cid, at

    def mature(self):
        state = self.initial()
        for index in range(learning.PRIMARY_N):
            state, _, at = self.resolved(state, index)
        state = learning.advance(state, now=at + timedelta(hours=3), code=self.code, memory={}, stats=policy._paired_metrics)
        return state, at

    def test_initial_factory_covers_loosening_tightening_support_and_strength(self):
        state = self.initial()
        axes = {row["metadata"]["axis"] for row in state["candidates"].values()}
        self.assertEqual(len(axes), 6)
        self.assertTrue({"RETRACEMENT_LOWER", "RETRACEMENT_HIGHER", "SUPPORT_DEFINITION", "SIGNAL_LOWER", "SIGNAL_HIGHER"} <= axes)
        self.assertEqual(learning.context(state, self.start)["criteria"]["minimum_retracement"], .30)

    def test_counterfactual_is_frozen_before_the_path_and_never_written_to_history(self):
        state, cid, position, at = self.opportunity(self.initial(), 0)
        frozen = copy.deepcopy(state["cases"][cid]["frozen"])
        self.assertIsNone(state["cases"][cid]["outcome"])
        history = {"trades": []}
        state = learning.settle(state, history, now=at + timedelta(hours=2),
                                evaluator=lambda *_: {"coverage_complete": True, "closed_trade": self.trade(position, at)})
        outcome = state["cases"][cid]["outcome"]
        self.assertEqual(outcome["source"], "PROSPECTIVE_COUNTERFACTUAL")
        self.assertEqual(history, {"trades": []})
        self.assertEqual(state["cases"][cid]["frozen"], frozen)
        self.assertFalse(outcome["broker_fill_claim"])

    def test_actual_case_waits_for_canonical_close_and_never_uses_virtual_profit(self):
        state, cid, position, at = self.opportunity(self.initial(), 0, active_vote=True)
        state = learning.settle(state, {"trades": []}, now=at + timedelta(hours=2),
                                evaluator=lambda *_: self.fail("actual must not use a virtual outcome"))
        self.assertIsNone(state["cases"][cid]["outcome"])
        trade = self.trade(position, at, -.4)
        state = learning.settle(state, {"trades": [trade]}, now=at + timedelta(hours=2))
        self.assertEqual(state["cases"][cid]["outcome"]["source"], "CANONICAL_ACTUAL")
        self.assertAlmostEqual(state["cases"][cid]["outcome"]["net_r"], -.4)
        learning.validate_history(state, {"trades": [trade]})

    def test_refreshes_of_the_same_loss_are_not_independent_samples(self):
        state, cid, position, at = self.opportunity(self.initial(), 0)
        again, repeated, _, _ = self.opportunity(state, 0, at=at + timedelta(minutes=5))
        self.assertIsNone(repeated)
        self.assertEqual(len(again["cases"]), 1)
        self.assertEqual(again["active_case_id"], cid)

    def test_27_hour_purge_holds_even_when_prior_position_closed(self):
        state, _, at = self.resolved(self.initial(), 0)
        state, cid, _, _ = self.opportunity(state, 1, at=at + timedelta(hours=26))
        self.assertIsNone(cid)
        self.assertEqual(len(state["cases"]), 1)

    def test_no_policy_change_from_three_successful_counterfactuals(self):
        state = self.initial()
        for index in range(3):
            state, _, at = self.resolved(state, index)
        state = learning.advance(state, now=at + timedelta(hours=3), code=self.code, memory={}, stats=policy._paired_metrics)
        self.assertEqual(learning.context(state, at + timedelta(hours=4))["criteria"]["minimum_retracement"], .30)
        self.assertTrue(all(row["gate"]["status"] == "COLLECTING" for row in state["candidates"].values()))

    def test_measured_future_evidence_can_promote_a_less_restrictive_25_percent_policy(self):
        state, at = self.mature()
        current = learning.context(state, at + timedelta(hours=4))
        self.assertEqual(current["criteria"]["minimum_retracement"], .25)
        version = state["versions"][current["version_id"]]
        gate = state["candidates"][version["candidate_id"]]["gate"]
        self.assertEqual(gate["observed_sample"], 40)
        self.assertEqual(gate["metrics"]["counterfactual_outcomes"], 40)
        self.assertGreater(gate["metrics"]["block_ci_low_r"], 0)
        self.assertEqual(learning.context(state, at)["criteria"]["minimum_retracement"], .30)
        learning.validate(state, policy._paired_metrics)

    def test_losses_on_relaxed_entries_reject_the_candidate(self):
        state = self.initial()
        for index in range(learning.PRIMARY_N):
            state, _, at = self.resolved(state, index, value=-.4)
        state = learning.advance(state, now=at + timedelta(hours=3), code=self.code, memory={}, stats=policy._paired_metrics)
        self.assertEqual(learning.context(state, at + timedelta(hours=4))["criteria"]["minimum_retracement"], .30)
        lower = next(row for row in state["candidates"].values() if row["metadata"]["axis"] == "RETRACEMENT_LOWER")
        self.assertEqual(lower["status"], "REJECTED")
        self.assertEqual(lower["gate"]["status"], "FAIL")

    def test_missing_forward_data_is_neither_a_zero_nor_a_win(self):
        state, cid, _, at = self.opportunity(self.initial(), 0)
        state = learning.settle(state, {"trades": []}, now=at + timedelta(hours=2),
                                evaluator=lambda *_: {"coverage_complete": False})
        self.assertEqual(state["cases"][cid]["status"], "DATA_INCOMPLETE")
        self.assertIsNone(state["cases"][cid]["outcome"])

    def test_runtime_change_censors_virtual_path_without_fabricating_a_result(self):
        state, cid, _, at = self.opportunity(self.initial(), 0)
        state = learning.advance(state, now=at + timedelta(minutes=2), code="b" * 64, memory={}, stats=policy._paired_metrics)
        self.assertEqual(state["cases"][cid]["status"], "CENSORED")
        self.assertIsNone(state["cases"][cid]["outcome"])
        self.assertIsNone(state["active_case_id"])

    def test_serialization_cannot_reorder_evidence_into_a_time_regression(self):
        state = self.initial()
        for index in range(4):
            state, _, _ = self.resolved(state, index)
        state = json.loads(json.dumps(state, sort_keys=True))
        learning.validate(state, policy._paired_metrics)
        state, cid, _, _ = self.opportunity(state, 5)
        self.assertIsNotNone(cid)

    def test_promotion_is_rolled_back_when_future_actual_results_degrade(self):
        state, at = self.mature()
        for index in range(learning.MONITOR_N):
            state, _, when = self.resolved(state, 100 + index, active_vote=True, value=-.4,
                                          at=at + timedelta(days=2 * index + 2), target="NO_OTHER_ARM")
        state = learning.advance(state, now=when + timedelta(hours=3), code=self.code, memory={}, stats=policy._paired_metrics)
        self.assertEqual(learning.context(state, when + timedelta(hours=4))["criteria"]["minimum_retracement"], .30)
        self.assertEqual(len(state["rollback_events"]), 1)
        learning.validate(state, policy._paired_metrics)

    def test_frozen_case_and_closed_result_cannot_be_rewritten(self):
        before, cid, _ = self.resolved(self.initial(), 0)
        after = copy.deepcopy(before)
        after["cases"][cid]["outcome"]["net_r"] = 100
        with self.assertRaisesRegex(ValueError, "closed evidence rewritten"):
            learning.validate_transition(before, after)
        after = copy.deepcopy(before)
        after["cases"][cid]["frozen"]["loss_id"] = "different-loss"
        with self.assertRaisesRegex(ValueError, "frozen opportunity rewritten"):
            learning.validate_transition(before, after)
        with self.assertRaisesRegex(ValueError, "learning epoch removed"):
            learning.validate_transition(before, None)

    def test_candidate_cannot_select_a_later_known_outcome_instead_of_its_first_case(self):
        state = self.initial()
        for index in range(3):
            state, _, _ = self.resolved(state, index)
        changed = copy.deepcopy(state)
        row = next(iter(changed["candidates"].values()))
        row["evaluation_case_ids"] = row["evaluation_case_ids"][1:]
        with self.assertRaisesRegex(ValueError, "first frozen eligible"):
            learning.validate(changed, policy._paired_metrics)

    def test_failed_generations_cannot_reset_the_18_test_budget(self):
        state = self.initial()
        for generation in range(3):
            for index in range(learning.PRIMARY_N):
                state, _, at = self.resolved(state, generation * 100 + index, active_vote=True,
                                             at=self.start + timedelta(days=100 * generation + 2 * index + 1),
                                             value=1, target="NO_OTHER_ARM")
            state = learning.advance(state, now=at + timedelta(hours=3), code=self.code,
                                     memory={}, stats=policy._paired_metrics)
        state = learning.advance(state, now=at + timedelta(days=10), code=self.code,
                                 memory={}, stats=policy._paired_metrics)
        self.assertEqual(len(state["candidates"]), learning.MAX_TESTS)
        self.assertFalse(any(row["status"] == "OOS_RUNNING" for row in state["candidates"].values()))
        learning.validate(state, policy._paired_metrics)

    def test_preboundary_or_unevaluated_policy_cannot_authorize_promotion(self):
        state, _ = self.mature()
        changed = copy.deepcopy(state)
        version = changed["versions"][changed["active_version_id"]]
        version["criteria"]["minimum_retracement"] = .10
        version["payload_sha256"] = policy.sha256(version["criteria"])
        with self.assertRaisesRegex(ValueError, "unevaluated production policy"):
            learning.validate(changed, policy._paired_metrics)


class AdaptiveReentryRuntimeTests(unittest.TestCase):
    def state(self, now):
        return policy.advance_governance(policy.default_state(now - timedelta(days=1)),
                                        now - timedelta(minutes=1), runtime_code_sha256=runtime.decision_record.runtime_code_hash())

    def prepared_quote(self, output, data):
        now, direction = data[3], output.direction
        sign = 1 if direction == "LONG" else -1
        fill = data[2]["minute_bars"][-1]["close"] + sign * .00015
        execution = {"verified": True, "status": "VERIFIED_FILL", "fill_price": fill,
                     "verified_at": fx.stamp(now), "selected_quote": {"timestamp": fx.stamp(now)},
                     "execution_cost_contract": "DAILY_EURUSD_BID_ASK_V1",
                     "synthetic_half_spread_pips": 1.5, "synthetic_spread_pips": 3}
        return runtime._clone(output, entry=fill, stop=fill - sign * .003, target=fill + sign * .0054,
                              metadata={**output.metadata, "execution_price_engine": execution})

    def test_rejected_29_9_percent_opportunity_is_observed_prospectively_but_remains_flat(self):
        data = fx.fixture(retracement=.299)
        output = fx.candidate(data[0], data[3])
        state = self.state(data[3])
        context = {"policy_state": state, "decision_at": data[3]}
        with patch.object(runtime, "_cycle_context", context), patch.object(
                fx.belief, "calendar_safety", return_value={"blocked": False}):
            admission = runtime._execution_admission(output, data[1], observed_at=data[3], market_context=data[2])
            self.assertFalse(admission["allowed"])
            self.assertTrue(admission["reentry_research_eligible"])
            output = runtime._clone(output, metadata={**output.metadata, "execution_admission": admission})
            prepared = self.prepared_quote(output, data)
            with patch.object(runtime, "_epe_prepare", return_value=prepared):
                result = runtime.prepare_entry_candidate(output, [], data[3])
        self.assertEqual(result.direction, "FLAT")
        registry = context["policy_state"]["reentry_learning"]
        self.assertEqual(len(registry["cases"]), 1)
        case = next(iter(registry["cases"].values()))
        self.assertFalse(case["frozen"]["canonical_admitted"])
        self.assertIsNone(case["outcome"])
        runtime.decision_record.validate_manifest(case["frozen"]["manifest"])
        self.assertEqual(result.metadata["decision_manifest"], case["frozen"]["manifest"])

    def test_missing_two_source_epe_proof_cannot_create_a_research_case(self):
        data = fx.fixture(retracement=.299)
        output = fx.candidate(data[0], data[3])
        state = self.state(data[3])
        context = {"policy_state": state, "decision_at": data[3]}
        with patch.object(runtime, "_cycle_context", context), patch.object(
                fx.belief, "calendar_safety", return_value={"blocked": False}):
            admission = runtime._execution_admission(output, data[1], observed_at=data[3], market_context=data[2])
            output = runtime._clone(output, metadata={**output.metadata, "execution_admission": admission})
            with patch.object(runtime, "_epe_prepare", return_value=runtime._clone(output, direction="FLAT", entry=None, stop=None, target=None)):
                result = runtime.prepare_entry_candidate(output, [], data[3])
        self.assertEqual(result.direction, "FLAT")
        self.assertFalse(context["policy_state"]["reentry_learning"]["cases"])

    def test_calendar_block_prevents_research_epe_and_position_creation(self):
        data = fx.fixture(retracement=.299)
        output = fx.candidate(data[0], data[3])
        context = {"policy_state": self.state(data[3]), "decision_at": data[3]}
        with patch.object(runtime, "_cycle_context", context), patch.object(
                fx.belief, "calendar_safety", return_value={"blocked": True, "reason": "official_release"}):
            admission = runtime._execution_admission(output, data[1], observed_at=data[3], market_context=data[2])
            output = runtime._clone(output, metadata={**output.metadata, "execution_admission": admission})
            with patch.object(runtime, "_epe_prepare") as epe:
                result = runtime.prepare_entry_candidate(output, [], data[3])
                epe.assert_not_called()
        self.assertEqual(result.direction, "FLAT")

    def test_manifest_cannot_claim_different_reentry_parameters_than_governed_version(self):
        now = datetime(2026, 10, 20, 10, 15, tzinfo=timezone.utc)
        state = self.state(now)
        context = policy.policy_context(state, 62, "LONG", observed_at=now)
        manifest = {"decision_at": fx.stamp(now), "policy": context}
        policy.validate_reentry_manifest(state, manifest)
        changed = copy.deepcopy(manifest)
        changed["policy"]["reentry_policy"]["criteria"]["minimum_retracement"] = .01
        with self.assertRaisesRegex(ValueError, "governed policy"):
            policy.validate_reentry_manifest(state, changed)


if __name__ == "__main__":
    unittest.main()
