"""Production-owned verification of prospective CASH-edge experiment evidence.

Research code is never loaded from the submitted branch. These imports resolve
only to the reviewed production checkout. Native replay frames belong to main;
the report's self-declared PASS, metrics and nested replay are not authority.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/investments"
TRIALS_PATH = DATA / "stock_trading_component_trials.json"
FAMILY_ID = "stock-trading-v2-cash-edge-lifetime-v1"
MAX_TESTS = 24


class EvidenceError(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _time(value: Any) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise EvidenceError("evidence timestamps must be timezone-aware")
    return stamp.astimezone(timezone.utc)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvidenceError(f"evidence is not an object: {path.name}")
    return value


def _hash_field(value: Mapping[str, Any], key: str) -> None:
    body = dict(value)
    stored = body.pop(key, None)
    if not stored or stored != digest(body):
        raise EvidenceError(f"evidence integrity mismatch: {key}")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise EvidenceError(f"boolean is not a numeric {label}")
    number = float(value)
    if not math.isfinite(number):
        raise EvidenceError(f"nonfinite {label}")
    return number


def _modules():
    from scripts import stock_trading_v2_exact_challenger_eval as evaluation
    from scripts import stock_trading_v2_exact_replay as exact
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_admission_ledger as ledger
    return evaluation, exact, contracts, ledger


def validate_protocol(protocol: Mapping[str, Any]) -> None:
    if (protocol.get("schema_version") != "stock-trading-cash-edge-protocol-v1"
            or protocol.get("family_id") != FAMILY_ID or protocol.get("lifetime_max_tests") != MAX_TESTS
            or protocol.get("maximum_censored") != 0 or protocol.get("single_formal_look") is not True
            or protocol.get("execution_semantics") != "production_lifecycle_v1"
            or protocol.get("membership") != "chronological_eligible_episode_before_outcome"
            or protocol.get("outcome_missing") != "WAIT_THEN_TERMINAL_CENSOR_NO_REPLACEMENT"):
        raise EvidenceError("unapproved prospective experiment protocol")
    bounds = (("fixed_paired_n", 30, None), ("block_hours", 168, None),
              ("bootstrap_draws", 20000, None), ("min_unique_symbols", 5, None),
              ("min_span_days", 10, None), ("max_single_positive_share", 0, 0.5),
              ("max_drawdown_increase_pct", 0, 0), ("min_mean_incremental_pct", 0, None),
              ("alpha", 0, 0.05 / MAX_TESTS), ("enrollment_deadline_hours", 0, 24))
    for key, low, high in bounds:
        value = _number(protocol.get(key), key)
        if value < low or (high is not None and value > high) or (key in {"alpha", "enrollment_deadline_hours", "max_single_positive_share"} and value <= 0):
            raise EvidenceError(f"protocol weakens production bound: {key}")


def initialize_trials(path: Path) -> dict[str, Any]:
    """Explicit one-time bootstrap for a new registry, never a runtime reset."""
    state = {"schema_version": "stock-trading-component-trials-v1", "family_id": FAMILY_ID,
             "lifetime_max_tests": MAX_TESTS, "trials": {}, "members": {}}
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise EvidenceError("production lifetime trial registry already initialized") from exc
    return state


def load_trials(path: Path = TRIALS_PATH) -> dict[str, Any]:
    if not path.exists():
        raise EvidenceError("production lifetime trial registry missing; automatic reset forbidden")
    payload = _read(path)
    if payload.get("schema_version") != "stock-trading-component-trials-v1" or payload.get("family_id") != FAMILY_ID or payload.get("lifetime_max_tests") != MAX_TESTS:
        raise EvidenceError("invalid production lifetime trial registry")
    if not isinstance(payload.get("trials"), dict) or not isinstance(payload.get("members", {}), dict):
        raise EvidenceError("invalid production lifetime trial registry entries")
    used: dict[int, str] = {}
    for key, row in payload.get("trials", {}).items():
        index = int(row["test_index"])
        if not 1 <= index <= MAX_TESTS or index in used or row.get("challenger_id") != key:
            raise EvidenceError("reused or invalid production trial index")
        used[index] = key
    return payload


def register_trials(challengers: Path, *, policy: Mapping[str, Any], manifest: Mapping[str, Any],
                    path: Path = TRIALS_PATH, now: datetime | None = None,
                    cohort_root: Path | None = None) -> dict[str, Any]:
    """Reserve every observed trial, including failures, before formal members.

    Clearing the research budget or changing code/Champion never clears this
    production-owned lifetime registry. Registration grants no trade authority.
    """
    from scripts import stock_trading_component_champion as champion
    state = load_trials(path)
    used = {int(row["test_index"]): key for key, row in state["trials"].items()}
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    added, rejected = [], []
    for item in sorted(challengers.glob("*.json")) if challengers.exists() else []:
        ch = _read(item)
        if (ch.get("exact_replay") or {}).get("adapter") != "cash_edge_v2":
            continue
        key = str(ch.get("challenger_id") or "")
        try:
            _hash_field(ch, "challenger_sha256")
            validate_protocol(ch.get("protocol") or {})
            prior = state["trials"].get(key)
            if prior:
                if prior["challenger_sha256"] != ch["challenger_sha256"]:
                    raise EvidenceError("registered experiment was mutated")
                continue
            family = ch.get("test_family") or {}
            index = int(family.get("test_index") or family.get("index") or 0)
            if family.get("family_id") != FAMILY_ID or not 1 <= index <= MAX_TESTS or index in used:
                raise EvidenceError("lifetime trial index missing, exhausted or already reserved")
            candidate = ch.get("production_candidate") or {}
            component = str(candidate.get("component") or "")
            if component not in champion.COMPONENTS or int(candidate.get("base_manifest_revision") or 0) != manifest["revision"]:
                raise EvidenceError("trial baseline manifest is stale")
            if (ch.get("champion") or {}).get("policy_sha256") != digest(policy):
                raise EvidenceError("trial policy differs from current production")
            if _time(ch["created_at"]) > _time(stamp):
                raise EvidenceError("trial creation is in the future")
            state["trials"][key] = {
                "challenger_id": key, "challenger_sha256": ch["challenger_sha256"],
                "family_id": FAMILY_ID, "test_index": index, "registered_at": stamp,
                "policy_sha256": digest(policy), "protocol_sha256": ch["protocol_sha256"],
            }
            used[index] = key
            added.append(key)
        except (EvidenceError, ValueError, KeyError, TypeError) as exc:
            rejected.append({"challenger_id": key, "reason": str(exc)})
    # A research timestamp is not proof of preregistration. Main records its
    # own first observation of each immutable member, before its future close.
    member_receipts = state.setdefault("members", {})
    enrolled = []
    for item in sorted(cohort_root.glob("*/members/*.json")) if cohort_root and cohort_root.exists() else []:
        try:
            row = _read(item)
            _hash_field(row, "member_sha256")
            challenger_id = item.parent.parent.name
            trial = state["trials"].get(challenger_id)
            if not trial or _time(row["decision_at"]) < _time(trial["registered_at"]):
                continue
            if _time(row["frozen_at"]) > _time(stamp):
                raise EvidenceError("research cohort freeze is in the future")
            identity = challenger_id + ":" + row["member_sha256"]
            if identity not in member_receipts:
                member_receipts[identity] = {
                    "challenger_id": challenger_id, "member_sha256": row["member_sha256"],
                    "registered_at": stamp,
                }
                enrolled.append(identity)
        except (EvidenceError, ValueError, KeyError, TypeError) as exc:
            rejected.append({"challenger_id": item.parent.parent.name, "reason": str(exc)})
    if added or enrolled:
        champion._atomic_json(path, state)
    return {"registered": added, "member_receipts": enrolled, "rejected": rejected}


def _event_content(event: Mapping[str, Any]) -> dict[str, Any]:
    # Research records its own collection time; every actual decision/input
    # field must independently match the native opportunity-derived event.
    return {key: value for key, value in event.items() if key not in {"recorded_at", "event_sha256"}}


def _episode_outcome_at(event: Mapping[str, Any], sample: Mapping[str, Any], frames: list[dict[str, Any]], *,
                        horizon_sessions: int) -> datetime:
    """Use the sourced terminal close, before its later collection receipt."""
    from scripts import stock_trading_portfolio as portfolio
    _, exact, _, _ = _modules()
    episode = exact.production_episode_input(event, frames, horizon_sessions=horizon_sessions)
    if not episode or episode.get("episode_end_at") != sample.get("episode_end_at"):
        raise EvidenceError("native episode outcome boundary unavailable")
    terminal = episode["frames"][-1]
    market = str(event["market"])
    session = (terminal.get("market_sessions") or {}).get(market)
    if not isinstance(session, Mapping):
        raise EvidenceError("native episode terminal session missing")
    try:
        opening, close = _time(session["regular_start"]), _time(session["regular_end"])
        observed, recorded = _time(terminal["observed_at"]), _time(terminal["recorded_at"])
        session_date = close.astimezone(portfolio.MARKET_TZ[market]).date().isoformat()
        if (not session.get("source") or session.get("date") != session_date
                or session.get("state") not in {"POST", "POSTPOST", "CLOSED", "REGULAR_CLOSED"}
                or not opening < close <= observed <= recorded
                or close <= _time(event["decision_at"])):
            raise EvidenceError("native episode terminal session invalid")
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceError("native episode terminal session invalid") from exc
    return close


def _native_eligible_events(challenger: Mapping[str, Any], frames: list[dict[str, Any]], *,
                            start: datetime, end: datetime, policy_sha256: str) -> dict[str, dict[str, Any]]:
    """Derive the enrollment universe from main's complete native stream."""
    from scripts import stock_trading_v2_opportunity_experience as experience
    from scripts import stock_trading_v2_production_replay as replay
    evaluator, exact, _, ledger = _modules()
    candidate = evaluator._exact_candidate(challenger)
    market = str((challenger.get("exact_replay") or {}).get("market") or "")
    baseline = challenger.get("champion") or {}
    code = replay.production_code_sha256()
    events: dict[str, dict[str, Any]] = {}
    for raw in frames:
        frame = raw.get("frame") or raw
        opportunity = (frame.get("opportunities") or {}).get(market)
        if not isinstance(opportunity, Mapping):
            continue
        decision = _time(opportunity.get("generated_at"))
        if not start <= decision <= end:
            continue
        errors = replay._validate_frame(frame, code)
        if errors:
            raise EvidenceError("native enrollment frame invalid: " + ",".join(errors))
        if _time(frame["observed_at"]) < decision:
            raise EvidenceError("native enrollment frame precedes source decision")
        context = opportunity.get("decision_context") or {}
        if (frame.get("policy_sha256") != digest(frame.get("policy") or {})
                or frame.get("policy_sha256") != context.get("policy_sha256")
                or frame.get("policy") != context.get("policy_snapshot")
                or frame.get("manifest") != context.get("champion_manifest")
                or context.get("champion_manifest_sha256") != digest(context.get("champion_manifest") or {})
                or context.get("bridge_config_sha256") != digest(context.get("bridge_config_snapshot") or {})
                or context.get("market") != market or context.get("decision_at") != opportunity.get("generated_at")):
            raise EvidenceError("native enrollment context/policy/manifest binding failed")
        # Another validated production baseline is outside this experiment.
        # A stale research context cannot hide a registered-policy frame.
        if (frame.get("policy_sha256") != policy_sha256
                or frame.get("manifest") != baseline.get("manifest_snapshot")):
            continue
        source = context.get("evidence_snapshot")
        if not isinstance(source, Mapping):
            raise EvidenceError("native enrollment evidence snapshot missing")
        try:
            derived = experience.events_from_snapshots(opportunity, source,
                candidates_per_cycle=max(1, len(opportunity.get("evaluated_candidates") or [])),
                recorded_at=opportunity["generated_at"])
            for event in derived:
                ledger._context(event)
                if not exact.cash_edge_eligible(event, candidate):
                    continue
                event_id = str(event["event_id"])
                prior = events.get(event_id)
                if prior is not None and _event_content(prior) != _event_content(event):
                    raise EvidenceError("ambiguous native enrollment opportunity")
                events[event_id] = event
        except (ValueError, KeyError, TypeError) as exc:
            raise EvidenceError("native enrollment opportunity cannot be independently derived: " + str(exc)) from exc
    return events


def validate_proof(challenger: Mapping[str, Any], evaluation: Mapping[str, Any], *,
                   source_root: Path = DATA, frames_root: Path | None = None,
                   trials_path: Path = TRIALS_PATH,
                   current_policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Recompute all formal samples from verified source files and main tape."""
    if (challenger.get("exact_replay") or {}).get("adapter") != "cash_edge_v2":
        raise EvidenceError("legacy frozen-geometry replay is not production-policy evidence")
    proof = evaluation.get("formal_proof")
    if not isinstance(proof, Mapping) or proof.get("schema_version") != "stock-trading-cash-edge-proof-v1" or proof.get("terminal") is not True:
        raise EvidenceError("complete terminal prospective proof required")
    _hash_field(proof, "proof_sha256")
    evaluator, exact, contracts, ledger = _modules()
    for key in ("challenger_id", "challenger_sha256", "protocol_sha256"):
        if proof.get(key) != challenger.get(key):
            raise EvidenceError(f"formal proof identity mismatch: {key}")
    protocol = proof.get("protocol") or {}
    validate_protocol(protocol)
    evaluator._cash_protocol(challenger)
    if digest(protocol) != proof.get("protocol_sha256") or protocol != challenger.get("protocol"):
        raise EvidenceError("formal protocol does not match preregistration")
    registry = load_trials(trials_path)
    trial = registry["trials"].get(str(challenger["challenger_id"]))
    if not trial or trial["challenger_sha256"] != challenger["challenger_sha256"]:
        raise EvidenceError("experiment lacks production lifetime registration")
    if current_policy is not None and digest(current_policy) != trial.get("policy_sha256"):
        raise EvidenceError("registered Champion differs from current production policy")
    family = challenger.get("test_family") or {}
    if int(family.get("test_index") or family.get("index") or 0) != trial["test_index"] or proof.get("family") != family:
        raise EvidenceError("formal trial family binding mismatch")
    candidate = challenger.get("production_candidate") or {}
    if (proof.get("deployment_sha256") != candidate.get("deployment_sha256")
            or digest(candidate.get("deployment_spec") or {}) != candidate.get("deployment_sha256")):
        raise EvidenceError("formal deployment differs from evaluated artifact")
    fingerprints = proof.get("code_fingerprints") or {}
    if fingerprints != challenger.get("code_fingerprints") or not evaluator._code_unchanged(challenger):
        raise EvidenceError("audited runtime/evaluator code changed")
    cohort = proof.get("cohort") or {}
    _hash_field(cohort, "cohort_sha256")
    if cohort.get("cohort_sha256") != proof.get("cohort_sha256") or not cohort.get("membership_closed"):
        raise EvidenceError("formal cohort is not closed")
    members = cohort.get("members") or []
    n = int(protocol["fixed_paired_n"])
    if len(members) != n or proof.get("source_refs") != members or len({row.get("source_event_id") for row in members}) != n:
        raise EvidenceError("formal sample membership is incomplete or duplicated")
    start = max(_time(challenger["validation_start_at"]), _time(trial["registered_at"]))
    previous_block = start
    frame_dir = frames_root or DATA / "stock_trading_v2_production_replay_frames"
    frames = [_read(path) for path in sorted(frame_dir.rglob("*.json"))]
    if not frames:
        raise EvidenceError("production-owned native replay inputs unavailable")
    native_events = _native_eligible_events(challenger, frames, start=start,
        end=max(_time(member["decision_at"]) for member in members), policy_sha256=trial["policy_sha256"])
    events_by_id = {}
    for path in (source_root / "stock_trading_v2_experience").rglob("*.json"):
        row = _read(path)
        if row["event_id"] in events_by_id:
            raise EvidenceError("ambiguous formal source event")
        events_by_id[row["event_id"]] = row
    admissions_by_id: dict[str, Any] = {}
    for path in (source_root / "stock_trading_v2_admission").rglob("*.json"):
        row = _read(path)
        if ledger.is_formal_observation(row):
            key = str(row["source_event_id"])
            if key in admissions_by_id:
                raise EvidenceError("ambiguous formal Champion baseline")
            admissions_by_id[key] = row
    selected_events, selected_admissions = [], []
    # Only main's complete native opportunities determine first-eligible N.
    # Missing research files/admissions never remove an episode from this set.
    chronological = []
    blocked = start
    groups = set()
    for event in sorted(native_events.values(), key=lambda row: (_time(row["decision_at"]), str(row["event_id"]))):
        decision = _time(event["decision_at"])
        group = exact.source_group(event)
        if decision < blocked or group in groups:
            continue
        chronological.append(str(event["event_id"]))
        groups.add(group)
        blocked = decision + timedelta(hours=float(protocol["block_hours"]))
        if len(chronological) == n:
            break
    if chronological != [str(member["source_event_id"]) for member in members]:
        raise EvidenceError("closed cohort omitted or replaced an earlier eligible episode")
    for index, member in enumerate(members, 1):
        _hash_field(member, "member_sha256")
        if member.get("index") != index or member.get("enrollment_status") != "FROZEN_BEFORE_OUTCOME":
            raise EvidenceError("nonprospective cohort member")
        decision, frozen = _time(member["decision_at"]), _time(member["frozen_at"])
        if decision < previous_block or frozen < decision or (frozen - decision).total_seconds() > float(protocol["enrollment_deadline_hours"]) * 3600:
            raise EvidenceError("cohort overlap or outcome-dependent late enrollment")
        previous_block = _time(member["block_until"])
        if (previous_block - decision).total_seconds() < float(protocol["block_hours"]) * 3600:
            raise EvidenceError("cohort independent episode was shortened")
        event_id = str(member["source_event_id"])
        event, admission = events_by_id.get(event_id), admissions_by_id.get(event_id)
        if not event or not admission or digest(event) != member.get("event_sha256") or digest(admission) != member.get("admission_sha256"):
            raise EvidenceError("formal source event/admission binding failed")
        contracts.validate_experience_event(event)
        ledger.validate_observation(admission)
        if _event_content(event) != _event_content(native_events[event_id]):
            raise EvidenceError("formal source event differs from trusted native opportunity")
        if event["decision_at"] != member["decision_at"] or admission["source_event_sha256"] != event["event_sha256"]:
            raise EvidenceError("formal event time or baseline mismatch")
        context = (event.get("candidate_state") or {}).get("decision_context") or {}
        if context.get("policy_sha256") != proof.get("champion_policy_sha256") or context.get("policy_sha256") != trial["policy_sha256"]:
            raise EvidenceError("formal source policy is not registered Champion")
        if _time(admission["recorded_at"]) > frozen:
            raise EvidenceError("Champion baseline was reconstructed after cohort enrollment")
        selected_events.append(event)
        selected_admissions.append(admission)
    computed = exact.collect_cash_edge_samples(
        candidate=evaluator._exact_candidate(challenger), events=selected_events,
        admissions=selected_admissions, outcomes=[], cohort=members, replay_frames=frames,
    )
    if len(computed) != n or computed != proof.get("paired_samples") or computed != evaluation.get("formal_samples"):
        raise EvidenceError("independent production replay does not reproduce formal samples")
    for member, sample, event in zip(members, computed, selected_events):
        outcome_at = _episode_outcome_at(event, sample, frames, horizon_sessions=int(challenger["horizon_sessions"]))
        if _time(member["frozen_at"]) >= outcome_at:
            raise EvidenceError("formal cohort was frozen after its outcome")
        receipt = registry.get("members", {}).get(str(challenger["challenger_id"]) + ":" + member["member_sha256"])
        if (not receipt or receipt.get("member_sha256") != member["member_sha256"]
                or _time(receipt["registered_at"]) >= outcome_at):
            raise EvidenceError("main did not receive the fixed cohort member before its outcome")
    metrics = evaluator.formal_metrics(computed, protocol, seed=str(challenger["challenger_id"]))
    if metrics != proof.get("metrics") or metrics != evaluation.get("metrics") or metrics.get("formal_pass") is not True:
        raise EvidenceError("independently recomputed statistical gate did not pass")
    return {"verified": True, "paired_n": n, "proof_sha256": proof["proof_sha256"], "trial_index": trial["test_index"]}
