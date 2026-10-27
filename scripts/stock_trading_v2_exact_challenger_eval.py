#!/usr/bin/env python3
"""Fresh-holdout evaluator for exact executable Stock Trading v2 Challengers."""
from __future__ import annotations

import argparse
import json
import hashlib
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_v2_challenger_eval as evaluator
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_exact_replay as exact
    from scripts import stock_trading_v2_policy_learner as learner
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_challenger_eval as evaluator
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_exact_replay as exact
    import stock_trading_v2_policy_learner as learner


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def _iter(root: Path) -> list[dict[str, Any]]:
    return [_read(path) for path in sorted(root.rglob("*.json"))] if root.exists() else []


def _exact_candidate(challenger: Mapping[str, Any]) -> dict[str, Any] | None:
    production = challenger.get("production_candidate")
    replay = challenger.get("exact_replay")
    if not isinstance(production, Mapping) or not isinstance(replay, Mapping):
        return None
    spec = production.get("deployment_spec")
    if not isinstance(spec, Mapping):
        raise contracts.ContractError("exact Challenger deployment_spec missing")
    result = {
        "horizon_sessions": challenger["horizon_sessions"],
        "deployment_spec": dict(spec),
        "replay_contract": dict(replay),
    }
    if replay.get("adapter") == "cash_edge_v2":
        result["champion_policy_snapshot"] = (challenger.get("champion") or {}).get("policy_snapshot")
        result["champion_policy_sha256"] = (challenger.get("champion") or {}).get("policy_sha256")
    return result


def _immutable(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != row:
            raise contracts.ContractError(f"immutable prospective evidence conflict: {path.name}")
        return
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _cash_protocol(challenger: Mapping[str, Any]) -> dict[str, Any]:
    protocol = dict(challenger.get("protocol") or {})
    if contracts.payload_sha256(protocol) != challenger.get("protocol_sha256"):
        raise contracts.ContractError("CASH protocol hash mismatch")
    if (protocol.get("schema_version") != "stock-trading-cash-edge-protocol-v1"
            or int(protocol.get("fixed_paired_n") or 0) < 30
            or int(protocol.get("block_hours") or 0) < 168
            or protocol.get("single_formal_look") is not True
            or protocol.get("lifetime_max_tests") != 24
            or protocol.get("family_id") != "stock-trading-v2-cash-edge-lifetime-v1"
            or not 0 < float(protocol.get("alpha") or 0) <= 0.05 / 24
            or protocol.get("maximum_censored") != 0
            or protocol.get("membership") != "chronological_eligible_episode_before_outcome"
            or protocol.get("outcome_missing") != "WAIT_THEN_TERMINAL_CENSOR_NO_REPLACEMENT"
            or not 0 < int(protocol.get("enrollment_deadline_hours") or 0) <= 24
            or not 0 < int(protocol.get("outcome_deadline_days") or 0) <= 180
            or int(protocol.get("bootstrap_draws") or 0) < 20000
            or int(protocol.get("min_unique_symbols") or 0) < 5
            or int(protocol.get("min_span_days") or 0) < 10
            or not 0 < float(protocol.get("max_single_positive_share") or 0) <= 0.5
            or float(protocol.get("max_drawdown_increase_pct", math.inf)) > 0
            or float(protocol.get("min_mean_incremental_pct", -math.inf)) < 0
            or protocol.get("horizon_sessions") != challenger.get("horizon_sessions")
            or int(protocol.get("horizon_sessions") or 0) not in {1, 2}
            or protocol.get("session_horizon_rule") != "first_sourced_regular_closes_after_decision"
            or protocol.get("execution_semantics") != "production_lifecycle_v1"):
        raise contracts.ContractError("CASH prospective protocol weakens fixed safety limits")
    return protocol


def _formal_admission(row: Mapping[str, Any]) -> bool:
    try:
        from scripts import stock_trading_v2_admission_ledger as ledger
        return ledger.is_formal_observation(row)
    except (ImportError, AttributeError):
        return False


def _trial_receipt(challenger: Mapping[str, Any], receipts: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Read a frozen main-owned receipt; research cannot invent registration."""
    if not receipts:
        return None
    if receipts.get("schema_version") != "stock-trading-component-trials-v1":
        raise contracts.ContractError("production trial receipt schema invalid")
    trials = receipts.get("trials") or {}
    row = trials.get(str(challenger["challenger_id"]))
    if row is None:
        return None
    family = challenger.get("test_family") or {}
    if (not isinstance(row, Mapping) or row.get("challenger_id") != challenger["challenger_id"]
            or row.get("challenger_sha256") != challenger["challenger_sha256"]
            or row.get("family_id") != "stock-trading-v2-cash-edge-lifetime-v1"
            or row.get("test_index") != family.get("test_index")
            or row.get("protocol_sha256") != challenger["protocol_sha256"]
            or row.get("policy_sha256") != (challenger.get("champion") or {}).get("policy_sha256")):
        raise contracts.ContractError("production trial receipt binding mismatch")
    exact.parse_dt(row.get("registered_at"))
    return dict(row)


def freeze_cohort(challenger: Mapping[str, Any], *, events: list[dict[str, Any]], admissions: list[dict[str, Any]],
                  outcomes: list[dict[str, Any]], cohort_root: Path, now: datetime,
                  trial_receipts: Mapping[str, Any] | None = None,
                  replay_frames: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Append members before results. Missing/late members stay in the sample.

    No outcome-ranked filtering, no replacement, and no post-result insertion
    of an earlier decision into an already frozen chronological cohort.
    """
    protocol = _cash_protocol(challenger)
    candidate = _exact_candidate(challenger)
    assert candidate is not None
    receipt = _trial_receipt(challenger, trial_receipts)
    contract = {"challenger_id": challenger["challenger_id"], "challenger_sha256": challenger["challenger_sha256"],
                "protocol_sha256": challenger["protocol_sha256"], "created_at": challenger["validation_start_at"]}
    if receipt is None or exact.parse_dt(receipt["registered_at"]) > now:
        # No contract/member write before main reserves the experiment. A
        # missing handoff receipt is waiting, never a failed statistical look.
        cohort = {**contract, "members": [], "required_n": int(protocol["fixed_paired_n"]),
                  "membership_closed": False, "awaiting_production_trial_receipt": True}
        cohort["cohort_sha256"] = contracts.payload_sha256(cohort)
        return cohort
    root = cohort_root / str(challenger["challenger_id"])
    contract["production_trial_receipt"] = receipt
    _immutable(root / "contract.json", contract)
    members = [_read(path) for path in sorted((root / "members").glob("*.json"))] if (root / "members").exists() else []
    events_by_id = {str(row.get("event_id")): row for row in events}
    admissions_by_id = {str(row.get("source_event_id")): row for row in admissions if _formal_admission(row)}
    for index, member in enumerate(members, start=1):
        body = dict(member)
        digest = body.pop("member_sha256", None)
        source = events_by_id.get(str(member.get("source_event_id")))
        observed = admissions_by_id.get(str(member.get("source_event_id")))
        if (member.get("index") != index or digest != contracts.payload_sha256(body)
                or (source is not None and member.get("event_sha256") != contracts.payload_sha256(source))
                or (observed is not None and member.get("admission_sha256") != contracts.payload_sha256(observed))
                or (index > 1 and exact.parse_dt(member["decision_at"]) < exact.parse_dt(members[index - 2]["block_until"]))):
            raise contracts.ContractError("immutable prospective cohort member integrity mismatch")
    outcome_ids = {str(row.get("source_event_id")) for row in outcomes}
    start = max(exact.parse_dt(challenger["validation_start_at"]), exact.parse_dt(receipt["registered_at"]))
    used_groups = {str(row["source_group"]) for row in members}
    blocked_until = max((exact.parse_dt(row["block_until"]) for row in members), default=start)
    native_events = exact.native_cash_admission_events(candidate, replay_frames or [], as_of=now)
    for native in sorted(native_events.values(), key=lambda row: (exact.parse_dt(row["decision_at"]), str(row["event_id"]))):
        if len(members) >= int(protocol["fixed_paired_n"]):
            break
        event_id = str(native["event_id"])
        decision_at = exact.parse_dt(native["decision_at"])
        if decision_at < start or decision_at < blocked_until or decision_at > now:
            continue
        if exact.source_group(native) in used_groups:
            continue
        context = (native.get("candidate_state") or {}).get("decision_context") or {}
        if context.get("champion_manifest") != (challenger.get("champion") or {}).get("manifest_snapshot"):
            continue
        event = events_by_id.get(event_id)
        observed = admissions_by_id.get(event_id)
        if event is None or observed is None or contracts.event_without_runtime_fields(event) != contracts.event_without_runtime_fields(native):
            # Do not replace an earlier main-observed eligible episode with a
            # later convenient research row while its source/admission waits.
            break
        late = (event_id in outcome_ids or now - decision_at > timedelta(hours=int(protocol["enrollment_deadline_hours"])))
        # A native close can reveal the formal horizon before the descriptive
        # next-session outcome writer runs. It cannot become a fresh member.
        completed_tape = exact.production_episode_input(event, replay_frames or [],
            horizon_sessions=int(challenger["horizon_sessions"]))
        if completed_tape is not None:
            terminal = completed_tape["frames"][-1]
            sourced_close = ((terminal.get("market_sessions") or {}).get(event["market"]) or {}).get("regular_end")
            if sourced_close is not None and exact.parse_dt(sourced_close) <= now:
                late = True
        member = {
            "index": len(members) + 1, "source_event_id": event_id, "source_group": exact.source_group(event),
            "event_sha256": contracts.payload_sha256(event), "admission_sha256": contracts.payload_sha256(observed),
            "decision_at": event["decision_at"], "frozen_at": now.isoformat().replace("+00:00", "Z"),
            "block_until": (decision_at + timedelta(hours=int(protocol["block_hours"]))).isoformat().replace("+00:00", "Z"),
            "block_id": f"{event.get('market')}:{event_id}",
            "horizon_sessions": int(challenger["horizon_sessions"]),
            "outcome_deadline_at": (decision_at + timedelta(days=int(protocol["outcome_deadline_days"]))).isoformat().replace("+00:00", "Z"),
            "enrollment_status": "CENSORED_LATE_ENROLLMENT" if late else "FROZEN_BEFORE_OUTCOME",
            "policy_sha256": context["policy_sha256"], "portfolio_sha256": context.get("portfolio_sha256"),
        }
        member["member_sha256"] = contracts.payload_sha256(member)
        _immutable(root / "members" / f"{member['index']:04d}.json", member)
        members.append(member)
        used_groups.add(member["source_group"])
        blocked_until = exact.parse_dt(member["block_until"])
    cohort = {**contract, "members": members, "required_n": int(protocol["fixed_paired_n"]),
              "membership_closed": len(members) == int(protocol["fixed_paired_n"])}
    cohort["cohort_sha256"] = contracts.payload_sha256(cohort)
    if cohort["membership_closed"]:
        _immutable(root / "cohort.json", cohort)
    return cohort


def _code_unchanged(challenger: Mapping[str, Any]) -> bool:
    fingerprints = challenger.get("code_fingerprints") or {}
    return set(exact.CASH_FINGERPRINT_FILES) == set(fingerprints) and all(
        (Path(__file__).parent / name).is_file() and hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() == sha
        for name, sha in fingerprints.items()
    )


def formal_metrics(samples: list[dict[str, Any]], protocol: Mapping[str, Any], *, seed: str) -> dict[str, Any]:
    values = [float(row["incremental_net_return_percent"]) for row in samples]
    n = len(values)
    positives = sum(value > 0 for value in values)
    # Each member is a separated prospective episode. In addition to the
    # block bootstrap mean, an exact one-sided paired sign test spends the
    # Bonferroni-adjusted lifetime alpha. Zero deltas count against improvement.
    sign_p = sum(math.comb(n, index) for index in range(positives, n + 1)) / (2 ** n) if n else 1.0
    lower = evaluator._bootstrap_lower_bound(values, confidence=1.0 - float(protocol["alpha"]),
                                              draws=int(protocol["bootstrap_draws"]), seed_text=seed)
    positive_values = [max(0.0, value) for value in values]
    concentration = max(positive_values) / sum(positive_values) if sum(positive_values) else 1.0
    dates = sorted(exact.parse_dt(row["decision_at"]) for row in samples)
    mean = statistics.mean(values) if values else 0.0
    midpoint = max(1, n // 2)
    checks = {
        "fixed_independent_episodes": n == int(protocol["fixed_paired_n"]) and len({row["block_id"] for row in samples}) == n,
        "positive_mean": mean > float(protocol["min_mean_incremental_pct"]),
        "adjusted_bootstrap_floor": lower is not None and lower > 0,
        "family_adjusted_sign_test": sign_p <= float(protocol["alpha"]),
        "unique_symbols": len({row["symbol"] for row in samples}) >= int(protocol["min_unique_symbols"]),
        "span_days": bool(dates) and (dates[-1] - dates[0]).days >= int(protocol["min_span_days"]),
        "concentration": concentration <= float(protocol["max_single_positive_share"]),
        "time_halves": bool(values) and statistics.mean(values[:midpoint]) > 0 and bool(values[midpoint:]) and statistics.mean(values[midpoint:]) > 0,
        "drawdown_non_inferiority": all(float(row["challenger_max_drawdown_percent"]) <= float(row["champion_max_drawdown_percent"]) + float(protocol["max_drawdown_increase_pct"]) for row in samples),
        "benchmark_present": all(row.get("benchmark_return_percent") is not None for row in samples),
        "production_lifecycle_net": all(row.get("execution_semantics") == "production_lifecycle_v1" and (row.get("production_replay") or {}).get("formal_evidence_eligible") is True for row in samples),
    }
    return {"paired_n": n, "required_n": int(protocol["fixed_paired_n"]), "mean_incremental_net_return_percent": mean,
            "bootstrap_lower_bound_percent": lower, "paired_sign_p": sign_p,
            "maximum_single_positive_contribution_share": concentration, "checks": checks, "formal_pass": all(checks.values())}


def evaluate_cash(challenger: Mapping[str, Any], *, events: list[dict[str, Any]], admissions: list[dict[str, Any]],
                  outcomes: list[dict[str, Any]], cohort_root: Path, now: datetime,
                  replay_frames: list[dict[str, Any]] | None = None,
                  trial_receipts: Mapping[str, Any] | None = None) -> dict[str, Any]:
    learner.validate_challenger(challenger)
    protocol = _cash_protocol(challenger)
    if challenger.get("test_family"):
        learner.validate_test_family(challenger["test_family"], challenger_id=str(challenger["challenger_id"]), protocol_sha256=challenger["protocol_sha256"])
    cohort = freeze_cohort(challenger, events=events, admissions=admissions, outcomes=outcomes,
                           cohort_root=cohort_root, now=now, trial_receipts=trial_receipts, replay_frames=replay_frames)
    candidate = _exact_candidate(challenger)
    assert candidate is not None
    samples = exact.collect_cash_edge_samples(candidate=candidate, events=events, admissions=admissions, outcomes=outcomes,
                                             cohort=cohort["members"], replay_frames=replay_frames or [])
    # Every identity remains frozen even if its outcome is absent. A timeout
    # terminates the experiment; a later result can never replace that member.
    waiting_receipt = cohort.get("awaiting_production_trial_receipt") is True
    failed = (not waiting_receipt and (not _code_unchanged(challenger) or not challenger.get("test_family")
              or any(row["enrollment_status"] != "FROZEN_BEFORE_OUTCOME" for row in cohort["members"])))
    sample_ids = {row["source_event_id"] for row in samples}
    failed = failed or any(now > exact.parse_dt(row["outcome_deadline_at"]) and row["source_event_id"] not in sample_ids for row in cohort["members"])
    terminal = failed or (cohort["membership_closed"] and len(samples) == int(protocol["fixed_paired_n"]))
    metrics = formal_metrics(samples, protocol, seed=str(challenger["challenger_id"])) if terminal and not failed else {
        "paired_n": len(samples), "required_n": int(protocol["fixed_paired_n"]), "formal_pass": False,
        "frozen_members": len(cohort["members"]), "checks": {"code_and_protocol_intact": not failed},
    }
    if waiting_receipt:
        metrics["awaiting_production_trial_receipt"] = True
    passed = terminal and not failed and metrics.get("formal_pass") is True
    state = "RESEARCH_PASS_AWAITING_MANUAL_ENABLEMENT" if passed else "FORMAL_HOLDOUT_FAIL" if terminal else "COLLECTING_FRESH_HOLDOUT"
    production = dict(challenger["production_candidate"])
    proof = {
        "schema_version": "stock-trading-cash-edge-proof-v1", "challenger_id": challenger["challenger_id"],
        "challenger_sha256": challenger["challenger_sha256"], "deployment_sha256": production["deployment_sha256"],
        "champion_policy_sha256": (challenger.get("champion") or {}).get("policy_sha256"),
        "protocol": protocol, "protocol_sha256": challenger["protocol_sha256"],
        "cohort": cohort, "cohort_sha256": cohort["cohort_sha256"],
        "code_fingerprints": challenger.get("code_fingerprints") or {}, "family": challenger.get("test_family") or {},
        "source_refs": cohort["members"], "paired_samples": samples, "metrics": metrics, "terminal": terminal,
    }
    proof["proof_sha256"] = contracts.payload_sha256(proof)
    report = {
        "schema_version": evaluator.SCHEMA_VERSION, "challenger_id": challenger["challenger_id"],
        "component": challenger["component"], "horizon_sessions": challenger["horizon_sessions"],
        "validation_start_at": challenger["validation_start_at"], "generated_at": now.isoformat().replace("+00:00", "Z"),
        "state": state, "metrics": metrics, "formal_samples": samples, "formal_proof": proof,
        "evaluated_production_candidate": {key: production[key] for key in ("component", "deployment_id", "base_manifest_revision", "base_component_version", "deployment_sha256")},
        "exact_replay_adapter": "cash_edge_v2",
        "governance": {"production_decision_influence": False, "automatic_policy_writeback": False,
                       "production_promotion_enabled": False, "single_formal_look": True},
    }
    report["evaluated_production_candidate"]["execution_semantics"] = "production_lifecycle_v1"
    report["evaluation_sha256"] = contracts.payload_sha256(report)
    evaluator.validate_evaluation(report)
    return report


def evaluate_one(challenger: Mapping[str, Any], *, events: list[dict[str, Any]], admissions: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> dict[str, Any] | None:
    learner.validate_challenger(challenger)
    replay_candidate = _exact_candidate(challenger)
    if replay_candidate is None:
        return None
    start = exact.parse_dt(challenger["validation_start_at"])
    samples = exact.collect_entry_threshold_samples(candidate=replay_candidate, events=events, admissions=admissions, outcomes=outcomes, after=start)
    report = evaluator.evaluate_samples(challenger, samples)
    production = dict(challenger["production_candidate"])
    report["evaluated_production_candidate"] = {
        "component": production["component"],
        "deployment_id": production["deployment_id"],
        "base_manifest_revision": production["base_manifest_revision"],
        "base_component_version": production["base_component_version"],
        "deployment_sha256": production["deployment_sha256"],
        "execution_semantics": "exact_shadow_replay",
    }
    report["exact_replay_adapter"] = str((challenger.get("exact_replay") or {}).get("adapter") or "")
    body = dict(report)
    body.pop("evaluation_sha256", None)
    report["evaluation_sha256"] = contracts.payload_sha256(body)
    evaluator.validate_evaluation(report)
    return report


def run(*, challenger_root: Path, experience_root: Path, admission_root: Path, outcome_root: Path, output_root: Path,
        replay_frame_root: Path | None = None, now: datetime | None = None,
        trial_receipts: Mapping[str, Any] | None = None, freeze_cohorts_only: bool = False) -> dict[str, Any]:
    events, admissions, outcomes = _iter(experience_root), _iter(admission_root), _iter(outcome_root)
    output_root.mkdir(parents=True, exist_ok=True)
    exact_count = pass_count = frozen_count = 0
    replay_frames = _iter(replay_frame_root) if replay_frame_root else []
    for path in sorted(challenger_root.glob("*.json")) if challenger_root.exists() else []:
        challenger = _read(path)
        output_path = output_root / path.name
        if output_path.exists():
            previous = _read(output_path)
            evaluator.validate_evaluation(previous)
            if previous.get("state") != "COLLECTING_FRESH_HOLDOUT":
                exact_count += 1
                pass_count += int((previous.get("metrics") or {}).get("formal_pass") is True)
                continue  # Closed verdict and cohort are permanently immutable.
        if (challenger.get("exact_replay") or {}).get("adapter") == "cash_edge_v2":
            if freeze_cohorts_only:
                learner.validate_challenger(challenger)
                cohort = freeze_cohort(challenger, events=events, admissions=admissions, outcomes=outcomes,
                                       cohort_root=output_root / ".cohorts", now=now or datetime.now(timezone.utc),
                                       trial_receipts=trial_receipts, replay_frames=replay_frames)
                frozen_count += len(cohort["members"])
                exact_count += 1
                continue
            report = evaluate_cash(challenger, events=events, admissions=admissions, outcomes=outcomes,
                                   cohort_root=output_root / ".cohorts", now=now or datetime.now(timezone.utc),
                                   replay_frames=replay_frames, trial_receipts=trial_receipts)
        else:
            if freeze_cohorts_only:
                continue
            report = evaluate_one(challenger, events=events, admissions=admissions, outcomes=outcomes)
        if report is None:
            continue
        exact_count += 1
        if (report.get("metrics") or {}).get("formal_pass") is True:
            pass_count += 1
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"schema_version": "stock-trading-v2-exact-eval-run-v1", "exact_challengers": exact_count,
            "formal_passes": pass_count, "frozen_members": frozen_count,
            "freeze_cohorts_only": freeze_cohorts_only, "production_decision_influence": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challengers", type=Path, required=True)
    parser.add_argument("--experience-root", type=Path, required=True)
    parser.add_argument("--admission-root", type=Path, required=True)
    parser.add_argument("--outcome-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--replay-frame-root", type=Path, default=Path(__file__).resolve().parents[1] / "data/investments/stock_trading_v2_production_replay_frames")
    parser.add_argument("--trial-receipts", type=Path, help="Read-only snapshot of main-owned lifetime trial registrations")
    parser.add_argument("--freeze-cohorts-only", action="store_true", help="Freeze new members after admissions, before outcome settlement; never evaluate")
    args = parser.parse_args()
    result = run(challenger_root=args.challengers, experience_root=args.experience_root, admission_root=args.admission_root, outcome_root=args.outcome_root, output_root=args.output_root, replay_frame_root=args.replay_frame_root,
                 trial_receipts=_read(args.trial_receipts) if args.trial_receipts and args.trial_receipts.exists() else None,
                 freeze_cohorts_only=args.freeze_cohorts_only)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
