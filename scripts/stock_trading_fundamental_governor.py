#!/usr/bin/env python3
"""Main-owned automatic promotion and circuit breaker for Stock fundamentals.

The only eligible deployment is the compiled-in, filter-only US quality gate.
Research cannot supply patches or executable code. Two independent, fixed-N
prospective 1/2-session holdouts must PASS. No discretionary approvals needed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts import stock_trading_fundamental_policy as policy
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_outcome_replay as outcomes
    from scripts import stock_trading_v2_fundamental_trial as trials
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_fundamental_policy as policy
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_outcome_replay as outcomes
    import stock_trading_v2_fundamental_trial as trials

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data/investments/stock_trading_fundamental_rollout.json"
PROTOCOL_PATH = ROOT / "data/investments/stock_trading_v2_fundamental_trial_config.json"
AUDIT_PATH = ROOT / "data/investments/stock_trading_fundamental_promotion_audit.jsonl"
SCHEMA = "stock-trading-fundamental-rollout-v1"
REPLAY_VERSION = "frozen-geometry-next-session-open-v1"


class FundamentalPromotionError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise FundamentalPromotionError("expected object: " + str(path))
    return result


def _atomic(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
        json.dump(data, file, indent=2, ensure_ascii=False, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
        temp_path = Path(file.name)
    os.replace(temp_path, path)


def _audit(event: str, *, at: datetime, data: Mapping[str, Any], path: Path = AUDIT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"event": event, "at": contracts.iso_utc(at), **dict(data)}
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        file.flush()
        os.fsync(file.fileno())


def validate_rollout(state: Mapping[str, Any]) -> None:
    if state.get("schema_version") != SCHEMA or state.get("policy_id") != policy.POLICY_ID:
        raise FundamentalPromotionError("fundamental rollout schema/policy mismatch")
    if state.get("phase") not in {"SHADOW", "ACTIVE", "ROLLED_BACK"}:
        raise FundamentalPromotionError("invalid fundamental rollout phase")
    if not isinstance(state.get("blocked_trial_ids"), list):
        raise FundamentalPromotionError("rollback blocklist missing")
    if state["phase"] == "ACTIVE":
        if (not state.get("activated_at") or not state.get("formal_proof_sha256")
                or state.get("active_policy_sha256") != trials.digest_file(Path(policy.__file__))):
            raise FundamentalPromotionError("fundamental rollout lacks exact authority receipt")


def is_active(state: Mapping[str, Any]) -> bool:
    validate_rollout(state)
    return state.get("phase") == "ACTIVE"


def _outcome_id(event_id: str, horizon: int) -> str:
    return "stoutv2-" + contracts.payload_sha256({
        "event_id": event_id, "horizon": horizon, "replay_version": REPLAY_VERSION,
    })[:24]


def _validated_event(row: Mapping[str, Any], receipt: Mapping[str, Any], event_root: Path) -> dict[str, Any]:
    event_ref = row["event"]
    path = event_root / "us" / receipt["session_date"] / (event_ref["event_id"] + ".json")
    event = _read(path)
    contracts.validate_experience_event(event)
    if event["event_sha256"] != event_ref["event_sha256"] or event["symbol"] != event_ref["symbol"]:
        raise FundamentalPromotionError("immutable decision event mismatch")
    if event["source"]["payload_sha256"] != receipt["source_opportunity_sha256"]:
        raise FundamentalPromotionError("event from a different source opportunity")
    if event["recorded_at"] > receipt["frozen_at"]:
        raise FundamentalPromotionError("experience was not frozen before fundamental trial")
    if event["decision_at"] != receipt["decision_at"]:
        raise FundamentalPromotionError("event timestamps not aligned")
    context = ((event.get("candidate_state") or {}).get("decision_context") or {})
    original = context.get("opportunity_snapshot")
    if not isinstance(original, Mapping):
        raise FundamentalPromotionError("missing original immutable opportunity snapshot")
    source = dict(original)
    declared = source.pop("opportunity_sha256", None)
    if declared != receipt["source_opportunity_sha256"] or contracts.payload_sha256(source) != declared:
        raise FundamentalPromotionError("opportunity snapshot integrity failed")
    frozen_input = receipt["decision_input"]
    for key in ("generated_at", "cash", "evaluated_candidates"):
        if original.get(key) != frozen_input.get(key):
            raise FundamentalPromotionError("fundamental trial was not derived from immutable original opportunity")
    if original.get("source_evidence_sha256") != receipt["source_evidence_sha256"]:
        raise FundamentalPromotionError("fundamental evidence hash differs from original opportunity")
    evidence = (original.get("decision_context") or {}).get("evidence_snapshot") or {}
    if evidence.get("source_fundamentals_sha256") != receipt["source_fundamentals_sha256"]:
        raise FundamentalPromotionError("fundamental snapshot provenance not bound")
    return event


def _outcome_return(
    row: Mapping[str, Any] | None, receipt: Mapping[str, Any],
    *,
    horizon: int, event_root: Path, outcome_root: Path,
) -> tuple[str, float | None]:
    if row is None:
        return "SETTLED", 0.0
    event = _validated_event(row, receipt, event_root)
    event_id = event["event_id"]
    path = outcome_root / "us" / receipt["session_date"] / (_outcome_id(event_id, horizon) + ".json")
    if not path.exists():
        return "PENDING", None
    outcome = _read(path)
    outcomes.validate_outcome(outcome)
    outcomes.validate_source_binding(outcome, event)
    if int(outcome["horizon_sessions"]) != horizon or outcome.get("settled_at") <= receipt["frozen_at"]:
        raise FundamentalPromotionError("outcome horizon or settlement time violates prospectivity")
    replay = outcome["replay"]
    status = replay.get("status")
    if status == "UNPLAYABLE":
        return "UNPLAYABLE", None
    if status == "NOT_ACTIVATED":
        return "SETTLED", 0.0
    result = policy.finite(replay.get("net_return_percent"))
    if result is None or not -200 <= result <= 200:
        raise FundamentalPromotionError("invalid settled return")
    return "SETTLED", result


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, int((len(ordered)-1) * quantile)))
    return ordered[position]


def _bootstrap_lower(values: list[float], *, n: int, seed: int, quantile: float = 0.025) -> float:
    rng = random.Random(seed)
    means = [sum(rng.choices(values, k=len(values))) / len(values) for _ in range(n)]
    return _percentile(means, quantile)


def _test_horizon(episodes: list[dict[str, Any]], protocol: Mapping[str, Any], horizon: int) -> dict[str, Any]:
    diffs = [float(row["returns"][horizon]) for row in episodes]
    changed = [float(row["returns"][horizon]) for row in episodes if row["changed"]]
    positive = [value for value in diffs if value > 0]
    mean = sum(diffs) / len(diffs)
    lower = _bootstrap_lower(diffs, n=int(protocol["bootstrap_resamples"]),
                             seed=int(protocol["bootstrap_seed"]) + horizon)
    positive_changed_fraction = sum(x > 0 for x in changed) / len(changed) if changed else 0.0
    max_share = max(positive)/sum(positive) if positive else 1.0
    passed = (
        mean >= float(protocol["min_mean_uplift_percent"])
        and lower > 0
        and positive_changed_fraction >= float(protocol["min_positive_changed_fraction"])
        and max_share <= float(protocol["max_single_positive_contribution_share"])
    )
    return {
        "horizon_sessions": horizon, "mean_net_incremental_return_percent": round(mean, 6),
        "bootstrap_lower_97_5_percent": round(lower, 6),
        "positive_changed_fraction": round(positive_changed_fraction, 6),
        "largest_positive_share": round(max_share, 6),
        "formal_pass": passed,
    }


def evaluate(
    *,
    research_root: Path, protocol_path: Path = PROTOCOL_PATH,
    fixed_n: int | None = None,
) -> dict[str, Any]:
    """One irrevocable formal look at the first fixed-N dated receipts.

    The evaluation NEVER substitutes later winners for missing or failed
    earliest episodes. Exact algorithm + snapshot provenance recomputed on main.
    """
    protocol = _read(protocol_path)
    n = int(protocol["fixed_n"]) if fixed_n is None else fixed_n
    if n != 30 or not protocol.get("one_formal_look") or protocol.get("policy_id") != policy.POLICY_ID:
        raise FundamentalPromotionError("only predeclared 30-episode single-look trial is allowed")
    if protocol.get("required_horizons_sessions") != [1, 2]:
        raise FundamentalPromotionError("both independent 1/2-day holdouts required")
    research_protocol = _read(research_root / "data/investments/stock_trading_v2_fundamental_trial_config.json")
    if research_protocol != protocol:
        raise FundamentalPromotionError("unapproved research protocol difference")
    research_policy = research_root / "scripts/stock_trading_fundamental_policy.py"
    if trials.digest_file(research_policy) != trials.digest_file(Path(policy.__file__)):
        raise FundamentalPromotionError("research and production algorithm bytes differ")
    frozen_root = research_root / "data/investments/stock_trading_v2_fundamental_trials"
    experience_root = research_root / "data/investments/stock_trading_v2_experience"
    outcome_root = research_root / "data/investments/stock_trading_v2_outcomes"
    files = sorted(frozen_root.glob("*.json")) if frozen_root.exists() else []
    if len(files) < n:
        return {"status": "COLLECTING", "frozen_sessions": len(files), "required": n}
    episodes: list[dict[str, Any]] = []
    for path in files[:n]:
        receipt = _read(path)
        trials.validate_receipt(receipt, protocol)
        if str(receipt["session_date"]) != path.stem:
            raise FundamentalPromotionError("misnamed trial receipt")
        returned: dict[int, float] = {}
        for horizon in (1, 2):
            baseline_status, baseline_ret = _outcome_return(
                receipt["baseline"], receipt, horizon=horizon,
                event_root=experience_root, outcome_root=outcome_root)
            challenger_status, challenger_ret = _outcome_return(
                receipt["challenger"], receipt, horizon=horizon,
                event_root=experience_root, outcome_root=outcome_root)
            if baseline_status == "UNPLAYABLE" or challenger_status == "UNPLAYABLE":
                return {"status": "FAILED_DATA", "reason": "unplayable_frozen_episode",
                        "session_date": receipt["session_date"], "horizon": horizon}
            if baseline_status == "PENDING" or challenger_status == "PENDING":
                return {"status": "AWAITING_SETTLEMENT", "session_date": receipt["session_date"],
                        "horizon": horizon, "frozen_sessions": len(files)}
            returned[horizon] = round(float(challenger_ret) - float(baseline_ret), 8)
        baseline_symbol = (receipt.get("baseline") or {}).get("candidate_symbol")
        challenger_symbol = (receipt.get("challenger") or {}).get("candidate_symbol")
        candidates = policy.original_candidates(receipt["decision_input"])
        complete = sum(policy.verify_fundamental(row, decision_at=receipt["decision_at"])[0]
                       for row in candidates)
        episodes.append({
            "session_date": receipt["session_date"], "trial_sha256": receipt["trial_sha256"],
            "changed": baseline_symbol != challenger_symbol,
            "symbols": [value for value in (baseline_symbol, challenger_symbol) if value],
            "verified_candidates": complete,
            "eligible_candidates": len(candidates),
            "returns": returned,
        })
    changed = [e for e in episodes if e["changed"]]
    unique_symbols = {symbol for e in changed for symbol in e["symbols"]}
    span = (date.fromisoformat(episodes[-1]["session_date"]) -
            date.fromisoformat(episodes[0]["session_date"])).days
    checks = {
        "changed_episodes": len(changed),
        "unique_symbols": len(unique_symbols),
        "span_days": span,
        "coverage": (
            sum(e["verified_candidates"] for e in episodes) /
            max(1, sum(e["eligible_candidates"] for e in episodes))
        ),
    }
    evidence_ok = (
        len(changed) >= int(protocol["min_changed_episodes"])
        and len(unique_symbols) >= int(protocol["min_unique_symbols"])
        and span >= int(protocol["min_span_days"])
        and checks["coverage"] >= float(protocol["min_coverage"])
    )
    horizons = [_test_horizon(episodes, protocol, h) for h in (1, 2)]
    passing = evidence_ok and all(row["formal_pass"] for row in horizons)
    report: dict[str, Any] = {
        "schema_version": "stock-trading-fundamental-proof-v1",
        "status": "FORMAL_PASS" if passing else "FORMAL_FAIL",
        "policy_id": policy.POLICY_ID,
        "protocol_sha256": contracts.payload_sha256(protocol),
        "policy_sha256": trials.digest_file(Path(policy.__file__)),
        "fixed_n": n, "episodes": episodes, "quality": checks, "horizons": horizons,
        "formal_pass": passing, "prospective_only": True,
        "evidence_maturity": "STRESSED_FROZEN_SL_TP_VIRTUAL_OPEN_SLOT_NOT_FULL_LIVE_EXECUTION",
    }
    report["proof_sha256"] = contracts.payload_sha256(report)
    return report


def monitor_active(
    state: Mapping[str, Any], research_root: Path, *,
    protocol_path: Path = PROTOCOL_PATH,
) -> dict[str, Any]:
    """Prospective after-activation outcome guard; acts on a predeclared first cohort.

    No selective episode replacement, no outcome peeking before freeze.
    The production runtime separately handles immediate source failures.
    """
    validate_rollout(state)
    if state["phase"] != "ACTIVE":
        return {"status": "INACTIVE"}
    protocol = _read(protocol_path)
    research_protocol = _read(research_root / "data/investments/stock_trading_v2_fundamental_trial_config.json")
    if protocol != research_protocol:
        return {"status": "INTEGRITY_FAILURE", "reason": "monitor_protocol_mismatch"}
    if trials.digest_file(research_root / "scripts/stock_trading_fundamental_policy.py") != trials.digest_file(Path(policy.__file__)):
        return {"status": "INTEGRITY_FAILURE", "reason": "monitor_policy_hash_mismatch"}
    receipt_root = research_root / "data/investments/stock_trading_v2_fundamental_trials"
    experience_root = research_root / "data/investments/stock_trading_v2_experience"
    outcome_root = research_root / "data/investments/stock_trading_v2_outcomes"
    files = sorted(receipt_root.glob("*.json")) if receipt_root.exists() else []
    selected = []
    for path in files:
        row = _read(path)
        trials.validate_receipt(row, protocol)
        if policy.utc(row["decision_at"]) > policy.utc(state["activated_at"]):
            selected.append(row)
    monitor_n = 10
    if len(selected) < monitor_n:
        return {"status": "MONITOR_COLLECTING", "sessions": len(selected), "required": monitor_n}
    # Non-overlapping, chronological ten-session safety windows; recheck
    # every complete batch, not just the first post-promotion observation.
    reviewed = 0
    last_means: dict[int, float] = {}
    for offset in range(0, len(selected) - monitor_n + 1, monitor_n):
        sample = selected[offset:offset+monitor_n]
        deltas: dict[int, list[float]] = {1: [], 2: []}
        for receipt in sample:
            for horizon in (1, 2):
                baseline_status, baseline = _outcome_return(
                    receipt["baseline"], receipt, horizon=horizon,
                    event_root=experience_root, outcome_root=outcome_root)
                candidate_status, candidate = _outcome_return(
                    receipt["challenger"], receipt, horizon=horizon,
                    event_root=experience_root, outcome_root=outcome_root)
                if baseline_status == "UNPLAYABLE" or candidate_status == "UNPLAYABLE":
                    return {"status": "INTEGRITY_FAILURE", "reason": "monitor_unplayable_frozen_event"}
                if baseline_status == "PENDING" or candidate_status == "PENDING":
                    return {"status": "MONITOR_WAITING", "session_date": receipt["session_date"]}
                deltas[horizon].append(float(candidate) - float(baseline))
        means = {horizon: round(sum(vals) / len(vals), 6) for horizon, vals in deltas.items()}
        if any(mean < -0.25 for mean in means.values()):
            return {"status": "MONITOR_UNDERPERFORMANCE", "means_percent": means,
                    "sessions": monitor_n, "window_offset": offset}
        last_means = means
        reviewed += monitor_n
    return {"status": "MONITOR_HEALTHY", "means_percent": last_means,
            "sessions_reviewed": reviewed, "new_sessions": len(selected)}


def rollback(
    state: Mapping[str, Any], reason: str, *,
    now: datetime | None = None, path: Path = STATE_PATH,
    audit_path: Path = AUDIT_PATH,
) -> dict[str, Any]:
    validate_rollout(state)
    if state["phase"] != "ACTIVE":
        return dict(state)
    at = now or datetime.now(timezone.utc)
    updated = dict(state)
    updated["phase"] = "ROLLED_BACK"
    updated["rolled_back_at"] = contracts.iso_utc(at)
    updated["rollback_reason"] = str(reason)[:240]
    frozen = str(state.get("formal_proof_sha256") or "")
    blocked = set(state["blocked_trial_ids"])
    if frozen:
        blocked.add(frozen)
    updated["blocked_trial_ids"] = sorted(blocked)
    _atomic(path, updated)
    _audit("FUNDAMENTAL_AUTO_ROLLBACK", at=at, data={
        "policy_id": policy.POLICY_ID, "reason": updated["rollback_reason"],
        "formal_proof_sha256": frozen}, path=audit_path)
    return updated


def promote(
    research_root: Path, *,
    state_path: Path = STATE_PATH, protocol_path: Path = PROTOCOL_PATH,
    audit_path: Path = AUDIT_PATH, now: datetime | None = None,
) -> dict[str, Any]:
    at = now or datetime.now(timezone.utc)
    state = _read(state_path)
    validate_rollout(state)
    if state["phase"] == "ROLLED_BACK":
        return {"status": "BLOCKED_AFTER_ROLLBACK", "reason": state.get("rollback_reason")}
    if state["phase"] == "ACTIVE":
        monitor_result = monitor_active(state, research_root, protocol_path=protocol_path)
        if monitor_result.get("status") in {"MONITOR_UNDERPERFORMANCE", "INTEGRITY_FAILURE"}:
            reason = "post_promotion_" + str(monitor_result.get("status")) + ":" + str(monitor_result.get("reason") or monitor_result.get("means_percent") or "")
            updated = rollback(state, reason, now=at, path=state_path, audit_path=audit_path)
            return {"status": "AUTO_ROLLBACK", "reason": reason,
                    "phase": updated["phase"], "monitor": monitor_result}
        return {"status": "ALREADY_ACTIVE", "proof": state.get("formal_proof_sha256"),
                "monitor": monitor_result}
    report = evaluate(research_root=research_root, protocol_path=protocol_path)
    if report["status"] != "FORMAL_PASS":
        return {"status": report["status"], "quality": report.get("quality"),
                "horizons": report.get("horizons")}
    if report["proof_sha256"] in state["blocked_trial_ids"]:
        return {"status": "TRIAL_PREVIOUSLY_BLOCKED"}
    updated = dict(state)
    updated["phase"] = "ACTIVE"
    updated["activated_at"] = contracts.iso_utc(at)
    updated["formal_proof_sha256"] = report["proof_sha256"]
    updated["active_policy_sha256"] = report["policy_sha256"]
    updated["trial_frozen_through"] = report["episodes"][-1]["session_date"]
    updated["promotion_quality"] = report["quality"]
    updated["promotion_horizons"] = report["horizons"]
    validate_rollout(updated)
    _atomic(state_path, updated)
    _audit("FUNDAMENTAL_AUTO_PROMOTED", at=at, data={
        "policy_id": policy.POLICY_ID, "proof_sha256": report["proof_sha256"],
        "trial_frozen_through": updated["trial_frozen_through"],
        "quality": report["quality"], "horizons": report["horizons"]}, path=audit_path)
    return {"status": "AUTO_PROMOTED", "proof_sha256": report["proof_sha256"],
            "policy_id": policy.POLICY_ID}


def validate_live_binding(
    opportunity: Mapping[str, Any], evidence: Mapping[str, Any],
    *,
    now: datetime,
) -> tuple[bool, str]:
    """Fail-closed on upstream corruption; weaker issuers are filtered later."""
    if opportunity.get("market") != "US" or evidence.get("market") != "US":
        return False, "US_market_mismatch"
    age = (now - policy.utc(opportunity.get("generated_at"))).total_seconds()
    if age < -60 or age > 360 * 60:
        return False, "fundamental_opportunity_too_old"
    opportunity_body = dict(opportunity)
    opportunity_digest = opportunity_body.pop("opportunity_sha256", None)
    if not opportunity_digest or contracts.payload_sha256(opportunity_body) != opportunity_digest:
        return False, "fundamental_opportunity_integrity_failed"
    if evidence.get("evidence_sha256") != opportunity.get("source_evidence_sha256"):
        return False, "fundamental_evidence_hash_disagrees"
    body = dict(evidence)
    declared = body.pop("evidence_sha256", None)
    if not declared or contracts.payload_sha256(body) != declared:
        return False, "fundamental_evidence_integrity_failed"
    if not evidence.get("source_fundamentals_sha256") or evidence.get("fundamental_score_influence") is not False:
        return False, "fundamental_unbound_source"
    by_symbol = {str(row.get("symbol")): row for row in evidence.get("candidates") or []}
    for candidate in opportunity.get("evaluated_candidates") or []:
        if candidate.get("eligible") is not True:
            continue
        match = by_symbol.get(str(candidate.get("symbol")))
        if not isinstance(match, Mapping) or match.get("fundamental_analysis") != candidate.get("fundamental_analysis"):
            return False, "fundamental_candidate_binding_failed"
    return True, "FUNDAMENTAL_PROVENANCE_OK"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-root", type=Path)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--protocol", type=Path, default=PROTOCOL_PATH)
    parser.add_argument("--audit", type=Path, default=AUDIT_PATH)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    state = _read(args.state)
    validate_rollout(state)
    if args.verify:
        print(json.dumps({"status": "OK", "phase": state["phase"],
                          "policy_id": state["policy_id"]}, sort_keys=True))
    else:
        if args.research_root is None:
            raise SystemExit("--research-root required for promotion evaluation")
        print(json.dumps(promote(args.research_root, state_path=args.state,
                                 protocol_path=args.protocol, audit_path=args.audit),
                         ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
