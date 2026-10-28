"""Prospective protocol v2: continuous native books and a frozen calendar."""
from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts import stock_trading_component_evidence as evidence
from scripts import stock_trading_component_bound_promotion as binding
from scripts import stock_trading_portfolio as portfolio
from scripts import stock_trading_v2_admission_ledger as admission
from scripts import stock_trading_v2_challenger_factory as factory
from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_exact_challenger_eval as formal
from scripts import stock_trading_v2_exact_replay as exact
from scripts import stock_trading_v2_opportunity_engine as opportunity
from scripts import stock_trading_v2_opportunity_experience as experience
from scripts import stock_trading_v2_production_replay as replay
from scripts import stock_trading_v2_replay_tournament as tournament
from tests.test_stock_trading_portfolio_evaluation import policy as production_policy, bridge_config
from tests.test_stock_trading_v2_opportunity_experience import evidence_snapshot

UTC = timezone.utc
START = datetime(2025, 1, 6, 15, tzinfo=UTC)
MANIFEST = {"revision": 7, "components": {"entry": {"version": "entry-7"}}}


def rehash(value, key):
    value.pop(key, None)
    value[key] = contracts.payload_sha256(value)
    return value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def fixture(root: Path, *, horizon=2, complete=True):
    policy, config = production_policy(), bridge_config()
    candidate = factory._cash_edge_candidates(hypothesis={"component": "cash_admission_margin", "market": "US", "horizon_sessions": horizon}, manifest=MANIFEST, policy=policy)[1]
    item = tournament.make_challenger(candidate, "descriptive", {}, start_at=(START - timedelta(hours=3)).isoformat())
    family = {"family_id": evidence.FAMILY_ID, "test_index": 1, "challenger_id": item["challenger_id"],
              "protocol_sha256": item["protocol_sha256"], "reserved_at": item["created_at"], "lifetime_max_tests": 24, "alpha": .05 / 24}
    item["test_family"] = rehash(family, "reservation_sha256")
    rehash(item, "challenger_sha256")
    write(root / "challengers" / (item["challenger_id"] + ".json"), item)
    evidence.initialize_trials(root / "trials.json")
    evidence.register_trials(root / "challengers", policy=policy, manifest=MANIFEST, path=root / "trials.json", now=START - timedelta(hours=2))
    receipts = evidence.load_trials(root / "trials.json")
    epoch = {"schema_version": admission.EPOCH_SCHEMA_VERSION, "activated_at": (START - timedelta(hours=2)).isoformat(), "initial_policy_sha256": contracts.payload_sha256(policy), "immutable": True}
    rehash(epoch, "epoch_sha256")
    sessions = []
    day = START.date()
    while len(sessions) < 60:
        if day.weekday() < 5:
            opening = datetime.combine(day, datetime.min.time(), ZoneInfo("America/New_York")).replace(hour=9, minute=30)
            closing = opening.replace(hour=16, minute=0)
            sessions.append({"date": day.isoformat(), "regular_start": opening.isoformat(), "regular_end": closing.isoformat()})
        day += timedelta(days=1)
    calendar = {"schema_version": "stock-trading-session-calendar-v1", "market": "US", "timezone": "America/New_York",
        "source": "synthetic predeclared exchange schedule (fixture)", "source_url": "https://example.test/frozen-schedule",
        "calendar_id": "test-fixed-60-sessions", "observed_at": (START - timedelta(hours=2)).isoformat(),
        "received_at": (START - timedelta(hours=1)).isoformat(), "coverage_start": (START - timedelta(days=1)).isoformat(),
        "coverage_end": (START + timedelta(days=120)).isoformat(), "sessions": sessions}
    rehash(calendar, "calendar_sha256")
    state = portfolio.empty_state(START, policy)
    frames, events, admissions = [], [], []
    for index, session in enumerate(sessions if complete else sessions[:1]):
        opening, close = exact.parse_dt(session["regular_start"]), exact.parse_dt(session["regular_end"])
        moment = opening + timedelta(minutes=30)
        symbol = f"S{index:03d}"
        source = evidence_snapshot()
        source.update(generated_at=moment.isoformat(), source_frontier_generated_at=(moment - timedelta(minutes=5)).isoformat(), candidate_count=1)
        source["candidates"] = source["candidates"][:1]
        row = source["candidates"][0]
        row.update(symbol=symbol, market_data_symbol=symbol, opportunity_score=55.5, deep_opportunity_score=55.5)
        row["evidence"][0]["published_at"] = (moment - timedelta(hours=1)).isoformat()
        row["freshness"]["latest_session"] = row["features"]["latest_session"] = (moment - timedelta(days=1)).date().isoformat()
        rehash(source, "evidence_sha256")
        opp = opportunity.compare_opportunities(source, state, opportunity.load_config(), generated_at=moment,
            policy_snapshot=policy, champion_manifest=MANIFEST, bridge_config_snapshot=config)
        event = experience.events_from_snapshots(opp, source, recorded_at=moment.isoformat())[0]
        baseline = admission.make_observation(event, policy=policy, epoch=epoch, recorded_at=moment.isoformat())
        events.append(event)
        admissions.append(baseline)
        write(root / "stock_trading_v2_experience" / f"{index:03d}.json", event)
        write(root / "stock_trading_v2_admission" / f"{index:03d}.json", baseline)
        benchmark = lambda stamp: {"US": {"price": 100, "observed_at": stamp.isoformat(), "provider": "frozen test SPY"}}
        quote = {"price": 100, "observed_at": moment.isoformat(), "received_at": moment.isoformat(), "decision_at": moment.isoformat(), "provider": "frozen test", "market_state": "REGULAR", "is_realtime": True, "delay_status": "realtime", "capture_age_seconds": 0}
        frame = replay.capture_frame(root / "frames", kind="ADMISSION", observed_at=moment, recorded_at=moment + timedelta(seconds=1), policy=policy, initial_state=state,
            opportunities={"US": opp}, quotes={"US": {symbol: quote}}, bridge_config=config, runtime={"phase": "FULL"}, manifest=MANIFEST,
            benchmark=benchmark(moment), session_calendar={"US": calendar})["frame"]
        frames.append(frame)
        if index == 0:
            formal.freeze_cohort(item, events=events, admissions=admissions, outcomes=[], cohort_root=root / "cohorts", now=moment + timedelta(minutes=1), trial_receipts=receipts, replay_frames=frames)
            evidence.register_trials(root / "challengers", policy=policy, manifest=MANIFEST, path=root / "trials.json", cohort_root=root / "cohorts", now=moment + timedelta(minutes=2))
        if not complete:
            break
        later = moment + timedelta(hours=1)
        bars = [{"start": moment.isoformat(), "observed_at": later.isoformat(), "high": 100.1, "low": 90.0, "close": 98.0, "provider": "frozen raw test bar"}]
        observation = {"source_bars": bars, "snapshot": portfolio.snapshot_from_source_bars(bars, "US", now=later),
            "closes": [50 + value for value in range(71)], "closes_as_of": later.isoformat(), "atr": 1.0}
        frames.append(replay.capture_frame(root / "frames", kind="LIFECYCLE", observed_at=later, recorded_at=later + timedelta(seconds=1), policy=policy, initial_state=state,
            observations={"US": {symbol: observation}}, benchmark=benchmark(later))["frame"])
        received = close + timedelta(minutes=1)
        frames.append(replay.capture_frame(root / "frames", kind="SESSION_CLOSE", observed_at=received, recorded_at=received + timedelta(seconds=1), policy=policy, initial_state=state,
            benchmark=benchmark(received), close_observations={"US": {}}, market_sessions={"US": {**session, "source": "frozen provider regular period", "provider": "frozen native test provider", "state": "CLOSED", "closed": True, "received_at": received.isoformat()}})["frame"])
    return {"item": item, "events": events, "admissions": admissions, "frames": frames, "receipts": receipts,
            "policy": policy, "sessions": sessions, "calendar": calendar,
            "paths": {"source_root": root, "frames_root": root / "frames", "trials_path": root / "trials.json"}}


def evaluate(data, root, **kwargs):
    return formal.evaluate_cash(data["item"], events=data["events"], admissions=data["admissions"], outcomes=[],
        cohort_root=root / "cohorts", now=kwargs.pop("now", exact.parse_dt(data["sessions"][-1]["regular_end"]) + timedelta(hours=1)),
        trial_receipts=data["receipts"], replay_frames=kwargs.pop("frames", data["frames"]), **kwargs)


class ContinuousProtocolTests(unittest.TestCase):
    def test_new_protocol_is_distinct_and_old_contract_retains_168h(self):
        policy = production_policy()
        candidate = factory._cash_edge_candidates(hypothesis={"component": "cash_admission_margin", "market": "US", "horizon_sessions": 2}, manifest=MANIFEST, policy=policy)[0]
        old = tournament.make_challenger(candidate, "x", {"cash_protocol_version": "legacy_v1"}, start_at=START.isoformat())
        new = tournament.make_challenger(candidate, "x", {}, start_at=START.isoformat())
        self.assertNotEqual(old["challenger_id"], new["challenger_id"])
        self.assertEqual(old["protocol"]["block_hours"], 168)
        self.assertEqual(new["protocol"]["fixed_regular_sessions"], 60)
        self.assertEqual(new["protocol"]["fixed_paired_n"], 30)
        self.assertEqual(new["protocol"]["lifetime_max_tests"], 24)
        self.assertEqual(new["protocol"]["alpha"], old["protocol"]["alpha"])

    def test_calendar_absence_late_calendar_and_future_frames_remain_pending(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = fixture(root, complete=False)
            event = data["events"][0]
            native = deepcopy(data["frames"])
            native[0].pop("session_calendar")
            self.assertIn("calendar_missing", exact.continuous_trial_input(event, native, protocol=data["item"]["protocol"])["reason"])
            native = deepcopy(data["frames"])
            calendar = native[0]["session_calendar"]["US"]
            calendar["received_at"] = (START + timedelta(seconds=1)).isoformat()
            rehash(calendar, "calendar_sha256")
            self.assertEqual("PENDING", exact.continuous_trial_input(event, native, protocol=data["item"]["protocol"])["status"])
            self.assertEqual("PENDING", exact.continuous_trial_input(event, data["frames"], protocol=data["item"]["protocol"], as_of=START)["status"])

    def test_continuous_actual_production_books_have_no_episode_resets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = fixture(root)
            report = evaluate(data, root)
            self.assertEqual("RESEARCH_PASS_AWAITING_MANUAL_ENABLEMENT", report["state"], report["metrics"])
            self.assertTrue(evidence.validate_proof(data["item"], report, current_policy=data["policy"], **data["paths"])["verified"])
            samples = report["formal_samples"]
            pair = samples[-1]["continuous_replay"]
            self.assertEqual(30, len(samples))
            self.assertEqual(60, len(portfolio.market_state(pair["champion"]["final_state"], "US")["closed_positions"]))
            self.assertEqual([], portfolio.market_state(pair["challenger"]["final_state"], "US")["closed_positions"])
            self.assertEqual(1, len({row["production_replay"]["input_sha256"] for row in samples}))
            self.assertFalse(report["metrics"]["iid_or_unconditional_mean_claim"])
            self.assertEqual("DESCRIPTIVE_SENSITIVITY_ONLY", report["metrics"]["descriptive_moving_block_bootstrap"]["role"])
            raw_loss = deepcopy(samples)
            raw_loss[-1]["continuous_replay"]["market_results"]["US"]["net_delta_percent"] = -1
            economic_gate = formal.formal_metrics(raw_loss, data["item"]["protocol"], seed="economic-gate")
            self.assertTrue(economic_gate["checks"]["weekly_conditional_mean_e_value"])
            self.assertFalse(economic_gate["checks"]["positive_unclipped_net"])
            self.assertFalse(economic_gate["formal_pass"])
            one_symbol = deepcopy(samples)
            contributions = one_symbol[-1]["continuous_replay"]["champion"]["period_position_contributions_by_market"]["US"]
            contributions[0]["net_pnl_amount"] = sum(row["net_pnl_amount"] for row in contributions)
            del contributions[1:]
            concentration_gate = formal.formal_metrics(one_symbol, data["item"]["protocol"], seed="concentration-gate")
            self.assertTrue(concentration_gate["checks"]["weekly_conditional_mean_e_value"])
            self.assertFalse(concentration_gate["checks"]["symbol_concentration"])
            self.assertFalse(concentration_gate["formal_pass"])
            early = evaluate(data, root, now=START + timedelta(minutes=5))
            self.assertEqual("COLLECTING_FRESH_HOLDOUT", early["state"])
            self.assertEqual(0, len(early["formal_samples"]))
            self.assertEqual("waiting_for_all_predeclared_session_closes", early["metrics"]["coverage"]["reason"])
            # An adjusted PASS flag cannot replace main's recomputed statistics.
            forged = deepcopy(report)
            forged["metrics"]["e_value"] += 1
            forged["formal_proof"]["metrics"] = forged["metrics"]
            rehash(forged["formal_proof"], "proof_sha256")
            with self.assertRaisesRegex(evidence.EvidenceError, "statistical/economic gate"):
                evidence.validate_proof(data["item"], forged, current_policy=data["policy"], **data["paths"])
            # Main's receipt must predate the FIRST close, even for horizon 2.
            registry_path = data["paths"]["trials_path"]
            registered = json.loads(registry_path.read_text())
            frozen_registry = registry_path.read_bytes()
            next(iter(registered["members"].values()))["registered_at"] = data["sessions"][0]["regular_end"]
            write(registry_path, registered)
            with self.assertRaisesRegex(evidence.EvidenceError, "first sourced close"):
                evidence.validate_proof(data["item"], report, current_policy=data["policy"], **data["paths"])
            registry_path.write_bytes(frozen_registry)
            # Main rejects a changed source tape even when a report still says PASS.
            first_close = next(path for path in (root / "frames").rglob("*.json") if json.loads(path.read_text())["kind"] == "SESSION_CLOSE")
            first_close.unlink()
            with self.assertRaisesRegex(evidence.EvidenceError, "calendar/tape"):
                evidence.validate_proof(data["item"], report, current_policy=data["policy"], **data["paths"])

    def test_one_session_requires_the_same_sixty_session_tape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = fixture(root, horizon=1)
            report = evaluate(data, root)
            self.assertTrue(report["metrics"]["formal_pass"], report["metrics"])
            self.assertEqual(60, len(report["formal_samples"]))
            self.assertEqual(data["sessions"][-1]["regular_end"], report["formal_samples"][-1]["sourced_regular_end"])
            # Closing reports remain byte-identical after later input/results.
            write(root / "evaluation" / (data["item"]["challenger_id"] + ".json"), report)
            frozen = next((root / "evaluation").glob("*.json")).read_bytes()
            formal.run(challenger_root=root / "challengers", experience_root=root / "stock_trading_v2_experience",
                admission_root=root / "stock_trading_v2_admission", outcome_root=root / "outcomes", output_root=root / "evaluation",
                replay_frame_root=root / "frames", now=START + timedelta(days=200), trial_receipts=data["receipts"])
            self.assertEqual(frozen, next((root / "evaluation").glob("*.json")).read_bytes())

    def test_earlier_native_anchor_cannot_be_deleted_or_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = fixture(root, complete=False)
            # Even if the research source disappears, freeze-only cannot choose
            # a later attractive source instead of the first native decision.
            other_root = root / "new-cohort"
            cohort = formal.freeze_cohort(data["item"], events=[], admissions=[], outcomes=[], cohort_root=other_root,
                now=START + timedelta(minutes=5), trial_receipts=data["receipts"], replay_frames=data["frames"])
            self.assertEqual([], cohort["members"])
            self.assertFalse(cohort["membership_closed"])
            late = formal.freeze_cohort(data["item"], events=data["events"], admissions=data["admissions"], outcomes=[], cohort_root=root / "late-cohort",
                now=START + timedelta(hours=25), trial_receipts=data["receipts"], replay_frames=data["frames"])
            self.assertEqual("CENSORED_LATE_ENROLLMENT", late["members"][0]["enrollment_status"])

    def test_observation_deadline_censors_without_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = fixture(root, complete=False)
            report = evaluate(data, root, now=START + timedelta(days=181))
            self.assertEqual("FORMAL_HOLDOUT_FAIL", report["state"])
            self.assertEqual(1, len(report["formal_proof"]["cohort"]["members"]))

    def test_missing_close_is_not_repaired_after_the_next_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = fixture(root, complete=False)
            session = data["sessions"][0]
            late = exact.parse_dt(session["regular_end"]) + timedelta(days=2)
            frame = replay.capture_frame(root / "late-frame", kind="SESSION_CLOSE", observed_at=late, recorded_at=late,
                policy=data["policy"], initial_state={}, close_observations={"US": {}}, market_sessions={"US": {**session,
                    "source": "delayed frozen provider regular period", "state": "REGULAR_CLOSED"}})["frame"]
            tape = exact.continuous_trial_input(data["events"][0], data["frames"] + [frame], protocol=data["item"]["protocol"])
            self.assertEqual("PENDING", tape["status"])
            self.assertEqual("session_close_receipt_after_next_session_or_deadline", tape["reason"])
            session = {**session, "regular_end": (exact.parse_dt(session["regular_end"]) + timedelta(minutes=5)).isoformat()}
            frame = replay.capture_frame(root / "disagreement", kind="SESSION_CLOSE", observed_at=late, recorded_at=late,
                policy=data["policy"], initial_state={}, close_observations={"US": {}}, market_sessions={"US": {**session,
                    "source": "different provider close", "state": "REGULAR_CLOSED"}})["frame"]
            tape = exact.continuous_trial_input(data["events"][0], data["frames"] + [frame], protocol=data["item"]["protocol"])
            self.assertEqual("provider_calendar_disagreement", tape["reason"])

    def test_positive_rows_without_a_continuous_book_are_not_evidence(self):
        protocol = exact.continuous_cash_protocol(2)
        # No exact continuous book or attribution: a list of positive rows is insufficient.
        samples = [{"market": "US", "block_id": str(index), "incremental_net_return_percent": 1,
                    "production_replay": {"input_sha256": "x"}, "benchmark_return_percent": 0,
                    "calendar_sha256": "x", "anchor_decision_at": START.isoformat(),
                    "sourced_regular_end": (START + timedelta(days=84)).isoformat()} for index in range(30)]
        metrics = formal.formal_metrics(samples, protocol, seed="x")
        self.assertFalse(metrics["formal_pass"])
        self.assertFalse(metrics["checks"]["continuous_single_pair"])
        self.assertFalse(metrics["checks"]["unique_differential_symbols"])

    def test_new_execution_label_needs_exact_frozen_protocol(self):
        item = {"protocol": exact.continuous_cash_protocol(2), "horizon_sessions": 2}
        item["protocol_sha256"] = contracts.payload_sha256(item["protocol"])
        report = {"formal_proof": {"protocol": deepcopy(item["protocol"]), "protocol_sha256": item["protocol_sha256"]}}
        self.assertEqual("continuous_production_lifecycle_v2", binding.expected_execution_semantics(item, report))
        item["protocol"]["fixed_regular_sessions"] = 10
        with self.assertRaises(binding.EvidenceBindingError):
            binding.expected_execution_semantics(item, report)


if __name__ == "__main__":
    unittest.main()
