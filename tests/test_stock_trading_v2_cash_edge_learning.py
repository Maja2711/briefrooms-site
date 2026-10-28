from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import stock_trading_v2_admission_ledger as admission
from scripts import stock_trading_v2_challenger_factory as factory
from scripts import stock_trading_v2_challenger_eval as generic
from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_exact_challenger_eval as formal
from scripts import stock_trading_v2_exact_replay as exact
from scripts import stock_trading_v2_experience_store as store
from scripts import stock_trading_v2_opportunity_engine as opportunity
from scripts import stock_trading_v2_opportunity_experience as experience
from scripts import stock_trading_v2_policy_learner as learner
from scripts import stock_trading_v2_production_replay as production_replay
from scripts import stock_trading_v2_regret_engine as regret
from scripts import stock_trading_v2_replay_tournament as tournament
from tests.test_stock_trading_v2_opportunity_experience import evidence_snapshot


POLICY = {"policy_version": "cash-test", "champion_engine": "v2", "markets": {
    market: {"minimum_cash_edge_points": 5, "max_open_positions": 3} for market in ("US", "GPW")}}
MANIFEST = {"revision": 7, "components": {"entry": {"version": "entry-7"}}}
START = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)


def candidates():
    return factory._cash_edge_candidates(hypothesis={"component": "cash_admission_margin", "market": "US", "horizon_sessions": 2},
                                        manifest=MANIFEST, policy=POLICY)


def event_at(moment=START + timedelta(hours=1), *, score=54.5, held=False):
    evidence = evidence_snapshot()
    evidence["generated_at"] = moment.isoformat().replace("+00:00", "Z")
    evidence["candidates"] = evidence["candidates"][:1]
    evidence["candidate_count"] = 1
    evidence["candidates"][0]["opportunity_score"] = score
    evidence["candidates"][0]["deep_opportunity_score"] = score
    evidence.pop("evidence_sha256")
    evidence["evidence_sha256"] = contracts.payload_sha256(evidence)
    positions = [{"symbol": "ACME", "status": "OPEN"}] if held else []
    snapshot = opportunity.compare_opportunities(evidence, {"markets": {"US": {"open_positions": positions}}},
        opportunity.load_config(), generated_at=moment, policy_snapshot=POLICY, champion_manifest=MANIFEST,
        bridge_config_snapshot={"schema_version": "test"})
    return experience.events_from_snapshots(snapshot, evidence, recorded_at=moment.isoformat().replace("+00:00", "Z"))[0]


def challenger(horizon=2):
    candidate = factory._cash_edge_candidates(
        hypothesis={"component": "cash_admission_margin", "market": "US", "horizon_sessions": horizon},
        manifest=MANIFEST, policy=POLICY)[0]
    row = tournament.make_challenger(candidate, "discovery-sha", {"cash_protocol_version": "legacy_v1"}, start_at=START.isoformat().replace("+00:00", "Z"))
    family = {"family_id": "stock-trading-v2-cash-edge-lifetime-v1", "test_index": 1,
              "challenger_id": row["challenger_id"], "protocol_sha256": row["protocol_sha256"],
              "reserved_at": START.isoformat(), "lifetime_max_tests": 24, "alpha": 0.05 / 24}
    family["reservation_sha256"] = contracts.payload_sha256(family)
    row["test_family"] = family
    row.pop("challenger_sha256")
    row["challenger_sha256"] = contracts.payload_sha256(row)
    return row


def trial_receipts(item, *, registered_at=START):
    row = {"challenger_id": item["challenger_id"], "challenger_sha256": item["challenger_sha256"],
           "family_id": item["test_family"]["family_id"], "test_index": item["test_family"]["test_index"],
           "registered_at": registered_at.isoformat(), "policy_sha256": item["champion"]["policy_sha256"],
           "protocol_sha256": item["protocol_sha256"]}
    return {"schema_version": "stock-trading-component-trials-v1", "trials": {item["challenger_id"]: row}}


def epoch():
    row = {"schema_version": admission.EPOCH_SCHEMA_VERSION, "activated_at": "2026-10-01T10:00:00Z",
           "initial_policy_sha256": contracts.payload_sha256(POLICY), "immutable": True}
    row["epoch_sha256"] = contracts.payload_sha256(row)
    return row


def admitted(event):
    return admission.make_observation(event, policy=POLICY, epoch=epoch(), recorded_at=event["decision_at"])


def native_frames(*events):
    """Real hashed main-frame envelopes; enrollment needs no outcome quotes."""
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for event in events:
            context = event["candidate_state"]["decision_context"]
            moment = exact.parse_dt(event["decision_at"])
            rows.append(production_replay.capture_frame(Path(tmp), kind="ADMISSION", observed_at=moment,
                recorded_at=moment + timedelta(seconds=1), policy=context["policy_snapshot"],
                initial_state=context["portfolio_snapshot"], opportunities={event["market"]: context["opportunity_snapshot"]},
                bridge_config=context["bridge_config_snapshot"], manifest=context["champion_manifest"])["frame"])
    return rows


class CashEdgeLearningTests(unittest.TestCase):
    def test_changed_full_policy_starts_distinct_trial_without_rewriting_old_one(self):
        old_candidate = candidates()[0]
        changed_policy = deepcopy(POLICY)
        changed_policy["policy_version"] = "changed-full-production-baseline"
        new_candidate = factory._cash_edge_candidates(
            hypothesis={"component": "cash_admission_margin", "market": "US", "horizon_sessions": 2},
            manifest=MANIFEST, policy=changed_policy)[0]
        old = tournament.make_challenger(old_candidate, "discovery", {}, start_at=START.isoformat())
        new = tournament.make_challenger(new_candidate, "discovery", {}, start_at=START.isoformat())
        self.assertEqual(old["production_candidate"], new["production_candidate"])
        self.assertNotEqual(old["challenger_id"], new["challenger_id"])
        self.assertNotEqual(old["champion"]["policy_sha256"], new["champion"]["policy_sha256"])

    def test_native_completed_close_censors_before_descriptive_settlement(self):
        item, event = challenger(horizon=1), event_at()
        frames = native_frames(event)
        close = START + timedelta(hours=11)
        receipt = close + timedelta(minutes=1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames.append(production_replay.capture_frame(root / "frames", kind="SESSION_CLOSE",
                observed_at=receipt, recorded_at=receipt, policy=POLICY, initial_state={},
                market_sessions={"US": {"date": close.date().isoformat(),
                    "regular_start": (START + timedelta(hours=4)).isoformat(),
                    "regular_end": close.isoformat(), "source": "frozen provider session", "state": "CLOSED"}})["frame"])
            cohort = formal.freeze_cohort(item, events=[event], admissions=[admitted(event)], outcomes=[],
                cohort_root=root / "cohorts", now=receipt, trial_receipts=trial_receipts(item), replay_frames=frames)
            self.assertEqual("CENSORED_LATE_ENROLLMENT", cohort["members"][0]["enrollment_status"])

    def test_factory_uses_real_v2_margin_and_only_one_bounded_parameter(self):
        rows = candidates()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["replay_contract"]["challenger_cash_edge_points"] for row in rows}, {4.0, 6.0})
        for row in rows:
            factory.validate_candidate(row)
            self.assertEqual(set(row["deployment_spec"]["targets"]), {"stock_trading_policy"})
            self.assertEqual(row["production_component"], "entry")
            self.assertEqual(row["champion_policy_sha256"], contracts.payload_sha256(POLICY))
            left, right = exact.cash_edge_policy_pair(row)
            self.assertEqual(left, POLICY)
            changed = right["markets"]["US"].pop("minimum_cash_edge_points")
            unchanged = deepcopy(POLICY)
            unchanged["markets"]["US"].pop("minimum_cash_edge_points")
            self.assertEqual(right, unchanged)
            self.assertIn(changed, {4, 6})

    def test_active_v2_does_not_enter_legacy_72_vs_0_factory(self):
        report = {"schema_version": regret.SCHEMA_VERSION, "challenger_hypotheses": [{
            "component": "entry_score_below_threshold", "status": "ELIGIBLE_FOR_CHALLENGER_HOLDOUT", "horizon_sessions": 2}],
            "governance": {"production_decision_influence": False, "automatic_policy_writeback": False}}
        report["report_sha256"] = contracts.payload_sha256(report)
        rows, skipped = factory.build_candidates(report=report, manifest=MANIFEST, gpw_config={"minimum_composite_score": 72}, policy=POLICY)
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(row["research_component"] == "cash_admission_margin" for row in rows))
        self.assertEqual({(row["market"], row["horizon_sessions"]) for row in rows}, {("GPW", 1), ("GPW", 2), ("US", 1), ("US", 2)})
        self.assertEqual(len(skipped), 1)

    def test_real_source_adapter_has_frozen_policy_context_and_causal_margin(self):
        event = event_at()
        candidate = candidates()[0]
        self.assertTrue(exact.cash_edge_eligible(event, candidate))
        self.assertTrue(admission.is_formal_observation(admitted(event)))
        context = event["candidate_state"]["decision_context"]
        self.assertEqual(context["portfolio_sha256"], contracts.payload_sha256(context["portfolio_snapshot"]))
        self.assertEqual(context["opportunity_sha256"], contracts.payload_sha256(context["opportunity_snapshot"]))
        self.assertEqual(event["candidate_state"]["decision_path"]["first_blocking_gate"], "cash_admission_margin")
        for key, value in (("available_slots", 0), ("eligible", False), ("already_held", True),
                           ("cash_margin_only_blocker", False)):
            broken = deepcopy(event)
            broken["candidate_state"]["decision_path"][key] = value
            self.assertFalse(exact.cash_edge_eligible(broken, candidate))
        ranked = deepcopy(event)
        ranked["candidate_state"]["decision_path"].update({"first_blocking_gate": "opportunity_ranked_below_selected_candidate", "cash_margin_only_blocker": False})
        self.assertFalse(exact.cash_edge_eligible(ranked, candidate))

    def test_a_real_margin_error_generates_a_future_exact_hypothesis(self):
        event = event_at()
        outcome = {"replay": {"status": "SETTLED", "net_r": 0.4}}
        config = {"learning": {"minimum_observations_for_hypothesis": 1,
                              "minimum_observations_for_challenger": 1, "minimum_unique_symbols": 1}}
        rows = regret._cash_margin_hypotheses({event["event_id"]: event}, {(event["event_id"], 2): outcome}, config)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["component"], "cash_admission_margin")
        self.assertEqual(rows[0]["market"], "US")
        self.assertTrue(rows[0]["evidence"]["formal_holdout_reuse_forbidden"])
        self.assertEqual(rows[0]["suggested_experiment"]["predeclared_deltas"], [-1, 1])

    def test_membership_frozen_before_outcome_and_later_results_do_not_replace_it(self):
        item, event = challenger(), event_at()
        observed = admitted(event)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = formal.freeze_cohort(item, events=[event], admissions=[observed], outcomes=[], cohort_root=root, now=START + timedelta(hours=2), trial_receipts=trial_receipts(item), replay_frames=native_frames(event))
            member_path = root / item["challenger_id"] / "members" / "0001.json"
            before = member_path.read_bytes()
            later = event_at(START + timedelta(days=8))
            second = formal.freeze_cohort(item, events=[event, later], admissions=[observed, admitted(later)],
                outcomes=[{"source_event_id": event["event_id"]}], cohort_root=root, now=START + timedelta(days=8, hours=1), trial_receipts=trial_receipts(item), replay_frames=native_frames(event, later))
            self.assertEqual(member_path.read_bytes(), before)
            self.assertEqual(second["members"][0], first["members"][0])
            self.assertEqual(len(second["members"]), 2)
            self.assertEqual(second["members"][0]["enrollment_status"], "FROZEN_BEFORE_OUTCOME")

    def test_known_outcome_before_enrollment_censors_and_cannot_be_replaced(self):
        item, event = challenger(), event_at()
        with tempfile.TemporaryDirectory() as tmp:
            cohort = formal.freeze_cohort(item, events=[event], admissions=[admitted(event)],
                outcomes=[{"source_event_id": event["event_id"]}], cohort_root=Path(tmp), now=START + timedelta(hours=2), trial_receipts=trial_receipts(item), replay_frames=native_frames(event))
            self.assertEqual(cohort["members"][0]["enrollment_status"], "CENSORED_LATE_ENROLLMENT")

    def test_same_episode_repeats_are_not_independent_and_missing_native_tape_is_pending(self):
        item, event = challenger(), event_at()
        same_day = event_at(START + timedelta(hours=2))
        with tempfile.TemporaryDirectory() as tmp:
            cohort = formal.freeze_cohort(item, events=[event, same_day], admissions=[admitted(event), admitted(same_day)],
                outcomes=[], cohort_root=Path(tmp), now=START + timedelta(hours=3), trial_receipts=trial_receipts(item), replay_frames=native_frames(event, same_day))
            self.assertEqual(len(cohort["members"]), 1)
        samples = exact.collect_cash_edge_samples(candidate=candidates()[0], events=[event], admissions=[admitted(event)],
            outcomes=[{"source_event_id": event["event_id"], "replay": {"status": "SETTLED", "net_return_percent": 10}}])
        self.assertEqual(samples, [])  # Frozen next-open proxy is never exact.

    def test_unobserved_research_cycles_never_create_or_censor_members(self):
        item, research_only = challenger(), event_at()
        received = event_at(START + timedelta(days=8))
        now = START + timedelta(days=8, hours=2)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            waiting = formal.freeze_cohort(item, events=[research_only], admissions=[admitted(research_only)],
                outcomes=[{"source_event_id": research_only["event_id"]}], cohort_root=root,
                now=now, trial_receipts=trial_receipts(item))
            self.assertEqual(waiting["members"], [])
            self.assertFalse((root / item["challenger_id"] / "members").exists())
            report = formal.evaluate_cash(item, events=[research_only, received],
                admissions=[admitted(research_only), admitted(received)],
                outcomes=[{"source_event_id": research_only["event_id"]}], cohort_root=root,
                now=now, trial_receipts=trial_receipts(item), replay_frames=native_frames(received))
            cohort = report["formal_proof"]["cohort"]
            self.assertEqual([row["source_event_id"] for row in cohort["members"]], [received["event_id"]])
            self.assertEqual(cohort["members"][0]["index"], 1)
            self.assertEqual(cohort["members"][0]["enrollment_status"], "FROZEN_BEFORE_OUTCOME")
            self.assertEqual(report["state"], "COLLECTING_FRESH_HOLDOUT")
            self.assertFalse(report["formal_proof"]["terminal"])

    def test_only_valid_current_main_admission_frames_can_authorize_membership(self):
        item, event = challenger(), event_at()
        frame = native_frames(event)[0]
        variants = {}
        broken = deepcopy(frame)
        broken["frame_sha256"] = "changed"
        variants["invalid receipt hash"] = broken
        for name, key, value in (
            ("other frame kind", "kind", "LIFECYCLE"),
            ("unreviewed production code", "production_code_sha256", "changed"),
            ("receipt not yet observed", "recorded_at", (START + timedelta(hours=3)).isoformat()),
            ("different policy", "policy_sha256", "changed"),
            ("different manifest", "manifest", {"revision": 8}),
        ):
            broken = deepcopy(frame)
            broken[key] = value
            broken.pop("frame_sha256")
            broken["frame_sha256"] = contracts.payload_sha256(broken)
            variants[name] = broken
        for name, broken in variants.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cohort = formal.freeze_cohort(item, events=[event], admissions=[admitted(event)], outcomes=[],
                    cohort_root=root, now=START + timedelta(hours=2), trial_receipts=trial_receipts(item),
                    replay_frames=[broken])
                self.assertEqual(cohort["members"], [])
                self.assertFalse((root / item["challenger_id"] / "members").exists())
        with tempfile.TemporaryDirectory() as tmp:
            cohort = formal.freeze_cohort(item, events=[event], admissions=[admitted(event)], outcomes=[],
                cohort_root=Path(tmp), now=START + timedelta(hours=2), trial_receipts=trial_receipts(item),
                replay_frames=[{"frame": frame}])
            self.assertEqual([row["source_event_id"] for row in cohort["members"]], [event["event_id"]])

    def test_missing_native_episode_source_or_admission_cannot_be_replaced_by_later_episode(self):
        item, earlier = challenger(), event_at()
        later = event_at(START + timedelta(days=8))
        altered = deepcopy(earlier)
        altered["candidate_state"]["decision_path"]["reason"] = "changed research source"
        for name, events, admissions in (
            ("missing source", [later], [admitted(earlier), admitted(later)]),
            ("missing admission", [earlier, later], [admitted(later)]),
            ("different source", [altered, later], [admitted(earlier), admitted(later)]),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cohort = formal.freeze_cohort(item, events=events, admissions=admissions, outcomes=[],
                    cohort_root=root, now=START + timedelta(days=8, hours=2), trial_receipts=trial_receipts(item),
                    replay_frames=native_frames(earlier, later))
                self.assertEqual(cohort["members"], [])
                self.assertFalse((root / item["challenger_id"] / "members").exists())

    def test_lifetime_budget_is_shared_across_champion_generations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(24):
                item = challenger()
                item.pop("test_family")
                item["challenger_id"] += f"-generation-{index}"
                item.pop("challenger_sha256")
                item["challenger_sha256"] = contracts.payload_sha256(item)
                self.assertTrue(learner.persist(root, item))
            extra = challenger()
            extra.pop("test_family")
            extra["challenger_id"] += "-generation-25"
            extra.pop("challenger_sha256")
            extra["challenger_sha256"] = contracts.payload_sha256(extra)
            self.assertFalse(learner.persist(root, extra))
            reservations = [json.loads(path.read_text()) for path in (root / ".test-budget").glob("*.json")]
            self.assertEqual({row["test_index"] for row in reservations}, set(range(1, 25)))
            self.assertLessEqual(sum(row["alpha"] for row in reservations), 0.05 + 1e-12)

    def test_closed_verdict_is_byte_identical_after_new_outcomes_arrive(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            roots = {key: base / key for key in ("challenger", "experience", "admission", "outcome", "output", "frames")}
            for root in roots.values():
                root.mkdir()
            item = challenger()
            learner.persist(roots["challenger"], item)
            item = json.loads((roots["challenger"] / f"{item['challenger_id']}.json").read_text())
            event = event_at()
            (roots["experience"] / "event.json").write_text(json.dumps(event))
            (roots["admission"] / "admission.json").write_text(json.dumps(admitted(event)))
            (roots["frames"] / "frame.json").write_text(json.dumps(native_frames(event)[0]))
            args = dict(challenger_root=roots["challenger"], experience_root=roots["experience"], admission_root=roots["admission"],
                        outcome_root=roots["outcome"], output_root=roots["output"], trial_receipts=trial_receipts(item), replay_frame_root=roots["frames"])
            with patch.object(formal, "_code_unchanged", return_value=True):
                formal.run(**args, now=START + timedelta(hours=2))
                path = roots["output"] / f"{item['challenger_id']}.json"
                self.assertEqual(json.loads(path.read_text())["state"], "COLLECTING_FRESH_HOLDOUT")
                formal.run(**args, now=START + timedelta(days=181))
                self.assertEqual(json.loads(path.read_text())["state"], "FORMAL_HOLDOUT_FAIL")
                before = path.read_bytes()
                (roots["outcome"] / "new.json").write_text(json.dumps({"source_event_id": event["event_id"], "result": 200}))
                formal.run(**args, now=START + timedelta(days=182))
                self.assertEqual(path.read_bytes(), before)

    def test_formal_statistics_use_lifetime_alpha_and_reject_drawdown_or_concentration(self):
        item = challenger()
        samples = [{"source_event_id": f"e{i}", "block_id": f"b{i}", "symbol": f"S{i % 6}",
            "decision_at": (START + timedelta(days=i * 7)).isoformat(), "incremental_net_return_percent": 0.5,
            "champion_max_drawdown_percent": 1, "challenger_max_drawdown_percent": 1,
            "benchmark_return_percent": 0.1, "execution_semantics": "production_lifecycle_v1",
            "production_replay": {"formal_evidence_eligible": True}} for i in range(30)]
        self.assertTrue(formal.formal_metrics(samples, item["protocol"], seed="test")["formal_pass"])
        samples[0]["challenger_max_drawdown_percent"] = 2
        metrics = formal.formal_metrics(samples, item["protocol"], seed="test")
        self.assertFalse(metrics["formal_pass"])
        self.assertFalse(metrics["checks"]["drawdown_non_inferiority"])

    def test_production_receipt_is_required_before_any_member_can_freeze(self):
        item, event = challenger(), event_at()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cohort = formal.freeze_cohort(item, events=[event], admissions=[admitted(event)], outcomes=[],
                cohort_root=root, now=START + timedelta(hours=2))
            self.assertEqual(cohort["members"], [])
            self.assertTrue(cohort["awaiting_production_trial_receipt"])
            self.assertEqual(list(root.iterdir()), [])
            report = formal.evaluate_cash(item, events=[event], admissions=[admitted(event)], outcomes=[],
                cohort_root=root, now=START + timedelta(days=181))
            self.assertEqual(report["state"], "COLLECTING_FRESH_HOLDOUT")
            self.assertFalse(report["formal_proof"]["terminal"])

    def test_registration_never_backdates_a_known_decision(self):
        item, early = challenger(), event_at()
        future = event_at(START + timedelta(days=8))
        with tempfile.TemporaryDirectory() as tmp:
            cohort = formal.freeze_cohort(item, events=[early, future], admissions=[admitted(early), admitted(future)],
                outcomes=[], cohort_root=Path(tmp), now=START + timedelta(days=8, hours=1),
                trial_receipts=trial_receipts(item, registered_at=START + timedelta(days=1)), replay_frames=native_frames(early, future))
            self.assertEqual([row["source_event_id"] for row in cohort["members"]], [future["event_id"]])
            changed = trial_receipts(item)
            changed["trials"][item["challenger_id"]]["challenger_sha256"] = "changed"
            with self.assertRaisesRegex(contracts.ContractError, "receipt binding"):
                formal.freeze_cohort(item, events=[], admissions=[], outcomes=[], cohort_root=Path(tmp),
                    now=START + timedelta(days=9), trial_receipts=changed)

    def test_freeze_only_mode_never_creates_an_evaluation_or_runs_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = {key: Path(tmp) / key for key in ("challenger", "experience", "admission", "outcome", "output", "frames")}
            for root in roots.values():
                root.mkdir()
            item, event = challenger(), event_at()
            learner.persist(roots["challenger"], item)
            item = json.loads((roots["challenger"] / f"{item['challenger_id']}.json").read_text())
            (roots["experience"] / "event.json").write_text(json.dumps(event))
            (roots["admission"] / "admission.json").write_text(json.dumps(admitted(event)))
            (roots["frames"] / "frame.json").write_text(json.dumps(native_frames(event)[0]))
            with patch.object(formal, "evaluate_cash", side_effect=AssertionError("formal evaluation during freeze")):
                result = formal.run(challenger_root=roots["challenger"], experience_root=roots["experience"],
                    admission_root=roots["admission"], outcome_root=roots["outcome"], output_root=roots["output"],
                    freeze_cohorts_only=True, trial_receipts=trial_receipts(item), now=START + timedelta(hours=2), replay_frame_root=roots["frames"])
            self.assertEqual(result["frozen_members"], 1)
            self.assertFalse((roots["output"] / f"{item['challenger_id']}.json").exists())
            self.assertTrue((roots["output"] / ".cohorts" / item["challenger_id"] / "members" / "0001.json").exists())

    def test_tournament_can_start_predeclared_future_trials_without_replay_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_root, challenger_root = root / "candidates", root / "challengers"
            candidate_root.mkdir()
            for item in candidates():
                (candidate_root / f"{item['candidate_id']}.json").write_text(json.dumps(item))
            promotion_path = root / "promotion.json"
            promotion_path.write_text("{}")
            result = tournament.run(candidate_root=candidate_root, experience_root=root / "events", admission_root=root / "admissions",
                outcome_root=root / "outcomes", promotion_config_path=promotion_path, challenger_root=challenger_root,
                tournament_root=root / "tournament")
            self.assertEqual(result["robust_winners"], 0)
            self.assertEqual(result["challengers_created"], 2)
            self.assertEqual(len(list((challenger_root / ".test-budget").glob("*.json"))), 2)

    def test_session_horizon_requires_actual_close_receipts_not_elapsed_days(self):
        event = event_at()
        source_sha = event["source"]["payload_sha256"]
        first = {"observed_at": event["decision_at"], "recorded_at": event["decision_at"],
                 "opportunities": {"US": {"opportunity_sha256": source_sha}}, "initial_state": {"markets": {}},
                 "bridge_config": {"schema_version": "test"}, "frame_sha256": "first-frame"}
        def closing(day):
            stamp = START + timedelta(days=day, hours=10)
            return {"observed_at": stamp.isoformat(), "recorded_at": (stamp + timedelta(minutes=1)).isoformat(),
                    "market_sessions": {"US": {"regular_start": (stamp - timedelta(hours=6)).isoformat(),
                        "regular_end": stamp.isoformat(), "state": "REGULAR_CLOSED", "source": "provider-periods",
                        "date": stamp.date().isoformat()}}}
        one, two = closing(0), closing(1)
        self.assertIsNone(exact.production_episode_input(event, [first, one], horizon_sessions=2))
        result = exact.production_episode_input(event, [first, one, two], horizon_sessions=2)
        self.assertEqual(result["horizon_completed_sessions"], 2)
        broken = deepcopy(two)
        broken["market_sessions"]["US"]["source"] = None
        self.assertIsNone(exact.production_episode_input(event, [first, one, broken], horizon_sessions=2))
        self.assertIsNone(exact.production_episode_input(event, [first, one, one], horizon_sessions=2))

    def test_generic_evaluator_cannot_issue_a_cash_formal_verdict(self):
        with self.assertRaisesRegex(contracts.ContractError, "prospective cohort"):
            generic.evaluate_samples(challenger(), [])

    def test_later_same_day_cash_margin_source_survives_earlier_nonmargin_scan(self):
        first = event_at(START + timedelta(hours=1), score=50)
        margin = event_at(START + timedelta(hours=2), score=54.5)
        self.assertFalse(exact.cash_edge_eligible(first, candidates()[0]))
        self.assertTrue(exact.cash_edge_eligible(margin, candidates()[0]))
        self.assertFalse(first["selected"])
        self.assertFalse(margin["selected"])
        def ingest(event, root):
            context = event["candidate_state"]["decision_context"]
            return experience.ingest(context["opportunity_snapshot"], context["evidence_snapshot"], root=root)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(ingest(first, root)["events_written"], 1)
            first_path = next(iter(store.iter_event_files(root)))
            original_first = first_path.read_bytes()
            self.assertEqual(ingest(margin, root)["events_written"], 1)
            self.assertEqual(first_path.read_bytes(), original_first)
            before = {path.name: path.read_bytes() for path in store.iter_event_files(root)}
            repeated = ingest(margin, root)
            self.assertEqual(repeated["events_written"], 0)
            self.assertEqual(repeated["events_skipped_daily_duplicate"], 0)
            self.assertEqual(repeated["events_skipped_source_duplicate"], 1)
            self.assertEqual({path.name: path.read_bytes() for path in store.iter_event_files(root)}, before)
            persisted = [json.loads(path.read_text(encoding="utf-8")) for path in store.iter_event_files(root)]
            self.assertEqual(len(persisted), 2)
            self.assertEqual(sum(exact.cash_edge_eligible(row, candidates()[0]) for row in persisted), 1)


if __name__ == "__main__":
    unittest.main()
