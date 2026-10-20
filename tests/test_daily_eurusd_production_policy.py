from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone

from scripts import daily_eurusd_production_policy as policy


class ProductionAdmissionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 20, tzinfo=timezone.utc)

    def opportunity(self, state, index=0, *, at=None, score=53.5, code_hash="a" * 64):
        at = at or self.start + timedelta(days=2 * index + 1)
        context = policy.policy_context(state, score, "LONG", observed_at=at)
        manifest = {
            "schema_version": policy.MANIFEST_SCHEMA,
            "decision_id": f"decision-{index}", "decision_at": policy._iso(at),
            "engine_version": policy.ENGINE_VERSION,
            "policy_version_id": context["version_id"], "policy_sha256": context["policy_sha256"],
            "runtime_code_sha256": code_hash, "decision_sha256": f"decision-sha-{index}",
            "final_decision": {"direction": "LONG", "score": score},
            "execution_admission": {"allowed": True},
            "execution_price_engine": {"status": "VERIFIED_FILL", "execution_cost_contract": "DAILY_EURUSD_BID_ASK_V1"},
            "risk": {"risk_plan_version": "v1"}, "market_snapshot_sha256": f"market-{index}",
        }
        manifest["manifest_sha256"] = policy.sha256(manifest)
        position = {
            "trade_id": f"trade-{index}", "status": "OPEN", "opened_at": policy._iso(at),
            "direction": "LONG", "entry": 1.1, "stop": 1.09, "target": 1.118,
            "decision_manifest": manifest,
        }
        return at, manifest, position

    def closed(self, position, at, result_r=-1.0):
        return {
            "trade_id": position["trade_id"], "direction": position["direction"],
            "entry": position["entry"], "exit_price": position["entry"] + result_r * 0.01,
            "closed_at": policy._iso(at + timedelta(hours=20)), "exit_reason": "TEST",
            "decision_manifest": copy.deepcopy(position["decision_manifest"]),
            # Independent calculation must ignore this deliberately wrong field.
            "r_multiple": 9876.0,
        }

    def actual_case(self, state, index, *, result_r=-1.0, at=None, score=53.5, code_hash="a" * 64):
        at, manifest, position = self.opportunity(state, index, at=at, score=score, code_hash=code_hash)
        state, cid = policy.freeze_case(state, decision_manifest=manifest, position=position, score=score, now=at + timedelta(seconds=1), slices={"session": "LONDON", "macro": "CLEAR"})
        state = policy.bind_actual_history(state, {"trades": [self.closed(position, at, result_r)]}, now=at + timedelta(hours=21))
        return state, cid, at

    def mature(self):
        state = policy.default_state(self.start)
        for index in range(policy.PRIMARY_N):
            state, _, at = self.actual_case(state, index)
        state = policy.advance_governance(state, at + timedelta(hours=22))
        return state, at + timedelta(hours=22)

    def test_baseline_preserves_directional_band_and_never_creates_direction(self):
        state = policy.default_state(self.start)
        self.assertTrue(policy.policy_context(state, 53.0, "LONG")["allowed"])
        self.assertTrue(policy.policy_context(state, 47.0, "SHORT")["allowed"])
        self.assertFalse(policy.policy_context(state, 52.9, "LONG")["allowed"])
        self.assertFalse(policy.policy_context(state, 90.0, "FLAT")["allowed"])
        self.assertFalse(policy.policy_context(state, 10.0, "LONG")["allowed"])
        policy.validate_state(state)

    def test_single_evaluation_position_prevents_duplicate_or_overlapping_cases(self):
        state = policy.default_state(self.start)
        at, manifest, position = self.opportunity(state)
        state, first = policy.freeze_case(state, decision_manifest=manifest, position=position, score=53.5, now=at + timedelta(seconds=1))
        repeated, second = policy.freeze_case(state, decision_manifest=manifest, position=position, score=53.5, now=at + timedelta(seconds=2))
        self.assertIsNone(second)
        self.assertEqual(state, repeated)
        self.assertEqual(first, state["active_case_id"])

    def test_actual_result_requires_exact_manifest_and_recomputes_net_r(self):
        state = policy.default_state(self.start)
        at, manifest, position = self.opportunity(state)
        state, cid = policy.freeze_case(state, decision_manifest=manifest, position=position, score=53.5, now=at + timedelta(seconds=1))
        wrong = self.closed(position, at)
        wrong["decision_manifest"]["decision_id"] = "other"
        with self.assertRaisesRegex(ValueError, "manifest hash"):
            policy.bind_actual_history(state, {"trades": [wrong]}, at + timedelta(hours=21))
        bound = policy.bind_actual_history(state, {"trades": [self.closed(position, at)]}, at + timedelta(hours=21))
        self.assertAlmostEqual(bound["cases"][cid]["outcome"]["net_r"], -1.0)
        self.assertEqual(bound["cases"][cid]["outcome"]["source"], "CANONICAL_ACTUAL")
        policy.validate_transition(state, bound)
        policy.validate_history_bindings(bound, {"trades": [self.closed(position, at)]})
        with self.assertRaisesRegex(ValueError, "absent"):
            policy.validate_history_bindings(bound, {"trades": []})

    def test_virtual_close_never_substitutes_for_actual_canonical_result(self):
        state = policy.default_state(self.start)
        at, manifest, position = self.opportunity(state)
        state, cid = policy.freeze_case(state, decision_manifest=manifest, position=position, score=53.5, now=at + timedelta(seconds=1))
        settled = policy.settle_cases(state, evaluator=lambda *_: {"coverage_complete": True, "closed_trade": self.closed(position, at)}, now=at + timedelta(hours=21))
        self.assertIsNone(settled["cases"][cid]["outcome"])
        self.assertEqual(settled["active_case_id"], cid)

    def test_no_peeking_before_frozen_complete_sample(self):
        state = policy.default_state(self.start)
        for index in range(7):
            state, _, at = self.actual_case(state, index)
        advanced = policy.advance_governance(state, at + timedelta(hours=22))
        candidate = next(iter(advanced["candidates"].values()))
        self.assertEqual(candidate["gate"]["status"], "COLLECTING")
        self.assertEqual(candidate["gate"]["metrics"], {"selected_n": 7, "resolved_n": 7})
        self.assertEqual(policy.policy_context(advanced, 53.5, "LONG")["minimum_score_distance"], 3)

    def test_27_hour_purge_is_chosen_before_outcome(self):
        state = policy.default_state(self.start)
        state, first, _ = self.actual_case(state, 0, at=self.start + timedelta(hours=1))
        state, second, _ = self.actual_case(state, 1, at=self.start + timedelta(hours=25))
        ids = next(iter(state["candidates"].values()))["evaluation_case_ids"]
        self.assertEqual(ids, [first])
        self.assertIn(second, state["cases"])
        policy.validate_state(state)

    def test_promotion_uses_exact_evaluated_hash_and_future_activation(self):
        state, at = self.mature()
        current = state["versions"][state["active_version_id"]]
        self.assertEqual(current["policy"]["minimum_score_distance"], 4)
        candidate = state["candidates"][current["candidate_id"]]
        self.assertEqual(current["payload_sha256"], candidate["challenger_version"])
        self.assertEqual(candidate["gate"]["metrics"]["actual_outcomes"], 40)
        self.assertEqual(candidate["gate"]["metrics"]["counterfactual_outcomes"], 0)
        self.assertGreater(candidate["gate"]["metrics"]["block_ci_low_r"], 0)
        self.assertFalse(policy.policy_context(state, 53.5, "LONG", observed_at=at + timedelta(seconds=1))["allowed"])
        self.assertTrue(policy.policy_context(state, 53.5, "LONG", observed_at=at - timedelta(seconds=1))["allowed"])
        policy.validate_state(state)

    def test_losing_challenger_rejected_once_without_reusing_later_winners(self):
        state = policy.default_state(self.start)
        for index in range(policy.PRIMARY_N):
            state, _, at = self.actual_case(state, index, result_r=1)
        advanced = policy.advance_governance(state, at + timedelta(hours=22))
        candidate = next(iter(advanced["candidates"].values()))
        self.assertEqual(candidate["status"], "REJECTED")
        self.assertEqual(candidate["gate"]["status"], "FAIL")
        again = policy.advance_governance(advanced, at + timedelta(days=1))
        self.assertEqual(candidate["gate"], again["candidates"][candidate["candidate_id"]]["gate"])

    def test_current_missing_coverage_blocks_otherwise_passing_promotion(self):
        state = policy.default_state(self.start)
        for index in range(policy.PRIMARY_N):
            state, _, at = self.actual_case(state, index)
        next_at, manifest, position = self.opportunity(state, 100, at=at + timedelta(days=2))
        state, _ = policy.freeze_case(state, decision_manifest=manifest, position=position, score=53.5, now=next_at + timedelta(seconds=1))
        state = policy.settle_cases(state, evaluator=lambda *_: {"coverage_complete": False}, now=next_at + timedelta(minutes=1))
        state = policy.advance_governance(state, next_at + timedelta(minutes=2))
        candidate = next(iter(state["candidates"].values()))
        self.assertEqual(candidate["gate"]["status"], "HOLD")
        self.assertEqual(policy.policy_context(state, 53.5, "LONG")["minimum_score_distance"], 3)

    def test_positive_confidence_and_better_pf_cannot_override_worse_drawdown(self):
        state = policy.default_state(self.start)
        for index in range(policy.PRIMARY_N):
            if index in (0, 2):
                score, value = 60, -1
            elif index == 1:
                score, value = 53.5, 1
            elif index % 2 == 0:
                score, value = 53.5, -1
            else:
                score, value = 60, 1.8
            state, _, at = self.actual_case(state, index, result_r=value, score=score)
        state = policy.advance_governance(state, at + timedelta(hours=22))
        gate = next(iter(state["candidates"].values()))["gate"]
        self.assertGreater(gate["metrics"]["block_ci_low_r"], 0)
        self.assertGreater(gate["metrics"]["challenger_profit_factor"]["value"], gate["metrics"]["champion_profit_factor"]["value"])
        self.assertAlmostEqual(gate["metrics"]["champion_max_drawdown_r"], 1)
        self.assertAlmostEqual(gate["metrics"]["challenger_max_drawdown_r"], 2)
        self.assertEqual(gate["status"], "FAIL")
        self.assertIn("drawdown_degradation", gate["blockers"])

    def test_fabricated_passing_gate_without_actual_records_is_rejected(self):
        state = policy.default_state(self.start)
        candidate = next(iter(state["candidates"].values()))
        candidate["gate"] = {"status": "PASS", "evaluated_at": policy._iso(self.start), "observed_sample": 40}
        with self.assertRaisesRegex(ValueError, "reconcile"):
            policy.validate_state(state)

    def test_frozen_case_candidate_and_closed_outcome_cannot_be_rewritten(self):
        state = policy.default_state(self.start)
        state, cid, _ = self.actual_case(state, 0)
        changed = copy.deepcopy(state)
        changed["cases"][cid]["outcome"]["net_r"] = 2
        with self.assertRaises(ValueError):
            policy.validate_transition(state, changed)
        changed = copy.deepcopy(state)
        candidate = next(iter(changed["candidates"].values()))
        candidate["proposed_change"]["minimum_score_distance"] = 6
        with self.assertRaisesRegex(ValueError, "candidate mutated"):
            policy.validate_state(changed)

    def test_runtime_code_change_parks_candidate_and_keeps_cohort_immutable(self):
        state = policy.default_state(self.start)
        state, _, _ = self.actual_case(state, 0)
        original = copy.deepcopy(next(iter(state["candidates"].values())))
        state, _, at = self.actual_case(state, 1, code_hash="b" * 64)
        candidate = state["candidates"][original["candidate_id"]]
        self.assertEqual(candidate["status"], "PARKED")
        self.assertEqual(candidate["evaluation_case_ids"], original["evaluation_case_ids"])
        self.assertEqual(candidate["runtime_binding"], original["runtime_binding"])
        policy.validate_state(state)

    def test_rejected_monitoring_opportunity_is_explicit_counterfactual(self):
        state, promoted = self.mature()
        at, manifest, position = self.opportunity(state, 100, at=promoted + timedelta(days=2))
        state, cid = policy.freeze_case(state, decision_manifest=manifest, position=position, score=53.5, now=at + timedelta(seconds=1))
        self.assertEqual(state["cases"][cid]["mode"], "PROSPECTIVE_COUNTERFACTUAL")
        settled = policy.settle_cases(state, evaluator=lambda *_: {"coverage_complete": True, "closed_trade": self.closed(position, at, 1)}, now=at + timedelta(hours=21))
        outcome = settled["cases"][cid]["outcome"]
        self.assertEqual(outcome["source"], "PROSPECTIVE_COUNTERFACTUAL")
        self.assertFalse(outcome["live_executed"])
        self.assertFalse(outcome["execution_authority"])
        policy.validate_state(settled)

    def test_runtime_change_rolls_back_before_any_new_case_or_admission(self):
        state, promoted = self.mature()
        old_active = state["active_version_id"]
        state = policy.advance_governance(state, promoted + timedelta(minutes=1), runtime_code_sha256="b" * 64)
        self.assertNotEqual(state["active_version_id"], old_active)
        self.assertEqual(state["rollback_events"][-1]["reason"], "INVARIANT_FAILURE")
        self.assertEqual(state["rollback_events"][-1]["metrics"]["reason"], "runtime_code_semantics_changed_before_admission")
        self.assertIsNone(state["active_case_id"])

    def test_code_epoch_censors_only_unresolved_counterfactual_and_releases_slot(self):
        state, promoted = self.mature()
        at, manifest, position = self.opportunity(state, 100, at=promoted + timedelta(days=2))
        state, cid = policy.freeze_case(state, decision_manifest=manifest, position=position, score=53.5, now=at + timedelta(seconds=1))
        frozen_before = copy.deepcopy(state["cases"][cid]["frozen"])
        state = policy.advance_governance(state, at + timedelta(minutes=1), runtime_code_sha256="b" * 64)
        case = state["cases"][cid]
        self.assertEqual(case["status"], "CENSORED")
        self.assertIsNone(case["outcome"])
        self.assertIsNone(state["active_case_id"])
        self.assertEqual(case["frozen"], frozen_before)
        self.assertTrue(case["censoring"]["excluded_from_evaluation"])
        policy.validate_state(state)
        next_at, next_manifest, next_position = self.opportunity(state, 101, at=at + timedelta(days=2), code_hash="b" * 64)
        state, next_cid = policy.freeze_case(state, decision_manifest=next_manifest, position=next_position, score=53.5, now=next_at + timedelta(seconds=1))
        self.assertEqual(state["active_case_id"], next_cid)
        self.assertIsNone(state["cases"][cid]["outcome"])
        policy.validate_state(state)

    def test_code_epoch_keeps_canonical_case_available_for_real_history_binding(self):
        state = policy.default_state(self.start)
        at, manifest, position = self.opportunity(state)
        state, cid = policy.freeze_case(state, decision_manifest=manifest, position=position, score=53.5, now=at + timedelta(seconds=1))
        state = policy.advance_governance(state, at + timedelta(minutes=1), runtime_code_sha256="b" * 64)
        self.assertEqual(state["active_case_id"], cid)
        self.assertNotEqual(state["cases"][cid]["status"], "CENSORED")
        state = policy.bind_actual_history(state, {"trades": [self.closed(position, at)]}, at + timedelta(hours=21))
        self.assertEqual(state["cases"][cid]["outcome"]["source"], "CANONICAL_ACTUAL")
        policy.validate_state(state)

    def test_code_epoch_skips_all_obsolete_promoted_ancestors_before_admission(self):
        state, promoted = self.mature()
        state = policy.advance_governance(state, promoted + timedelta(days=31))
        for index in range(policy.PRIMARY_N):
            state, _, at = self.actual_case(state, 100 + index,
                at=promoted + timedelta(days=2 * index + 32), score=54.5)
        state = policy.advance_governance(state, at + timedelta(hours=22))
        self.assertEqual(policy.policy_context(state, 54.5, "LONG")["minimum_score_distance"], 5)
        state = policy.advance_governance(state, at + timedelta(days=1), runtime_code_sha256="b" * 64)
        self.assertEqual(policy.policy_context(state, 53.5, "LONG")["minimum_score_distance"], 3)
        self.assertEqual(len(state["rollback_events"]), 2)

    def test_postpromotion_fixed_paired_window_can_rollback(self):
        state, promoted = self.mature()
        promoted_id = state["active_version_id"]
        parent_id = state["versions"][promoted_id]["parent_version_id"]
        for index in range(policy.MONITOR_N):
            at, manifest, position = self.opportunity(state, 100 + index, at=promoted + timedelta(days=2 * index + 2))
            state, _ = policy.freeze_case(state, decision_manifest=manifest, position=position, score=53.5, now=at + timedelta(seconds=1), slices={"session": "LONDON"})
            state = policy.settle_cases(state, evaluator=lambda *_: {"coverage_complete": True, "closed_trade": self.closed(position, at, 1)}, now=at + timedelta(hours=21))
        state = policy.advance_governance(state, at + timedelta(hours=22))
        self.assertEqual(state["active_version_id"], parent_id)
        self.assertEqual(state["versions"][promoted_id]["status"], "ROLLED_BACK")
        metrics = state["rollback_events"][-1]["metrics"]
        self.assertEqual(metrics["counterfactual_outcomes"], 20)

    def monitoring_window(self, *, outcome_r):
        state, promoted = self.mature()
        for index in range(policy.MONITOR_N):
            at, manifest, position = self.opportunity(state, 100 + index, at=promoted + timedelta(days=2 * index + 2))
            state, _ = policy.freeze_case(state, decision_manifest=manifest, position=position, score=53.5, now=at + timedelta(seconds=1))
            state = policy.settle_cases(state, evaluator=lambda *_: {"coverage_complete": True,
                "closed_trade": self.closed(position, at, outcome_r)}, now=at + timedelta(hours=21))
        return state, at

    def test_monitor_sample_cannot_be_removed_after_outcomes_to_bypass_rollback(self):
        state, at = self.monitoring_window(outcome_r=1)
        changed = copy.deepcopy(state)
        changed["versions"][changed["active_version_id"]]["monitor_case_ids"] = []
        with self.assertRaisesRegex(ValueError, "monitor sample history rewrite"):
            policy.validate_transition(state, changed)
        legitimate = policy.advance_governance(state, at + timedelta(hours=22))
        self.assertEqual(policy.policy_context(legitimate, 53.5, "LONG")["minimum_score_distance"], 3)

    def test_monitor_review_is_recomputed_and_cannot_skip_a_window(self):
        state, at = self.monitoring_window(outcome_r=-1)
        state = policy.advance_governance(state, at + timedelta(hours=22))
        active = state["active_version_id"]
        self.assertEqual(len(state["versions"][active]["monitor_reviews"]), 1)
        changed = copy.deepcopy(state)
        changed["versions"][active]["monitor_reviews"][0]["metrics"]["stressed_mean_delta_r"] = 123
        with self.assertRaisesRegex(ValueError, "independently reconciles"):
            policy.validate_state(changed)
        changed = copy.deepcopy(state)
        changed["versions"][active]["monitor_reviews"].append(copy.deepcopy(changed["versions"][active]["monitor_reviews"][0]))
        with self.assertRaisesRegex(ValueError, "fixed selected window"):
            policy.validate_state(changed)

    def test_monitor_evidence_cannot_be_reordered_or_moved_to_another_version(self):
        state, _ = self.monitoring_window(outcome_r=1)
        changed = copy.deepcopy(state)
        ids = changed["versions"][changed["active_version_id"]]["monitor_case_ids"]
        ids[0], ids[1] = ids[1], ids[0]
        with self.assertRaisesRegex(ValueError, "order changed"):
            policy.validate_state(changed)
        changed = copy.deepcopy(state)
        baseline = next(v for v in changed["versions"].values() if not v["parent_version_id"])
        baseline["monitor_case_ids"] = list(changed["versions"][changed["active_version_id"]]["monitor_case_ids"])
        with self.assertRaisesRegex(ValueError, "baseline cannot"):
            policy.validate_state(changed)

    def test_rolled_back_production_version_cannot_be_reactivated_by_old_promotion_replay(self):
        state, at = self.monitoring_window(outcome_r=1)
        state = policy.advance_governance(state, at + timedelta(hours=22))
        changed = copy.deepcopy(state)
        event = copy.deepcopy(next(e for e in changed["events"] if e["event"] == "PROMOTION"))
        event["at"] = policy._iso(at + timedelta(days=2))
        changed["events"].append(event)
        changed["versions"][changed["active_version_id"]]["status"] = "SUPERSEDED"
        changed["active_version_id"] = event["version_id"]
        changed["versions"][event["version_id"]]["status"] = "ACTIVE"
        changed["updated_at"] = event["at"]
        with self.assertRaisesRegex(ValueError, "version promotion replay"):
            policy.validate_transition(state, changed)

    def test_old_candidate_gate_cannot_authorize_a_new_version_identity_after_rollback(self):
        state, at = self.monitoring_window(outcome_r=1)
        state = policy.advance_governance(state, at + timedelta(hours=22))
        changed = copy.deepcopy(state)
        old_event = next(e for e in changed["events"] if e["event"] == "PROMOTION")
        candidate = changed["candidates"][old_event["candidate_id"]]
        when = policy._iso(at + timedelta(days=2))
        version = policy._version(candidate["proposed_change"], when, candidate["candidate_id"], changed["active_version_id"])
        version["runtime_code_sha256"] = candidate["runtime_binding"]["runtime_code_sha256"]
        changed["versions"][changed["active_version_id"]]["status"] = "SUPERSEDED"
        changed["versions"][version["version_id"]] = version
        changed["active_version_id"] = version["version_id"]
        changed["events"].append({**old_event, "at": when, "version_id": version["version_id"]})
        changed["updated_at"] = when
        with self.assertRaisesRegex(ValueError, "candidate promotion authorization replay"):
            policy.validate_state(changed)

    def test_missing_coverage_never_becomes_zero_and_persistent_gap_rolls_back(self):
        state, promoted = self.mature()
        promoted_id = state["active_version_id"]
        at, manifest, position = self.opportunity(state, 100, at=promoted + timedelta(days=2))
        state, cid = policy.freeze_case(state, decision_manifest=manifest, position=position, score=53.5, now=at + timedelta(seconds=1))
        for minutes in (30, 60, 121):
            state = policy.settle_cases(state, evaluator=lambda *_: {"coverage_complete": False, "reason": "missing_1m_path"}, now=at + timedelta(minutes=minutes))
        state = policy.advance_governance(state, at + timedelta(hours=3))
        self.assertIsNone(state["cases"][cid]["outcome"])
        self.assertNotEqual(state["active_version_id"], promoted_id)
        self.assertEqual(state["rollback_events"][-1]["reason"], "DATA_QUALITY_FAILURE")
        self.assertFalse(policy.policy_context(state, 53.5, "LONG", observed_at=at + timedelta(hours=2))["allowed"])
        self.assertTrue(policy.policy_context(state, 53.5, "LONG", observed_at=at + timedelta(hours=4))["allowed"])


if __name__ == "__main__":
    unittest.main()
