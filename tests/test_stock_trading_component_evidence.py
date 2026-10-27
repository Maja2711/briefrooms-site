from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import stock_trading_component_evidence as evidence
from scripts import stock_trading_portfolio as portfolio
from scripts import stock_trading_v2_admission_ledger as admission
from scripts import stock_trading_v2_challenger_factory as factory
from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_exact_challenger_eval as formal
from scripts import stock_trading_v2_opportunity_engine as opportunity
from scripts import stock_trading_v2_opportunity_experience as experience
from scripts import stock_trading_v2_production_bridge as bridge
from scripts import stock_trading_v2_production_replay as replay
from scripts import stock_trading_v2_replay_tournament as tournament
from tests.test_stock_trading_portfolio_evaluation import policy as production_policy
from tests.test_stock_trading_v2_opportunity_experience import evidence_snapshot


UTC = timezone.utc
START = datetime(2026, 9, 17, 15, tzinfo=UTC)
MANIFEST = {"revision": 7, "components": {"entry": {"version": "entry-7"}}}


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def rehash(value: dict, field: str) -> dict:
    value.pop(field, None)
    value[field] = contracts.payload_sha256(value)
    return value


def make_challenger(policy: dict) -> dict:
    candidate = factory._cash_edge_candidates(
        hypothesis={"component": "cash_admission_margin", "market": "US", "horizon_sessions": 1},
        manifest=MANIFEST, policy=policy,
    )[1]  # Tighter CASH edge avoids a future loss under the actual lifecycle.
    item = tournament.make_challenger(candidate, "diagnostic-only", {}, start_at=(START - timedelta(hours=2)).isoformat())
    family = {"family_id": evidence.FAMILY_ID, "test_index": 1, "challenger_id": item["challenger_id"],
              "protocol_sha256": item["protocol_sha256"], "reserved_at": item["created_at"],
              "lifetime_max_tests": 24, "alpha": 0.05 / 24}
    family["reservation_sha256"] = contracts.payload_sha256(family)
    item["test_family"] = family
    return rehash(item, "challenger_sha256")


def build_full_proof(root: Path) -> tuple[dict, dict, dict]:
    """Thirty real canonical-function replays; no mocked replay or metrics."""
    policy = production_policy()
    policy["champion_engine"] = "v2"
    item = make_challenger(policy)
    challengers = root / "challengers"
    write(challengers / f"{item['challenger_id']}.json", item)
    trials_path = root / "trials.json"
    evidence.initialize_trials(trials_path)
    registered = evidence.register_trials(challengers, policy=policy, manifest=MANIFEST,
        path=trials_path, now=START - timedelta(hours=1))
    if registered["rejected"]:
        raise AssertionError(registered)
    receipts = json.loads(trials_path.read_text(encoding="utf-8"))
    epoch = {"schema_version": admission.EPOCH_SCHEMA_VERSION, "activated_at": (START - timedelta(hours=1)).isoformat(),
             "initial_policy_sha256": contracts.payload_sha256(policy), "immutable": True}
    epoch["epoch_sha256"] = contracts.payload_sha256(epoch)
    events, admissions, native_admissions = [], [], []
    frame_root = root / "frames"
    cohort_root = root / "cohorts"
    config = bridge.load_config()
    for index in range(30):
        moment = START + timedelta(days=7 * index)
        symbol = f"S{index % 6}"
        state = portfolio.empty_state(moment, policy)
        source = evidence_snapshot()
        source["generated_at"] = moment.isoformat()
        source["candidates"] = source["candidates"][:1]
        source["candidate_count"] = 1
        row = source["candidates"][0]
        row.update(symbol=symbol, market_data_symbol=symbol, opportunity_score=55.5, deep_opportunity_score=55.5)
        rehash(source, "evidence_sha256")
        opp = opportunity.compare_opportunities(source, state, opportunity.load_config(), generated_at=moment,
            policy_snapshot=policy, champion_manifest=MANIFEST, bridge_config_snapshot=config)
        event = experience.events_from_snapshots(opp, source, recorded_at=moment.isoformat())[0]
        observed = admission.make_observation(event, policy=policy, epoch=epoch, recorded_at=moment.isoformat())
        events.append(event)
        admissions.append(observed)
        write(root / "stock_trading_v2_experience" / f"{index:03d}.json", event)
        write(root / "stock_trading_v2_admission" / f"{index:03d}.json", observed)
        quote = {"price": 100, "observed_at": moment.isoformat(), "received_at": moment.isoformat(),
                 "decision_at": moment.isoformat(), "provider": "frozen native test quote", "market_state": "REGULAR",
                 "is_realtime": True, "delay_status": "realtime", "capture_age_seconds": 0}
        benchmark = lambda stamp: {"US": {"price": 100, "observed_at": stamp.isoformat(), "provider": "frozen SPY test quote"}}
        captured = replay.capture_frame(frame_root, kind="ADMISSION", observed_at=moment, recorded_at=moment + timedelta(seconds=1),
            policy=policy, initial_state=state, opportunities={"US": opp}, quotes={"US": {symbol: quote}},
            bridge_config=config, runtime={"phase": "FULL"}, manifest=MANIFEST, benchmark=benchmark(moment))
        native_admissions.append(captured["frame"])
        formal.freeze_cohort(item, events=events, admissions=admissions, outcomes=[], cohort_root=cohort_root,
            now=moment + timedelta(minutes=5), trial_receipts=receipts, replay_frames=native_admissions)
        evidence.register_trials(challengers, policy=policy, manifest=MANIFEST, path=trials_path,
            cohort_root=cohort_root, now=moment + timedelta(minutes=6))
        later = moment + timedelta(hours=1)
        bars = [{"start": moment.isoformat(), "observed_at": later.isoformat(), "high": 100.1,
                 "low": 98.9, "close": 99, "provider": "frozen native test bar"}]
        observation = {"source_bars": bars, "snapshot": portfolio.snapshot_from_source_bars(bars, "US", now=later),
                       "closes": [50 + value for value in range(71)], "closes_as_of": later.isoformat(), "atr": 1.0}
        replay.capture_frame(frame_root, kind="LIFECYCLE", observed_at=later, recorded_at=later + timedelta(seconds=1),
            policy=policy, initial_state=state, observations={"US": {symbol: observation}}, benchmark=benchmark(later))
        close = moment.replace(hour=21, minute=1)
        replay.capture_frame(frame_root, kind="SESSION_CLOSE", observed_at=close, recorded_at=close + timedelta(seconds=1),
            policy=policy, initial_state=state, benchmark=benchmark(close), market_sessions={"US": {
                "date": moment.date().isoformat(), "regular_start": moment.replace(hour=14, minute=30).isoformat(),
                "regular_end": moment.replace(hour=21, minute=0).isoformat(),
                "source": "frozen provider currentTradingPeriod", "state": "REGULAR_CLOSED"}})
    frames = [json.loads(path.read_text(encoding="utf-8")) for path in frame_root.rglob("*.json")]
    report = formal.evaluate_cash(item, events=events, admissions=admissions, outcomes=[], cohort_root=cohort_root,
        now=START + timedelta(days=204), replay_frames=frames, trial_receipts=receipts)
    return item, report, {"source_root": root, "frames_root": frame_root, "trials_path": trials_path}


class ProductionEvidenceTests(unittest.TestCase):
    def test_hash_only_zero_sample_pass_is_rejected(self):
        item = make_challenger(production_policy())
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(evidence.EvidenceError, "terminal prospective proof"):
                evidence.validate_proof(item, {"state": "RESEARCH_PASS_AWAITING_MANUAL_ENABLEMENT", "metrics": {"paired_n": 0, "formal_pass": True},
                    "evaluation_sha256": "self-reported-hash"}, source_root=Path(tmp), trials_path=Path(tmp) / "none.json")

    def test_production_trial_indices_cannot_be_reused_after_research_reset(self):
        policy = production_policy()
        first = make_challenger(policy)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence.initialize_trials(root / "trials.json")
            write(root / "challengers" / "first.json", first)
            result = evidence.register_trials(root / "challengers", policy=policy, manifest=MANIFEST,
                path=root / "trials.json", now=START)
            self.assertEqual(result["registered"], [first["challenger_id"]])
            second = deepcopy(first)
            second["challenger_id"] += "-new-generation"
            second["test_family"]["challenger_id"] = second["challenger_id"]
            rehash(second["test_family"], "reservation_sha256")
            rehash(second, "challenger_sha256")
            write(root / "reset-research" / "second.json", second)
            result = evidence.register_trials(root / "reset-research", policy=policy, manifest=MANIFEST,
                path=root / "trials.json", now=START + timedelta(days=1))
            self.assertEqual(result["registered"], [])
            self.assertIn("already reserved", result["rejected"][0]["reason"])
            self.assertEqual(len(evidence.load_trials(root / "trials.json")["trials"]), 1)

    def test_missing_trial_registry_fails_closed_and_explicit_init_never_resets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trials.json"
            with self.assertRaisesRegex(evidence.EvidenceError, "missing.*reset forbidden"):
                evidence.load_trials(path)
            evidence.initialize_trials(path)
            item = make_challenger(production_policy())
            write(Path(tmp) / "challengers" / "first.json", item)
            evidence.register_trials(Path(tmp) / "challengers", policy=production_policy(), manifest=MANIFEST,
                path=path, now=START)
            frozen = path.read_bytes()
            with self.assertRaisesRegex(evidence.EvidenceError, "already initialized"):
                evidence.initialize_trials(path)
            self.assertEqual(path.read_bytes(), frozen)
            path.unlink()
            with self.assertRaisesRegex(evidence.EvidenceError, "missing.*reset forbidden"):
                evidence.register_trials(Path(tmp) / "challengers", policy=production_policy(), manifest=MANIFEST,
                    path=path, now=START + timedelta(days=1))
            self.assertFalse(path.exists())

    def test_full_proof_rejects_changed_current_policy_even_with_same_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            item, report, paths = build_full_proof(Path(tmp))
            policy = deepcopy(item["champion"]["policy_snapshot"])
            self.assertTrue(evidence.validate_proof(item, report, current_policy=policy, **paths)["verified"])
            policy["markets"]["US"]["model_exit_score"] = float(policy["markets"]["US"].get("model_exit_score", 38)) + 1
            with self.assertRaisesRegex(evidence.EvidenceError, "current production policy"):
                evidence.validate_proof(item, report, current_policy=policy, **paths)

    def test_native_first_eligible_episode_cannot_be_omitted_from_research_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            item, report, paths = build_full_proof(Path(tmp))
            policy = item["champion"]["policy_snapshot"]
            moment = START - timedelta(minutes=30)
            state = portfolio.empty_state(moment, policy)
            source = evidence_snapshot()
            source["generated_at"] = moment.isoformat()
            source["candidates"] = source["candidates"][:1]
            source["candidate_count"] = 1
            source["candidates"][0].update(symbol="OMITTED", market_data_symbol="OMITTED", opportunity_score=55.5, deep_opportunity_score=55.5)
            rehash(source, "evidence_sha256")
            config = bridge.load_config()
            opp = opportunity.compare_opportunities(source, state, opportunity.load_config(), generated_at=moment,
                policy_snapshot=policy, champion_manifest=MANIFEST, bridge_config_snapshot=config)
            replay.capture_frame(paths["frames_root"], kind="ADMISSION", observed_at=moment, recorded_at=moment + timedelta(seconds=1),
                policy=policy, initial_state=state, opportunities={"US": opp}, bridge_config=config, runtime={"phase": "FULL"}, manifest=MANIFEST)
            # No matching research event/admission is supplied. Previously the
            # verifier trusted that incomplete research universe and passed.
            with self.assertRaisesRegex(evidence.EvidenceError, "earlier eligible episode"):
                evidence.validate_proof(item, report, **paths)

    def test_selected_native_episode_requires_source_and_admission_and_valid_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            item, report, paths = build_full_proof(Path(tmp))
            for directory in ("stock_trading_v2_experience", "stock_trading_v2_admission"):
                path = paths["source_root"] / directory / "000.json"
                original = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(evidence.EvidenceError, "source event/admission binding"):
                    evidence.validate_proof(item, report, **paths)
                path.write_bytes(original)
            frame_path = next(path for path in paths["frames_root"].rglob("*.json")
                              if json.loads(path.read_text(encoding="utf-8"))["kind"] == "ADMISSION")
            frame = json.loads(frame_path.read_text(encoding="utf-8"))
            original = frame_path.read_bytes()
            frame["policy_sha256"] = "changed"
            write(frame_path, frame)
            with self.assertRaisesRegex(evidence.EvidenceError, "native enrollment frame invalid"):
                evidence.validate_proof(item, report, **paths)
            rehash(frame, "frame_sha256")
            write(frame_path, frame)
            with self.assertRaisesRegex(evidence.EvidenceError, "context/policy/manifest"):
                evidence.validate_proof(item, report, **paths)
            frame_path.write_bytes(original)

    def test_full_proof_reproduces_native_production_lifecycle_without_mocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            item, report, paths = build_full_proof(Path(tmp))
            self.assertEqual(report["state"], "RESEARCH_PASS_AWAITING_MANUAL_ENABLEMENT", report["metrics"])
            result = evidence.validate_proof(item, report, **paths)
            self.assertTrue(result["verified"])
            self.assertEqual(result["paired_n"], 30)
            # The actual Champion buys the weak margin and incurs a loss/cost;
            # the exact +1 CASH alternative leaves the slot and NAV in cash.
            sample = report["formal_samples"][0]
            self.assertLess(sample["champion_net_return_percent"], 0)
            self.assertEqual(sample["challenger_net_return_percent"], 0)
            self.assertGreater(sample["incremental_net_return_percent"], 0)

    def test_enrollment_must_precede_sourced_close_not_later_close_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            item, report, paths = build_full_proof(Path(tmp))
            self.assertTrue(evidence.validate_proof(item, report, **paths)["verified"])
            registry = json.loads(paths["trials_path"].read_text(encoding="utf-8"))
            proof = report["formal_proof"]
            member = proof["cohort"]["members"][0]
            member_key = item["challenger_id"] + ":" + member["member_sha256"]
            source_close = START.replace(hour=21, minute=0)
            self.assertLess(source_close, evidence._time(report["formal_samples"][0]["episode_end_at"]))
            for first_seen in (source_close, source_close + timedelta(seconds=30)):
                late = deepcopy(registry)
                late["members"][member_key]["registered_at"] = first_seen.isoformat()
                write(paths["trials_path"], late)
                with self.assertRaisesRegex(evidence.EvidenceError, "main did not receive"):
                    evidence.validate_proof(item, report, **paths)

            forged = deepcopy(report)
            forged_proof = forged["formal_proof"]
            forged_member = forged_proof["cohort"]["members"][0]
            forged_member["frozen_at"] = source_close.isoformat()
            rehash(forged_member, "member_sha256")
            forged_proof["source_refs"] = deepcopy(forged_proof["cohort"]["members"])
            rehash(forged_proof["cohort"], "cohort_sha256")
            forged_proof["cohort_sha256"] = forged_proof["cohort"]["cohort_sha256"]
            rehash(forged_proof, "proof_sha256")
            rehash(forged, "evaluation_sha256")
            late = deepcopy(registry)
            receipt = late["members"].pop(member_key)
            receipt.update(member_sha256=forged_member["member_sha256"], registered_at=(source_close + timedelta(seconds=30)).isoformat())
            late["members"][item["challenger_id"] + ":" + forged_member["member_sha256"]] = receipt
            write(paths["trials_path"], late)
            with self.assertRaisesRegex(evidence.EvidenceError, "cohort was frozen after"):
                evidence.validate_proof(item, forged, **paths)

    def test_full_proof_rejects_missing_late_changed_receipts_and_rewritten_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            item, report, paths = build_full_proof(Path(tmp))
            self.assertEqual(report["state"], "RESEARCH_PASS_AWAITING_MANUAL_ENABLEMENT", report["metrics"])
            path = paths["trials_path"]
            original = json.loads(path.read_text(encoding="utf-8"))
            key = item["challenger_id"]
            member = report["formal_proof"]["cohort"]["members"][0]
            member_key = key + ":" + member["member_sha256"]
            cases = {}
            missing = deepcopy(original)
            missing["trials"].pop(key)
            cases["missing trial"] = missing
            changed = deepcopy(original)
            changed["trials"][key]["challenger_sha256"] = "changed-experiment"
            cases["changed trial"] = changed
            late = deepcopy(original)
            late["trials"][key]["registered_at"] = (START + timedelta(minutes=1)).isoformat()
            cases["late trial"] = late
            unseen = deepcopy(original)
            unseen["members"].pop(member_key)
            cases["member unseen before outcome"] = unseen
            post_outcome = deepcopy(original)
            post_outcome["members"][member_key]["registered_at"] = report["formal_samples"][0]["episode_end_at"]
            cases["member first seen at outcome"] = post_outcome
            for label, registry in cases.items():
                with self.subTest(label=label):
                    write(path, registry)
                    with self.assertRaises(evidence.EvidenceError):
                        evidence.validate_proof(item, report, **paths)
            write(path, original)
            source = paths["source_root"] / "stock_trading_v2_experience" / "000.json"
            original_source = source.read_bytes()
            rewritten = json.loads(original_source)
            rewritten["symbol"] = "REWRITTEN"
            rehash(rewritten, "event_sha256")
            write(source, rewritten)
            with self.assertRaises(evidence.EvidenceError):
                evidence.validate_proof(item, report, **paths)
            source.write_bytes(original_source)
            forged = deepcopy(report)
            forged["formal_proof"]["paired_samples"][0]["incremental_net_return_percent"] = 50
            rehash(forged["formal_proof"], "proof_sha256")
            rehash(forged, "evaluation_sha256")
            with self.assertRaisesRegex(evidence.EvidenceError, "does not reproduce"):
                evidence.validate_proof(item, forged, **paths)


if __name__ == "__main__":
    unittest.main()
