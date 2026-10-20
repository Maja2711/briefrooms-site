from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone

from scripts import daily_eurusd_production_policy as policy
from scripts import daily_eurusd_reflection as reflection
from tests import test_daily_eurusd_production_policy as fixtures


class ReflectionMemoryTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 20, tzinfo=timezone.utc)
        self.state = policy.default_state(self.start)

    def trade(self, number=0, *, opened=None, net_r=-.4, direction="LONG",
              score=54.5, confidence=.4, evidence="source-1", code="a" * 64):
        opened = opened or self.start + timedelta(hours=number)
        stop = 1.09 if direction == "LONG" else 1.11
        final = {
            "direction": direction, "score": score, "confidence": confidence, "coverage_weight": .7,
            "used_beliefs": [{"belief_id": "eurusd.trend.bullish", "source_evidence": [
                {"evidence_id": evidence, "observed_at": policy._iso(self.start)}]}],
        }
        manifest = {
            "schema_version": policy.MANIFEST_SCHEMA, "instrument": "EUR/USD",
            "engine_version": policy.ENGINE_VERSION, "decision_id": f"reflection-decision-{number}",
            "decision_at": policy._iso(opened), "runtime_code_sha256": code,
            "final_decision": final, "execution_geometry": {"entry": 1.1, "stop": stop, "target": 1.118},
            "policy_version_id": self.state["active_version_id"],
        }
        manifest["manifest_sha256"] = policy.sha256(manifest)
        return {
            "trade_id": f"reflection-trade-{number}", "opened_at": policy._iso(opened),
            "closed_at": policy._iso(opened + timedelta(minutes=10)), "direction": direction,
            "entry": 1.1, "exit_price": 1.1 + (1 if direction == "LONG" else -1) * .01 * net_r,
            "r_multiple": 999, "exit_reason": "DYNAMIC_RISK_EXIT", "decision_manifest": manifest,
        }

    def sync(self, trades, previous=None, state=None, *, now=None, code="a" * 64):
        return reflection.sync(previous, {"trades": trades}, state or self.state,
                               now=policy._iso(now or self.start + timedelta(days=1)), runtime_code_sha256=code)

    def test_actual_r_is_recomputed_and_old_results_are_discovery_only(self):
        trade = self.trade()
        memory = self.sync([trade])
        record = memory["records"][trade["trade_id"]]
        self.assertAlmostEqual(record["net_r"], -.4)
        self.assertEqual(record["evidence_use"], "DISCOVERY_ONLY_NOT_OOS")
        self.assertEqual(memory["lessons"][0]["status"], "HYPOTHESIS_ONLY")
        self.assertTrue(all(not row["production_authority"] for row in memory["hypotheses"]))
        reflection.validate_history(memory, {"trades": [trade]})

    def test_losing_short_uses_frozen_risk_and_correct_direction(self):
        trade = self.trade(direction="SHORT", score=45.5)
        record = self.sync([trade])["records"][trade["trade_id"]]
        self.assertAlmostEqual(record["net_r"], -.4)

    def test_repeat_loss_and_unchanged_original_evidence_are_detected(self):
        first = self.trade(0)
        second = self.trade(1, opened=self.start + timedelta(minutes=12))
        memory = self.sync([second, first])
        record = memory["records"][second["trade_id"]]
        self.assertEqual(record["prior_loss_trade_id"], first["trade_id"])
        self.assertIn("SAME_DIRECTION_REENTRY_WITHIN_1H_OF_LOSS", record["findings"])
        self.assertIn("UNCHANGED_SOURCE_EVIDENCE_AFTER_LOSS", record["findings"])

    def test_new_original_evidence_is_distinguished_from_same_source(self):
        first = self.trade()
        second = self.trade(1, evidence="new-fact")
        record = self.sync([first, second])["records"][second["trade_id"]]
        self.assertIn("SAME_DIRECTION_REENTRY_WITHIN_1H_OF_LOSS", record["findings"])
        self.assertNotIn("UNCHANGED_SOURCE_EVIDENCE_AFTER_LOSS", record["findings"])

    def test_winners_are_retained_and_count_against_loss_avoidance_hypothesis(self):
        memory = self.sync([self.trade(0, net_r=-.4), self.trade(1, net_r=1)])
        hypothesis = next(row for row in memory["hypotheses"] if row["minimum_score_distance"] == 5)
        self.assertEqual((hypothesis["avoided_losses"], hypothesis["lost_winners"]), (1, 1))
        self.assertAlmostEqual(hypothesis["discovery_mean_delta_r"], -.3)
        self.assertTrue(all(row["wins"] == 1 for row in memory["lessons"]))

    def test_future_close_is_not_reflected(self):
        memory = self.sync([self.trade()], now=self.start + timedelta(minutes=5))
        self.assertFalse(memory["records"])

    def test_unknown_evidence_never_becomes_a_claim_of_unchanged_evidence(self):
        first, second = self.trade(), self.trade(1)
        for trade in (first, second):
            manifest = trade["decision_manifest"]
            manifest["final_decision"].pop("used_beliefs")
            manifest.pop("manifest_sha256")
            manifest["manifest_sha256"] = policy.sha256(manifest)
        record = self.sync([first, second])["records"][second["trade_id"]]
        self.assertIn("ENTRY_CONTEXT_PARTIALLY_UNKNOWN", record["findings"])
        self.assertNotIn("UNCHANGED_SOURCE_EVIDENCE_AFTER_LOSS", record["findings"])

    def test_discovery_hypothesis_does_not_pool_runtime_code_cohorts(self):
        memory = self.sync([self.trade(code="b" * 64)])
        self.assertEqual(len(memory["records"]), 1)
        self.assertTrue(all(row["discovery_n"] == 0 for row in memory["hypotheses"]))

    def test_idempotent_monitor_does_not_manufacture_new_learning_iteration(self):
        trade = self.trade()
        first = self.sync([trade])
        again = self.sync([trade], previous=first, now=self.start + timedelta(days=2))
        self.assertEqual(first, again)

    def test_new_outcome_recursively_updates_lessons_without_rewriting_old_memory(self):
        first = self.sync([self.trade()])
        second = self.sync([self.trade(), self.trade(1, net_r=1)], previous=first)
        reflection.validate_transition(first, second)
        self.assertEqual(len(second["iterations"]), 2)
        self.assertEqual(second["iterations"][1]["parent_sha256"], first["iterations"][0]["iteration_sha256"])
        self.assertEqual(second["records"]["reflection-trade-0"], first["records"]["reflection-trade-0"])

    def test_failed_gate_or_rollback_becomes_next_iteration_feedback(self):
        first = self.sync([])
        changed = copy.deepcopy(self.state)
        candidate = next(iter(changed["candidates"].values()))
        candidate["status"] = "REJECTED"
        candidate["gate"] = {"gate_id": "failed-test", "blockers": ["protected_slice:SHORT"]}
        second = self.sync([], previous=first, state=changed)
        self.assertEqual(len(second["iterations"]), 2)
        feedback = second["iterations"][-1]["feedback"]["candidates"]
        self.assertIn("protected_slice:SHORT", next(row for row in feedback if row["candidate_id"] == candidate["candidate_id"])["blockers"])

    def test_new_entry_review_uses_known_similar_actual_outcomes(self):
        trade = self.trade()
        memory = self.sync([trade])
        review = reflection.review(memory, trade["decision_manifest"]["final_decision"],
                                   observed_at=policy._iso(self.start + timedelta(days=2)))
        self.assertEqual(review["recent_similar_loss_ids"], [trade["trade_id"]])
        self.assertFalse(review["source_evidence_changed_since_latest_similar_loss"])
        self.assertFalse(review["may_override_direction_risk_or_safety"])
        self.assertTrue(review["policy_change_requires_prospective_gate"])

    def test_memory_cannot_leak_into_past_decision_review(self):
        trade = self.trade()
        memory = self.sync([trade])
        review = reflection.review(memory, trade["decision_manifest"]["final_decision"], observed_at=trade["opened_at"])
        self.assertEqual(review["similar_actual_trades"], 0)
        self.assertFalse(review["available"])
        self.assertEqual(review["reflection_iteration"], 0)
        self.assertIsNone(review["memory_sha256"])

    def test_foreign_instrument_is_not_added_to_eurusd_memory(self):
        foreign = self.trade()
        foreign["decision_manifest"]["instrument"] = "GOLD"
        memory = self.sync([foreign])
        self.assertFalse(memory["records"])

    def test_missing_frozen_risk_fails_closed(self):
        trade = self.trade()
        manifest = trade["decision_manifest"]
        manifest["execution_geometry"]["stop"] = None
        manifest.pop("manifest_sha256")
        manifest["manifest_sha256"] = policy.sha256(manifest)
        with self.assertRaisesRegex(ValueError, "frozen entry/risk"):
            self.sync([trade])

    def test_history_mutation_and_memory_deletion_are_rejected(self):
        trade = self.trade()
        memory = self.sync([trade])
        changed = copy.deepcopy(trade)
        changed["r_multiple"] = 0
        with self.assertRaisesRegex(ValueError, "canonical history changed"):
            self.sync([changed], previous=memory)
        with self.assertRaisesRegex(ValueError, "epoch removed"):
            reflection.validate_transition(memory, None)
        tampered = copy.deepcopy(memory)
        tampered["records"] = {}
        with self.assertRaisesRegex(ValueError, "history rewritten"):
            reflection.validate_transition(memory, tampered)

    def test_rehashed_fabricated_facts_fail_canonical_reconciliation(self):
        trade = self.trade()
        memory = self.sync([trade])
        row = memory["records"][trade["trade_id"]]
        row["score"] = 99
        row.pop("reflection_sha256")
        row["reflection_sha256"] = policy.sha256(row)
        with self.assertRaisesRegex(ValueError, "facts differ"):
            reflection.validate_history(memory, {"trades": [trade]})

    def test_policy_state_owns_memory_and_enforces_append_only_migration(self):
        trade = self.trade()
        state = policy.reflect_on_history(self.state, {"trades": [trade]}, self.start + timedelta(days=1),
                                         runtime_code_sha256="a" * 64)
        policy.validate_history_bindings(state, {"trades": [trade]})
        policy.validate_transition(self.state, state)
        erased = copy.deepcopy(state)
        erased.pop("recursive_improvement")
        with self.assertRaisesRegex(ValueError, "epoch removed"):
            policy.validate_transition(state, erased)


class ParallelRecursivePolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProductionAdmissionPolicyTests()
        self.fixture.setUp()
        self.start = self.fixture.start

    def test_all_stricter_candidates_vote_before_any_outcome(self):
        state = policy.default_state(self.start)
        self.assertEqual({row["proposed_change"]["minimum_score_distance"] for row in state["candidates"].values()}, {4, 5, 6})
        at, manifest, position = self.fixture.opportunity(state, score=54.5)
        state, cid = policy.freeze_case(state, decision_manifest=manifest, position=position, score=54.5,
                                       now=at + timedelta(seconds=1))
        votes = state["cases"][cid]["frozen"]["votes"]
        for candidate in state["candidates"].values():
            self.assertIn(candidate["candidate_id"], votes)
            self.assertEqual(candidate["evaluation_case_ids"], [cid])
        self.assertIsNone(state["cases"][cid]["outcome"])
        policy.validate_state(state)

    def test_system_can_discover_better_floor_without_waiting_for_failed_intermediate_promotion(self):
        state = policy.default_state(self.start)
        for index in range(policy.PRIMARY_N):
            state, _, at = self.fixture.actual_case(state, index, score=54.5)
        state = policy.advance_governance(state, at + timedelta(hours=22))
        version = state["versions"][state["active_version_id"]]
        self.assertEqual(version["policy"]["minimum_score_distance"], 5)
        self.assertFalse(policy.policy_context(state, 54.5, "LONG")["allowed"])
        failed = next(row for row in state["candidates"].values() if row["proposed_change"]["minimum_score_distance"] == 4)
        self.assertEqual(failed["gate"]["status"], "FAIL")
        policy.validate_state(state)

    def test_parallel_winners_use_least_restrictive_policy_when_measured_results_tie(self):
        state, _ = self.fixture.mature()
        self.assertEqual(state["versions"][state["active_version_id"]]["policy"]["minimum_score_distance"], 4)
        self.assertEqual(len([row for row in state["candidates"].values() if row["status"] == "PROMOTED"]), 1)
        self.assertTrue(all(row["gate"]["status"] == "PASS" for row in state["candidates"].values()))
        policy.validate_state(state)

    def test_three_actual_losses_do_not_authorize_any_promotion(self):
        state = policy.default_state(self.start)
        for index in range(3):
            state, _, at = self.fixture.actual_case(state, index)
        state = policy.advance_governance(state, at + timedelta(hours=22))
        self.assertEqual(state["versions"][state["active_version_id"]]["policy"]["minimum_score_distance"], 3)
        self.assertTrue(all(row["gate"]["status"] == "COLLECTING" for row in state["candidates"].values()))

    def test_discovery_memory_cannot_supply_oos_samples(self):
        state = policy.default_state(self.start)
        memory_fixture = ReflectionMemoryTests()
        memory_fixture.setUp()
        state = policy.reflect_on_history(state, {"trades": [memory_fixture.trade()]},
                                         self.start + timedelta(days=1), runtime_code_sha256="a" * 64)
        state = policy.advance_governance(state, self.start + timedelta(days=1), runtime_code_sha256="a" * 64)
        self.assertTrue(state["recursive_improvement"]["records"])
        self.assertTrue(all(row["gate"]["observed_sample"] == 0 for row in state["candidates"].values()))
        self.assertEqual(state["versions"][state["active_version_id"]]["policy"]["minimum_score_distance"], 3)

    def test_rejected_siblings_do_not_reset_three_attempt_family_budget(self):
        state = policy.default_state(self.start)
        for attempt in range(3):
            base = self.start + timedelta(days=120 * attempt + 1)
            state = policy.advance_governance(state, base, runtime_code_sha256="a" * 64)
            for index in range(policy.PRIMARY_N):
                state, _, at = self.fixture.actual_case(state, attempt * 100 + index,
                                                       at=base + timedelta(days=2 * index, hours=1),
                                                       result_r=1, score=53.5)
            state = policy.advance_governance(state, at + timedelta(hours=22), runtime_code_sha256="a" * 64)
        state = policy.advance_governance(state, at + timedelta(days=40), runtime_code_sha256="a" * 64)
        self.assertEqual(len(state["candidates"]), 9)
        self.assertFalse(any(row["status"] == "OOS_RUNNING" for row in state["candidates"].values()))
        self.assertEqual(state["versions"][state["active_version_id"]]["policy"]["minimum_score_distance"], 3)
        policy.validate_state(state)

    def test_returning_to_a_parked_code_cohort_does_not_restart_attempt_one(self):
        state = policy.default_state(self.start)
        state, _, at = self.fixture.actual_case(state, 0)
        a_ids = set(state["candidates"])
        state = policy.advance_governance(state, at + timedelta(days=1), runtime_code_sha256="b" * 64)
        state = policy.advance_governance(state, at + timedelta(days=2), runtime_code_sha256="a" * 64)
        self.assertFalse(any(row["status"] == "OOS_RUNNING" for row in state["candidates"].values()))
        state = policy.advance_governance(state, at + timedelta(days=40), runtime_code_sha256="a" * 64)
        a_new = [row for row in state["candidates"].values()
                 if row["status"] == "OOS_RUNNING" and row["candidate_id"] not in a_ids]
        self.assertEqual(len(a_new), 3)
        self.assertTrue(all(row["metadata"]["attempt"] == 2 for row in a_new))
        policy.validate_state(state)

    def test_research_changes_do_not_authorize_direction_risk_or_hard_safety_override(self):
        state, _ = self.fixture.mature()
        context = policy.policy_context(state, 90, "SHORT")
        self.assertFalse(context["allowed"])
        self.assertFalse(context["direction_mutation_allowed"])
        self.assertFalse(context["risk_mutation_allowed"])
        self.assertFalse(reflection.AUTHORITY["hard_safety_override_allowed"])


if __name__ == "__main__":
    unittest.main()
