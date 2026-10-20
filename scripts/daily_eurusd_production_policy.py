#!/usr/bin/env python3
"""TR-03's bounded, future-only production admission component.

This is not a directional engine or an execution writer. The Daily writer owns
the file and consumes this policy AFTER Belief-first direction and verified EPE.
An admission floor may only abstain from an existing direction. Candidate gates
use canonical, manifest-bound future trades; skipped monitoring cases use a
separately labelled prospective counterfactual evaluated by the SAME lifecycle
callback supplied by Daily. No A/B/C, X or generic Evolution state is written.
"""
from __future__ import annotations

import copy
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Callable, Mapping

try:
    from briefrooms_evolution_contracts import (
        EvolutionCandidate, ProductionVersion, PromotionGate, RollbackEvent,
        sha256, stable_id,
    )
    from statistical_promotion_gate import deterministic_bootstrap_mean_ci, _max_drawdown
except ImportError:
    from scripts.briefrooms_evolution_contracts import (
        EvolutionCandidate, ProductionVersion, PromotionGate, RollbackEvent,
        sha256, stable_id,
    )
    from scripts.statistical_promotion_gate import deterministic_bootstrap_mean_ci, _max_drawdown

SCHEMA_VERSION = "eurusd-production-policy-state-v1"
POLICY_SCHEMA = "eurusd-production-admission-margin-v1"
MANIFEST_SCHEMA = "eurusd-production-decision-manifest-v1"
ENGINE_VERSION = "eurusd-daily-spot-v1.9.1"
COMPONENT_ID = "TR-03:production_admission_margin"
PROMOTION_ROUTE = "daily_eurusd_canonical_writer"
DEFAULT_PATH = Path("data/investments/eurusd_production_policy_state.json")
MINIMUM_FLOOR, MAXIMUM_FLOOR = 3.0, 6.0
PRIMARY_N, PRIMARY_DAYS = 40, 15
MONITOR_N, MONITOR_DAYS = 20, 10
PURGE_HOURS = 27
MAX_ATTEMPTS = 3
COOLDOWN_DAYS = 30
MIN_DELTA_R = 0.05
MIN_SLICE_N = 5
MAX_SLICE_DEGRADATION_R = 0.05
MAX_DRAWDOWN_DEGRADATION_R = 0.0
MODEL_UNCERTAINTY_PENALTY_R = 0.02
# At most 3 attempts at each of 3 bounded transitions; fixed final tests only.
CONFIDENCE = 1.0 - 0.05 / (3 * MAX_ATTEMPTS)
BOOTSTRAP_SAMPLES = 4999
EVALUATOR_SHA256 = sha256({
    "policy_module": Path(__file__).read_bytes().hex(),
    "statistical_gate": Path(deterministic_bootstrap_mean_ci.__code__.co_filename).read_bytes().hex(),
})
AUTHORITY = {
    "owner_module": "TR-03", "writer": PROMOTION_ROUTE,
    "direction_mutation_allowed": False, "risk_mutation_allowed": False,
    "trade_execution_authority": False, "historical_backfill": False,
    "abc_writeback": False, "x_writeback": False,
    "bounded_future_admission_policy": True,
}


def _time(value: str | datetime) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone-aware policy timestamp required")
    return result.astimezone(timezone.utc)


def _iso(value: str | datetime) -> str:
    return _time(value).isoformat().replace("+00:00", "Z")


def _now(value: str | datetime | None) -> str:
    return _iso(value or datetime.now(timezone.utc))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _number(value: Any, name: str) -> float:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), name + " must be numeric")
    result = float(value)
    _require(math.isfinite(result), name + " must be finite")
    return result


def _policy(floor: float) -> dict[str, Any]:
    _require(floor in {3.0, 4.0, 5.0, 6.0}, "admission floor outside bounded policy family")
    return {"schema_version": POLICY_SCHEMA, "minimum_score_distance": floor}


def _version(policy: Mapping[str, Any], at: str, candidate_id: str, parent: str | None) -> dict[str, Any]:
    vid = stable_id("eurusd-policy", {"policy": policy, "at": at, "candidate": candidate_id})
    result = ProductionVersion(
        version_id=vid, component_id=COMPONENT_ID, candidate_id=candidate_id,
        activated_at=at, parent_version_id=parent, owner_module="TR-03",
        materialization_route=PROMOTION_ROUTE, payload_sha256=sha256(policy),
    ).to_dict()
    result.update(policy=dict(policy), monitor_case_ids=[], monitor_reviews=[])
    return result


def default_state(now: str | datetime | None = None) -> dict[str, Any]:
    at = _now(now)
    baseline = _version(_policy(MINIMUM_FLOOR), at, "eurusd-policy-baseline", None)
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "activated_at": at, "updated_at": at,
        "mode": "PROSPECTIVE_POLICY_EVIDENCE", "execution_authority": False,
        "authority": dict(AUTHORITY), "active_version_id": baseline["version_id"],
        "versions": {baseline["version_id"]: baseline}, "candidates": {},
        "cases": {}, "active_case_id": None, "events": [], "rollback_events": [],
    }
    _ensure_candidate(state, at)
    return state


def load_state(path: Path = DEFAULT_PATH, now: str | datetime | None = None) -> dict[str, Any]:
    if not path.exists():
        return default_state(now)
    state = json.loads(path.read_text(encoding="utf-8"))
    validate_state(state)
    return state


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    validate_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _effective_version(state: Mapping[str, Any], observed_at: str | datetime | None = None) -> Mapping[str, Any]:
    if observed_at is None:
        return state["versions"][state["active_version_id"]]
    # Both promotion AND rollback have a future-only effective boundary. A
    # rollback target's original activation time does not backdate its return.
    baseline = next(v for v in state["versions"].values() if v.get("parent_version_id") is None)
    selected = baseline
    rollbacks = {r["rollback_id"]: r for r in state["rollback_events"]}
    for event in state["events"]:
        if _time(event["at"]) > _time(observed_at):
            continue
        if event["event"] == "PROMOTION":
            selected = state["versions"][event["version_id"]]
        elif event["event"] == "ROLLBACK":
            selected = state["versions"][rollbacks[event["rollback_id"]]["to_version_id"]]
    return selected


def _allows(policy: Mapping[str, Any], score: float, direction: str) -> bool:
    if direction not in {"LONG", "SHORT"}:
        return False
    signed = score - 50.0 if direction == "LONG" else 50.0 - score
    return signed >= float(policy["minimum_score_distance"]) - 1e-9


def policy_context(
    state: Mapping[str, Any], score: float, direction: str,
    observed_at: str | datetime | None = None,
) -> dict[str, Any]:
    version = _effective_version(state, observed_at)
    candidate = next((x for x in state["candidates"].values() if x["status"] == "OOS_RUNNING" and x["baseline_version"] == version["version_id"]), None)
    return {
        "version_id": version["version_id"], "policy_sha256": version["payload_sha256"],
        "minimum_score_distance": version["policy"]["minimum_score_distance"],
        "allowed": _allows(version["policy"], _number(score, "score"), str(direction).upper()),
        "candidate_id": candidate["candidate_id"] if candidate else None,
        "direction_mutation_allowed": False, "risk_mutation_allowed": False,
        "activation_boundary": version["activated_at"],
    }


def _candidate_fingerprint(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {key: candidate[key] for key in (
        "candidate_id", "created_at", "activation_boundary", "baseline_version",
        "challenger_version", "proposed_change", "source_sha256", "metadata",
    )}


def _ensure_candidate(state: dict[str, Any], at: str) -> None:
    active = state["versions"][state["active_version_id"]]
    floor = float(active["policy"]["minimum_score_distance"])
    if floor >= MAXIMUM_FLOOR:
        return
    prior = [x for x in state["candidates"].values() if x["baseline_version"] == active["version_id"]]
    completed_trials = [x for x in prior if x["status"] in {"REJECTED", "ROLLED_BACK", "PROMOTED"}]
    if any(x["status"] in {"OOS_RUNNING", "PROMOTED"} for x in prior) or len(completed_trials) >= MAX_ATTEMPTS:
        return
    if completed_trials and _time(at) < max(_time(x["terminal_at"]) for x in completed_trials) + timedelta(days=COOLDOWN_DAYS):
        return
    proposed = _policy(floor + 1.0)
    spec = {
        "engine_version": ENGINE_VERSION, "primary_sample": PRIMARY_N,
        "minimum_days": PRIMARY_DAYS, "purge_hours": PURGE_HOURS,
        "minimum_delta_r": MIN_DELTA_R, "confidence": CONFIDENCE,
        "bootstrap_samples": BOOTSTRAP_SAMPLES, "minimum_slice_n": MIN_SLICE_N,
        "max_slice_degradation_r": MAX_SLICE_DEGRADATION_R,
        "max_drawdown_degradation_r": MAX_DRAWDOWN_DEGRADATION_R,
        "model_uncertainty_penalty_r": MODEL_UNCERTAINTY_PENALTY_R,
        "attempt": len(completed_trials) + 1, "fixed_final_test": True,
        "primary_source": "CANONICAL_ACTUAL_ONLY", "evaluator_sha256": EVALUATOR_SHA256,
    }
    identity = {"baseline": active["version_id"], "policy": proposed, "at": at, "spec": spec}
    cid = stable_id("eurusd-admission", identity)
    result = EvolutionCandidate(
        candidate_id=cid, candidate_type="bounded_production_admission_margin",
        source_module="TR-03", target_module="TR-03", component_id=COMPONENT_ID,
        created_at=at, activation_boundary=at, status="OOS_RUNNING",
        evaluator_profile="eurusd_actual_paired_fixed_blocks_v1", promotion_route=PROMOTION_ROUTE,
        baseline_version=active["version_id"], challenger_version=sha256(proposed),
        proposed_change=proposed, source_ref="tr03://canonical_manifest_bound_admissions",
        source_sha256=sha256(identity), automatic_promotion_allowed=True,
        metadata=spec,
    ).to_dict()
    result.update(evaluation_case_ids=[], gate=None, terminal_at=None, runtime_binding=None)
    result["frozen_sha256"] = sha256(_candidate_fingerprint(result))
    state["candidates"][cid] = result


def _manifest_ref(manifest: Mapping[str, Any]) -> dict[str, Any]:
    _require(manifest.get("schema_version") == MANIFEST_SCHEMA, "production decision manifest required")
    body = dict(manifest)
    recorded = body.pop("manifest_sha256", None)
    _require(bool(recorded) and sha256(body) == recorded, "decision manifest hash mismatch")
    _require(manifest.get("engine_version") == ENGINE_VERSION, "policy evidence engine semantics mismatch")
    _require(bool(manifest.get("decision_id")), "decision id required")
    _require(isinstance(manifest.get("runtime_code_sha256"), str) and len(manifest["runtime_code_sha256"]) == 64, "runtime code fingerprint required")
    _time(manifest["decision_at"])
    return {"decision_id": manifest["decision_id"], "manifest_sha256": recorded, "decision_at": manifest["decision_at"]}


def _select_purged(state: Mapping[str, Any], selected: list[str], case: Mapping[str, Any], maximum: int) -> bool:
    if len(selected) >= maximum:
        return False
    at = _time(case["frozen"]["decision_at"])
    return not selected or at >= _time(state["cases"][selected[-1]]["frozen"]["decision_at"]) + timedelta(hours=PURGE_HOURS)


def freeze_case(
    state: Mapping[str, Any], *, decision_manifest: Mapping[str, Any],
    position: Mapping[str, Any], score: float, now: str | datetime | None = None,
    slices: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Freeze verified, otherwise-admissible production opportunity before filter.

    One evaluation position at a time, regardless of actual or skipped admission.
    This does not prevent the Daily engine from managing its own real position.
    """
    result = copy.deepcopy(state)
    if result.get("active_case_id"):
        return result, None
    at = _now(now)
    ref = _manifest_ref(decision_manifest)
    _require(_time(ref["decision_at"]) <= _time(at), "future decision cannot be frozen")
    _require(position.get("direction") in {"LONG", "SHORT"}, "directional position required")
    _require(bool(position.get("trade_id")), "position trade id required")
    _require(_time(position["opened_at"]) <= _time(at), "future position")
    entry, stop = _number(position.get("entry"), "entry"), _number(position.get("stop"), "stop")
    _require(abs(entry - stop) > 0, "positive initial risk unit required")
    execution = decision_manifest.get("execution_price_engine") or {}
    _require(execution.get("execution_cost_contract") == "DAILY_EURUSD_BID_ASK_V1", "verified cost contract required")
    _require(execution.get("status") == "VERIFIED_FILL", "verified EPE required")
    context = policy_context(result, score, position["direction"], observed_at=ref["decision_at"])
    _require(decision_manifest.get("policy_version_id") == context["version_id"] and decision_manifest.get("policy_sha256") == context["policy_sha256"], "manifest policy version mismatch")
    vid = context["version_id"]
    votes = {vid: context["allowed"]}
    active = result["versions"][vid]
    parent = active.get("parent_version_id")
    if parent:
        votes[parent] = _allows(result["versions"][parent]["policy"], score, position["direction"])
    candidate = result["candidates"].get(context["candidate_id"])
    final = decision_manifest.get("final_decision") or {}
    _require(final.get("direction") == position["direction"] and abs(_number(final.get("score"), "final score") - score) < 1e-8, "case differs from final Belief direction/score")
    runtime_hash = decision_manifest["runtime_code_sha256"]
    if candidate and candidate.get("runtime_binding") and candidate["runtime_binding"]["runtime_code_sha256"] != runtime_hash:
        candidate["status"], candidate["terminal_at"] = "PARKED", at
        candidate["terminal_reason"] = "runtime_code_semantics_changed"
        candidate = None
    if candidate and _time(ref["decision_at"]) > _time(candidate["activation_boundary"]):
        votes[candidate["candidate_id"]] = _allows(candidate["proposed_change"], score, position["direction"])
    cid = stable_id("eurusd-case", {"manifest": ref, "trade_id": position["trade_id"]})
    if cid in result["cases"]:
        prior = result["cases"][cid]["frozen"]
        _require(prior["manifest"] == decision_manifest and prior["position"] == position and prior["score"] == score, "frozen case rewrite")
        return result, cid
    frozen = {
        **ref, "frozen_at": at, "position": copy.deepcopy(dict(position)),
        "score": _number(score, "score"), "risk_unit": abs(entry - stop),
        "production_version_id": vid, "production_admitted": context["allowed"],
        "votes": votes, "slices": {"direction": position["direction"], **dict(slices or {})},
        "manifest": copy.deepcopy(dict(decision_manifest)),
    }
    case = {"case_id": cid, "frozen": frozen, "frozen_sha256": sha256(frozen),
            "mode": "CANONICAL_REFERENCE_ONLY" if context["allowed"] else "PROSPECTIVE_COUNTERFACTUAL",
            "execution_authority": False,
            "runtime_position": copy.deepcopy(dict(position)), "status": "OPEN", "outcome": None,
            "coverage_failures": [], "last_coverage_at": at, "coverage_reason": None}
    result["cases"][cid] = case
    result["active_case_id"] = cid
    if candidate and context["allowed"] and candidate["candidate_id"] in votes and _select_purged(result, candidate["evaluation_case_ids"], case, PRIMARY_N):
        if candidate["runtime_binding"] is None:
            candidate["runtime_binding"] = {"runtime_code_sha256": runtime_hash, "case_id": cid, "bound_at": at}
        candidate["evaluation_case_ids"].append(cid)
    monitor_eligible = [cid for cid in active["monitor_case_ids"] if result["cases"][cid]["status"] != "CENSORED"]
    if parent and _time(ref["decision_at"]) > _time(active["activated_at"]) and _select_purged(result, monitor_eligible, case, MONITOR_N * (len(active["monitor_reviews"]) + 1)):
        active["monitor_case_ids"].append(cid)
    result["updated_at"] = at
    return result, cid


def _trade_manifest(trade: Mapping[str, Any]) -> Mapping[str, Any] | None:
    candidate = trade.get("decision_manifest")
    if not isinstance(candidate, Mapping):
        thesis = trade.get("entry_thesis") or {}
        candidate = thesis.get("decision_manifest") if isinstance(thesis, Mapping) else None
    return candidate if isinstance(candidate, Mapping) else None


def _outcome(case: Mapping[str, Any], trade: Mapping[str, Any], source: str, at: str) -> dict[str, Any]:
    frozen = case["frozen"]
    position = frozen["position"]
    _require(trade.get("direction") == position["direction"], "outcome direction mismatch")
    _require(str(trade.get("trade_id")) == str(position["trade_id"]), "outcome trade mismatch")
    _require(_time(trade["closed_at"]) >= _time(frozen["frozen_at"]), "outcome predates prospective freeze")
    _require(_time(trade["closed_at"]) <= _time(at), "future outcome")
    if trade.get("entry") is not None:
        _require(abs(_number(trade["entry"], "trade entry") - float(position["entry"])) < 1e-8, "outcome entry mismatch")
    manifest = _trade_manifest(trade)
    _require(manifest is not None, "outcome decision manifest missing")
    ref = _manifest_ref(manifest)
    _require(ref["decision_id"] == frozen["decision_id"] and ref["manifest_sha256"] == frozen["manifest_sha256"], "outcome manifest mismatch")
    exit_price = _number(trade.get("exit_price"), "exit_price")
    sign = 1 if position["direction"] == "LONG" else -1
    net_r = sign * (exit_price - float(position["entry"])) / float(frozen["risk_unit"])
    return {"source": source, "closed_at": _iso(trade["closed_at"]), "bound_at": at,
            "net_r": net_r, "exit_price": exit_price, "exit_reason": trade.get("exit_reason"),
            "trade_sha256": sha256(trade), "manifest_sha256": ref["manifest_sha256"],
            "live_executed": False, "canonical_actual_reference": source == "CANONICAL_ACTUAL",
            "execution_authority": False, "broker_fill_claim": False}


def bind_actual_history(state: Mapping[str, Any], history: Mapping[str, Any], now: str | datetime | None = None) -> dict[str, Any]:
    result, at = copy.deepcopy(state), _now(now)
    trades = {str(x.get("trade_id")): x for x in history.get("trades", []) if isinstance(x, Mapping)}
    for case in result["cases"].values():
        if case["outcome"] is not None or not case["frozen"]["production_admitted"]:
            continue
        trade = trades.get(str(case["frozen"]["position"]["trade_id"]))
        if trade is None:
            continue
        case["outcome"] = _outcome(case, trade, "CANONICAL_ACTUAL", at)
        case["status"] = "CLOSED"
        if result["active_case_id"] == case["case_id"]:
            result["active_case_id"] = None
    result["updated_at"] = at
    return result


def validate_history_bindings(state: Mapping[str, Any], history: Mapping[str, Any]) -> None:
    """Cross-file reconciliation for the canonical pre-persist validator."""
    trades = {str(x.get("trade_id")): x for x in history.get("trades", []) if isinstance(x, Mapping)}
    for case in state["cases"].values():
        outcome = case.get("outcome")
        if not outcome or outcome["source"] != "CANONICAL_ACTUAL":
            continue
        trade = trades.get(str(case["frozen"]["position"]["trade_id"]))
        _require(trade is not None, "canonical policy outcome is absent from actual history")
        _require(_outcome(case, trade, "CANONICAL_ACTUAL", outcome["bound_at"]) == outcome, "canonical policy outcome differs from actual history")


def settle_cases(
    state: Mapping[str, Any], *, evaluator: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Resolve skipped opportunities using Daily's supplied lifecycle callback.

    Canonical actual cases NEVER settle from a virtual result. The callback
    explicitly reports coverage; missing evidence never becomes a zero outcome.
    Fast actual closes may differ from the shared monitor cadence; their actual
    manifest-bound result remains authoritative for primary promotion evidence.
    """
    result, at = copy.deepcopy(state), _now(now)
    cid = result.get("active_case_id")
    if not cid:
        return result
    case = result["cases"][cid]
    response = evaluator(copy.deepcopy(case["runtime_position"]), copy.deepcopy(case))
    _require(isinstance(response, Mapping), "lifecycle callback contract required")
    if response.get("market_open") is False:
        return result
    if response.get("coverage_complete") is not True:
        if at not in case["coverage_failures"]:
            case["coverage_failures"].append(at)
        case["coverage_reason"] = str(response.get("reason") or "incomplete_observations")
        case["status"] = "DATA_INCOMPLETE"
    else:
        case["last_coverage_at"] = at
        case["coverage_reason"] = None
        case["status"] = "OPEN"
        if isinstance(response.get("position"), Mapping):
            updated = dict(response["position"])
            for key in ("trade_id", "opened_at", "direction", "entry", "stop", "target", "decision_manifest"):
                _require(updated.get(key) == case["runtime_position"].get(key), "lifecycle callback changed frozen position facts: " + key)
            case["runtime_position"] = updated
        trade = response.get("closed_trade")
        if isinstance(trade, Mapping) and not case["frozen"]["production_admitted"]:
            case["outcome"] = _outcome(case, trade, "PROSPECTIVE_COUNTERFACTUAL", at)
            case["status"] = "CLOSED"
            result["active_case_id"] = None
    result["updated_at"] = at
    return result


def _values(case: Mapping[str, Any], baseline: str, challenger: str) -> tuple[float, float, float]:
    value = float(case["outcome"]["net_r"])
    votes = case["frozen"]["votes"]
    base, candidate = value if votes[baseline] else 0.0, value if votes[challenger] else 0.0
    # Predeclared uncertainty allowance, NOT a claim about broker commission.
    stressed = candidate - base - (MODEL_UNCERTAINTY_PENALTY_R if votes[baseline] != votes[challenger] else 0.0)
    return base, candidate, stressed


def _paired_metrics(cases: list[Mapping[str, Any]], baseline: str, challenger: str, seed: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    rows = [_values(case, baseline, challenger) for case in cases]
    delta = [row[2] for row in rows]
    by_day: dict[str, list[float]] = defaultdict(list)
    for case, value in zip(cases, delta):
        by_day[case["frozen"]["decision_at"][:10]].append(value)
    blocks = [fmean(x) for _, x in sorted(by_day.items())]
    low, high = deterministic_bootstrap_mean_ci(blocks, samples=BOOTSTRAP_SAMPLES, confidence=CONFIDENCE, seed_text=seed) if blocks else (None, None)
    midpoint = len(blocks) // 2
    def profit_factor(values: list[float]) -> dict[str, Any]:
        wins = sum(max(0.0, value) for value in values)
        losses = -sum(min(0.0, value) for value in values)
        return {"value": wins / losses if losses > 0 else None,
                "status": "DEFINED" if losses > 0 else "NO_LOSSES" if wins > 0 else "NO_REALIZED_PNL"}
    champion_values, challenger_values = [x[0] for x in rows], [x[1] for x in rows]
    metrics = {
        "n": len(cases), "independent_days": len(blocks), "purge_hours": PURGE_HOURS,
        "champion_mean_net_r": fmean(x[0] for x in rows) if rows else None,
        "challenger_mean_net_r": fmean(x[1] for x in rows) if rows else None,
        "mean_delta_net_r": fmean(x[1] - x[0] for x in rows) if rows else None,
        "stressed_mean_delta_r": fmean(delta) if delta else None,
        "champion_max_drawdown_r": abs(_max_drawdown(champion_values)),
        "challenger_max_drawdown_r": abs(_max_drawdown(challenger_values)),
        "champion_profit_factor": profit_factor(champion_values),
        "challenger_profit_factor": profit_factor(challenger_values),
        "block_ci_low_r": low, "block_ci_high_r": high,
        "first_half_block_mean_r": fmean(blocks[:midpoint]) if midpoint else None,
        "second_half_block_mean_r": fmean(blocks[midpoint:]) if midpoint else None,
        "actual_outcomes": sum(x["outcome"]["source"] == "CANONICAL_ACTUAL" for x in cases),
        "counterfactual_outcomes": sum(x["outcome"]["source"] == "PROSPECTIVE_COUNTERFACTUAL" for x in cases),
        "dataset_sha256": sha256([{ "case": c["frozen_sha256"], "outcome": c["outcome"]} for c in cases]),
    }
    groups: dict[str, list[float]] = defaultdict(list)
    for case, value in zip(cases, delta):
        for key, label in case["frozen"]["slices"].items():
            groups[f"{key}:{label}"].append(value)
    checks, blockers = [], []
    for segment, values in sorted(groups.items()):
        mean = fmean(values)
        # Every observed protected slice is visible. An unmeasured small slice
        # cannot authorize promotion where the candidate changes admissions.
        informative = any(abs(x) > 1e-12 for x in values)
        enough = len(values) >= MIN_SLICE_N or not informative
        passed = enough and mean >= -MAX_SLICE_DEGRADATION_R
        checks.append({"segment": segment, "n": len(values), "mean_delta_r": mean,
                       "status": "PASS" if passed else "INSUFFICIENT" if not enough else "FAIL"})
        if not passed:
            blockers.append("protected_slice:" + segment)
    return metrics, checks, blockers


def _candidate_gate(state: Mapping[str, Any], candidate: Mapping[str, Any], at: str, *, enforce_current_evaluator: bool = True) -> dict[str, Any]:
    selected = [state["cases"][cid] for cid in candidate["evaluation_case_ids"]]
    resolved = [x for x in selected if x["outcome"] is not None]
    blockers: list[str] = []
    if len(selected) < PRIMARY_N or len(resolved) < PRIMARY_N:
        blockers.append("fixed_prospective_sample_incomplete")
    if enforce_current_evaluator and candidate["metadata"]["evaluator_sha256"] != EVALUATOR_SHA256:
        blockers.append("evaluator_semantics_changed")
    # Partial samples are progress only: no early testing of the statistic.
    metrics, checks = {"selected_n": len(selected), "resolved_n": len(resolved)}, []
    status = "COLLECTING"
    if not blockers:
        for case in selected:
            frozen = case["frozen"]
            _require(frozen["production_admitted"] and case["outcome"]["source"] == "CANONICAL_ACTUAL", "primary gate requires canonical actuals")
            _require(_time(frozen["decision_at"]) > _time(candidate["activation_boundary"]), "preboundary promotion evidence")
            _require(frozen["production_version_id"] == candidate["baseline_version"], "baseline evidence version mismatch")
            _require(candidate.get("runtime_binding") is not None and frozen["manifest"]["runtime_code_sha256"] == candidate["runtime_binding"]["runtime_code_sha256"], "mixed runtime code evidence")
        metrics, checks, blockers = _paired_metrics(selected, candidate["baseline_version"], candidate["candidate_id"], candidate["frozen_sha256"])
        if metrics["independent_days"] < PRIMARY_DAYS:
            blockers.append("minimum_independent_days")
        if float(metrics["stressed_mean_delta_r"]) < MIN_DELTA_R:
            blockers.append("minimum_economic_improvement")
        if float(metrics["block_ci_low_r"]) <= 0:
            blockers.append("block_confidence_not_positive")
        if metrics["challenger_max_drawdown_r"] > metrics["champion_max_drawdown_r"] + MAX_DRAWDOWN_DEGRADATION_R + 1e-9:
            blockers.append("drawdown_degradation")
        if float(metrics["first_half_block_mean_r"]) <= 0 or float(metrics["second_half_block_mean_r"]) <= 0:
            blockers.append("temporal_stability")
        status = "FAIL" if blockers else "PASS"
    return PromotionGate(
        gate_id=stable_id("eurusd-gate", {"candidate": candidate["frozen_sha256"], "metrics": metrics}),
        candidate_id=candidate["candidate_id"], evaluated_at=at, status=status,
        evaluator_profile=candidate["evaluator_profile"], prospective_only=True,
        minimum_sample=PRIMARY_N, observed_sample=len(resolved),
        criteria={**dict(candidate["metadata"]), "runtime_binding": copy.deepcopy(candidate.get("runtime_binding"))}, metrics=metrics,
        segment_checks=tuple(checks), blockers=tuple(blockers), source_sha256=sha256(selected),
    ).to_dict()


def _rollback(state: dict[str, Any], at: str, reason: str, metrics: Mapping[str, Any]) -> None:
    current = state["versions"][state["active_version_id"]]
    parent_id = current.get("parent_version_id")
    if not parent_id:
        return
    event = RollbackEvent(
        rollback_id=stable_id("eurusd-rollback", {"from": current["version_id"], "at": at, "reason": reason}),
        component_id=COMPONENT_ID, from_version_id=current["version_id"], to_version_id=parent_id,
        candidate_id=current["candidate_id"], detected_at=at, effective_at=at,
        reason=reason, metrics=dict(metrics), owner_module="TR-03",
        materialization_route=PROMOTION_ROUTE, automatic=True,
    ).to_dict()
    state["rollback_events"].append(event)
    current["status"] = "ROLLED_BACK"
    state["versions"][parent_id]["status"] = "ACTIVE"
    state["active_version_id"] = parent_id
    failed = state["candidates"][current["candidate_id"]]
    failed["status"], failed["terminal_at"] = "ROLLED_BACK", at
    for candidate in state["candidates"].values():
        if candidate["status"] == "OOS_RUNNING" and candidate["baseline_version"] == current["version_id"]:
            candidate["status"], candidate["terminal_at"] = "PARKED", at
    state["events"].append({"event": "ROLLBACK", "at": at, "rollback_id": event["rollback_id"]})


def advance_governance(
    state: Mapping[str, Any], now: str | datetime | None = None, *,
    runtime_code_sha256: str | None = None,
) -> dict[str, Any]:
    result, at = copy.deepcopy(state), _now(now)
    validate_state(result)
    if runtime_code_sha256 is not None:
        _require(len(runtime_code_sha256) == 64, "runtime code fingerprint required")
        current = result["versions"][result["active_version_id"]]
        # A returned parent may itself belong to the obsolete code epoch.
        # Walk back to an evaluated compatible version or the baseline BEFORE
        # the next admission; do not leave one obsolete ancestor active.
        while current.get("runtime_code_sha256") and current["runtime_code_sha256"] != runtime_code_sha256:
            _rollback(result, at, "INVARIANT_FAILURE", {"reason": "runtime_code_semantics_changed_before_admission"})
            current = result["versions"][result["active_version_id"]]
        for candidate in result["candidates"].values():
            binding = candidate.get("runtime_binding")
            if candidate["status"] == "OOS_RUNNING" and binding and binding["runtime_code_sha256"] != runtime_code_sha256:
                candidate["status"], candidate["terminal_at"] = "PARKED", at
                candidate["terminal_reason"] = "runtime_code_semantics_changed"
        active_case = result["cases"].get(result.get("active_case_id"))
        if (active_case and not active_case["frozen"]["production_admitted"]
                and active_case["frozen"]["manifest"]["runtime_code_sha256"] != runtime_code_sha256):
            # The old callback implementation is no longer available. A new
            # lifecycle must not manufacture an old-epoch counterfactual.
            # Preserve the frozen case and explicit missing outcome; release
            # the evaluation slot rather than permanently blocking learning.
            active_case["status"] = "CENSORED"
            active_case["censoring"] = {
                "at": at, "reason": "RUNTIME_CODE_EPOCH_CHANGED",
                "from_runtime_code_sha256": active_case["frozen"]["manifest"]["runtime_code_sha256"],
                "to_runtime_code_sha256": runtime_code_sha256,
                "execution_authority": False, "excluded_from_evaluation": True,
            }
            result["active_case_id"] = None
    active_case = result["cases"].get(result.get("active_case_id"))
    coverage_blocked = bool(active_case and active_case["status"] == "DATA_INCOMPLETE")
    gap = False
    if active_case and len(active_case["coverage_failures"]) >= 3:
        gap = _time(at) - _time(active_case["last_coverage_at"]) >= timedelta(hours=2)
        if gap:
            _rollback(result, at, "DATA_QUALITY_FAILURE", {"case_id": active_case["case_id"], "missing_outcome": True})
    active = result["versions"][result["active_version_id"]]
    if active_case and active.get("runtime_code_sha256") and active_case["frozen"]["manifest"]["runtime_code_sha256"] != active["runtime_code_sha256"]:
        _rollback(result, at, "INVARIANT_FAILURE", {"reason": "runtime_code_semantics_changed"})
        active = result["versions"][result["active_version_id"]]
    if active.get("parent_version_id") and not gap:
        reviewed = len(active["monitor_reviews"])
        eligible = [cid for cid in active["monitor_case_ids"] if result["cases"][cid]["status"] != "CENSORED"]
        ids = eligible[reviewed * MONITOR_N:(reviewed + 1) * MONITOR_N]
        if len(ids) == MONITOR_N and all(result["cases"][cid]["outcome"] is not None for cid in ids):
            cases = [result["cases"][cid] for cid in ids]
            metrics, checks, blockers = _paired_metrics(cases, active["parent_version_id"], active["version_id"], sha256(ids))
            review = {"at": at, "case_ids": ids, "metrics": metrics, "segment_checks": checks}
            active["monitor_reviews"].append(review)
            if metrics["independent_days"] >= MONITOR_DAYS and float(metrics["stressed_mean_delta_r"]) <= -MIN_DELTA_R and float(metrics["block_ci_high_r"]) < 0:
                _rollback(result, at, "POST_PROMOTION_DEGRADATION", metrics)
            elif any(x["status"] == "FAIL" for x in checks):
                _rollback(result, at, "SEGMENT_SAFETY_FAILURE", {"checks": checks})
            elif metrics["independent_days"] >= MONITOR_DAYS and metrics["challenger_max_drawdown_r"] > metrics["champion_max_drawdown_r"] + MAX_DRAWDOWN_DEGRADATION_R + 1e-9:
                _rollback(result, at, "SEGMENT_SAFETY_FAILURE", {"reason": "drawdown_degradation", **metrics})
    for candidate in result["candidates"].values():
        if candidate["status"] != "OOS_RUNNING" or candidate["baseline_version"] != result["active_version_id"]:
            continue
        if candidate["metadata"]["evaluator_sha256"] != EVALUATOR_SHA256:
            candidate["status"], candidate["terminal_at"] = "PARKED", at
            candidate["terminal_reason"] = "evaluator_semantics_changed"
            continue
        gate = _candidate_gate(result, candidate, at)
        if gate["status"] == "PASS" and coverage_blocked:
            gate["status"] = "HOLD"
            gate["blockers"] = ["pending_policy_case_data_incomplete"]
            gate["gate_id"] = stable_id("eurusd-gate", {"candidate": candidate["frozen_sha256"], "metrics": gate["metrics"], "blockers": gate["blockers"]})
        candidate["gate"] = gate
        if gate["status"] == "PASS" and not gap:
            _require(candidate["challenger_version"] == sha256(candidate["proposed_change"]), "candidate exact policy hash mismatch")
            parent_id = result["active_version_id"]
            version = _version(candidate["proposed_change"], at, candidate["candidate_id"], parent_id)
            version["runtime_code_sha256"] = candidate["runtime_binding"]["runtime_code_sha256"]
            result["versions"][parent_id]["status"] = "SUPERSEDED"
            result["versions"][version["version_id"]] = version
            result["active_version_id"] = version["version_id"]
            candidate["status"], candidate["terminal_at"] = "PROMOTED", at
            result["events"].append({"event": "PROMOTION", "at": at, "candidate_id": candidate["candidate_id"], "gate_id": gate["gate_id"], "policy_sha256": version["payload_sha256"], "version_id": version["version_id"]})
        elif gate["status"] == "FAIL":
            candidate["status"], candidate["terminal_at"] = "REJECTED", at
    _ensure_candidate(result, at)
    result["updated_at"] = at
    validate_transition(state, result)
    return result


def _validate_monitoring(state: Mapping[str, Any], version: Mapping[str, Any]) -> None:
    """Reconcile fixed monitoring windows from their frozen source cases."""
    selected, reviews = version["monitor_case_ids"], version["monitor_reviews"]
    parent = version.get("parent_version_id")
    if not parent:
        _require(not selected and not reviews, "baseline cannot carry a parent comparison")
        return
    _require(len(selected) == len(set(selected)), "duplicate monitor evidence")
    eligible = []
    previous_raw_at = previous_eligible_at = None
    for cid in selected:
        _require(cid in state["cases"], "monitor evidence missing")
        case = state["cases"][cid]
        frozen = case["frozen"]
        at = _time(frozen["decision_at"])
        _require(at > _time(version["activated_at"]), "preactivation monitoring evidence")
        _require(previous_raw_at is None or at >= previous_raw_at, "monitor evidence order changed")
        previous_raw_at = at
        _require(frozen["production_version_id"] == version["version_id"], "monitor case belongs to another production version")
        _require(_effective_version(state, at)["version_id"] == version["version_id"], "monitor case was frozen outside its production epoch")
        _require(frozen["manifest"]["runtime_code_sha256"] == version["runtime_code_sha256"], "mixed monitoring runtime code")
        _require(version["version_id"] in frozen["votes"] and parent in frozen["votes"], "monitor comparison lacks frozen paired votes")
        if case["status"] == "CENSORED":
            continue
        _require(previous_eligible_at is None or at >= previous_eligible_at + timedelta(hours=PURGE_HOURS), "overlapping monitoring labels")
        previous_eligible_at = at
        eligible.append(cid)
    _require(len(eligible) <= MONITOR_N * (len(reviews) + 1), "monitor sampled beyond its fixed window")
    previous_review_at = _time(version["activated_at"])
    for index, review in enumerate(reviews):
        ids = eligible[index * MONITOR_N:(index + 1) * MONITOR_N]
        _require(len(ids) == MONITOR_N and review.get("case_ids") == ids, "monitor review differs from its fixed selected window")
        cases = [state["cases"][cid] for cid in ids]
        _require(all(case["outcome"] is not None for case in cases), "monitor review uses unresolved evidence")
        review_at = _time(review["at"])
        _require(previous_review_at <= review_at <= _time(state["updated_at"]), "monitor review time regression")
        _require(all(_time(case["outcome"]["bound_at"]) <= review_at for case in cases), "monitor review predates outcome knowledge")
        previous_review_at = review_at
        metrics, checks, _ = _paired_metrics(cases, parent, version["version_id"], sha256(ids))
        _require(review == {"at": review["at"], "case_ids": ids, "metrics": metrics, "segment_checks": checks}, "monitor review no longer independently reconciles")


def _validate_governance_timeline(state: Mapping[str, Any]) -> None:
    """Replay authorizations as a state machine, not a list of last targets."""
    baselines = [v for v in state["versions"].values() if v.get("parent_version_id") is None]
    _require(len(baselines) == 1, "one production baseline is required")
    baseline = baselines[0]
    _require(_time(baseline["activated_at"]) == _time(state["activated_at"]), "baseline activation differs from policy epoch")
    head = baseline["version_id"]
    expected_status = {head: "ACTIVE"}
    seen_versions, seen_candidates, seen_rollbacks = {head}, set(), set()
    rollbacks = {event["rollback_id"]: event for event in state["rollback_events"]}
    _require(len(rollbacks) == len(state["rollback_events"]), "duplicate rollback authorization")
    previous_at = _time(state["activated_at"])
    for event in state["events"]:
        at = _time(event["at"])
        _require(previous_at <= at <= _time(state["updated_at"]), "governance event time regression")
        previous_at = at
        if event["event"] == "PROMOTION":
            vid, cid = event["version_id"], event["candidate_id"]
            _require(vid not in seen_versions, "production version promotion replay")
            _require(cid not in seen_candidates, "candidate promotion authorization replay")
            version, candidate = state["versions"][vid], state["candidates"][cid]
            gate = candidate.get("gate") or {}
            _require(version["parent_version_id"] == head and candidate["baseline_version"] == head, "promotion does not descend from current governed head")
            _require(version["candidate_id"] == cid and candidate["status"] in {"PROMOTED", "ROLLED_BACK"}, "promotion candidate lifecycle mismatch")
            _require(at == _time(version["activated_at"]) == _time(gate["evaluated_at"]), "promotion activation differs from frozen passing gate")
            _require(gate["status"] == "PASS" and event["gate_id"] == gate["gate_id"], "promotion does not reference exact passing gate")
            _require(event["policy_sha256"] == version["payload_sha256"] == candidate["challenger_version"], "promotion policy differs from exact evaluated hash")
            _require(version["runtime_code_sha256"] == candidate["runtime_binding"]["runtime_code_sha256"], "promotion runtime differs from evaluated cohort")
            _require(all(_time(state["cases"][case_id]["outcome"]["bound_at"]) <= at for case_id in candidate["evaluation_case_ids"]), "promotion predates outcome knowledge")
            expected_status[head], expected_status[vid] = "SUPERSEDED", "ACTIVE"
            seen_versions.add(vid)
            seen_candidates.add(cid)
            head = vid
        elif event["event"] == "ROLLBACK":
            rid = event["rollback_id"]
            _require(rid in rollbacks and rid not in seen_rollbacks, "rollback authorization replay or missing record")
            rollback = rollbacks[rid]
            source = state["versions"][head]
            _require(rollback["from_version_id"] == head and rollback["to_version_id"] == source["parent_version_id"], "rollback does not start at current governed head")
            _require(rollback["candidate_id"] == source["candidate_id"], "rollback candidate differs from active component")
            _require(at == _time(rollback["effective_at"]) and _time(rollback["detected_at"]) <= at, "rollback effective boundary mismatch")
            _require(rollback["trade_execution_authority"] is False and rollback["owner_module"] == "TR-03" and rollback["materialization_route"] == PROMOTION_ROUTE, "rollback authority drift")
            expected_status[head] = "ROLLED_BACK"
            head = rollback["to_version_id"]
            expected_status[head] = "ACTIVE"
            seen_rollbacks.add(rid)
        else:
            raise ValueError("unknown production governance event")
    _require(seen_versions == set(state["versions"]), "production version lacks a unique timeline authorization")
    _require(seen_rollbacks == set(rollbacks), "orphan rollback authorization")
    _require(head == state["active_version_id"], "active policy differs from governed state-machine head")
    for vid, version in state["versions"].items():
        _require(vid == version["version_id"] and version["status"] == expected_status[vid], "production version status differs from governed timeline")


def validate_state(state: Mapping[str, Any]) -> None:
    _require(state.get("schema_version") == SCHEMA_VERSION, "production policy state schema mismatch")
    _require(state.get("authority") == AUTHORITY, "production policy authority mismatch")
    _require(state.get("mode") == "PROSPECTIVE_POLICY_EVIDENCE" and state.get("execution_authority") is False, "policy evidence cannot publish execution")
    _time(state["activated_at"])
    _require(state["active_version_id"] in state["versions"], "active production version missing")
    _require([v["version_id"] for v in state["versions"].values() if v["status"] == "ACTIVE"] == [state["active_version_id"]], "multiple active policy versions")
    for version in state["versions"].values():
        _require(version["policy"] == _policy(float(version["policy"]["minimum_score_distance"])), "policy contract drift")
        _require(sha256(version["policy"]) == version["payload_sha256"], "production policy hash mismatch")
        _require(version["owner_module"] == "TR-03" and version["materialization_route"] == PROMOTION_ROUTE, "foreign policy writer")
        parent = version.get("parent_version_id")
        if parent:
            _require(parent in state["versions"], "unknown parent version")
            _require(float(version["policy"]["minimum_score_distance"]) == float(state["versions"][parent]["policy"]["minimum_score_distance"]) + 1.0, "policy change outside bounded step")
            candidate = state["candidates"].get(version["candidate_id"])
            _require(candidate is not None and candidate.get("gate") is not None and candidate["gate"]["status"] == "PASS", "production version lacks passing candidate")
            _require(candidate["baseline_version"] == parent and candidate["challenger_version"] == version["payload_sha256"], "production materialization differs from evaluated candidate")
            _require(any(e.get("event") == "PROMOTION" and e.get("version_id") == version["version_id"] and e.get("policy_sha256") == version["payload_sha256"] and e.get("gate_id") == candidate["gate"]["gate_id"] for e in state["events"]), "version promotion authorization missing")
        else:
            _require(version["policy"] == _policy(MINIMUM_FLOOR) and version["candidate_id"] == "eurusd-policy-baseline" and not version.get("runtime_code_sha256"), "baseline production policy contract changed")
    active_cases = [case for case in state["cases"].values() if case["outcome"] is None and case["status"] != "CENSORED"]
    _require(len(active_cases) <= 1, "overlapping policy evaluation positions")
    _require(state.get("active_case_id") == (active_cases[0]["case_id"] if active_cases else None), "active case pointer mismatch")
    for cid, case in state["cases"].items():
        _require(cid == case["case_id"] and sha256(case["frozen"]) == case["frozen_sha256"], "frozen policy case mutated")
        _require(case.get("execution_authority") is False, "evaluation case cannot execute")
        _require(case["status"] in {"OPEN", "DATA_INCOMPLETE", "CLOSED", "CENSORED"}, "unknown policy case status")
        if case["status"] == "CENSORED":
            censoring = case.get("censoring") or {}
            _require(not case["frozen"]["production_admitted"] and case["outcome"] is None, "canonical actual cannot be counterfactually censored")
            _require(censoring.get("reason") == "RUNTIME_CODE_EPOCH_CHANGED" and censoring.get("excluded_from_evaluation") is True and censoring.get("execution_authority") is False, "invalid counterfactual censoring")
            _require(censoring.get("from_runtime_code_sha256") == case["frozen"]["manifest"]["runtime_code_sha256"] and censoring.get("to_runtime_code_sha256") != censoring.get("from_runtime_code_sha256"), "counterfactual censoring epoch mismatch")
            _require(_time(censoring["at"]) >= _time(case["frozen"]["frozen_at"]), "censoring predates frozen case")
        ref = _manifest_ref(case["frozen"]["manifest"])
        _require(ref["manifest_sha256"] == case["frozen"]["manifest_sha256"], "case manifest lineage mismatch")
        frozen = case["frozen"]
        score = _number(frozen["score"], "frozen score")
        _require(0 <= score <= 100, "invalid frozen directional score")
        _require(frozen["position"].get("decision_manifest") == frozen["manifest"], "position/case manifest mismatch")
        initial_risk = abs(_number(frozen["position"]["entry"], "frozen entry") - _number(frozen["position"]["stop"], "frozen stop"))
        _require(initial_risk > 0 and abs(initial_risk - float(frozen["risk_unit"])) < 1e-10, "frozen initial risk mismatch")
        version = state["versions"][frozen["production_version_id"]]
        _require(frozen["manifest"]["policy_version_id"] == version["version_id"] and frozen["manifest"]["policy_sha256"] == version["payload_sha256"], "frozen production policy mismatch")
        for vote_id, allowed in frozen["votes"].items():
            spec = state["versions"][vote_id]["policy"] if vote_id in state["versions"] else state["candidates"][vote_id]["proposed_change"]
            _require(isinstance(allowed, bool) and allowed == _allows(spec, score, frozen["position"]["direction"]), "frozen paired decision does not reconcile")
        _require(frozen["production_admitted"] == frozen["votes"][frozen["production_version_id"]], "canonical admission vote mismatch")
        if case["outcome"]:
            expected = "CANONICAL_ACTUAL" if case["frozen"]["production_admitted"] else "PROSPECTIVE_COUNTERFACTUAL"
            _require(case["outcome"]["source"] == expected, "actual/counterfactual boundary violation")
            _require(_time(case["outcome"]["closed_at"]) >= _time(case["frozen"]["frozen_at"]), "nonprospective case outcome")
            _number(case["outcome"]["net_r"], "net_r")
            frozen = case["frozen"]
            expected_r = (1 if frozen["position"]["direction"] == "LONG" else -1) * (float(case["outcome"]["exit_price"]) - float(frozen["position"]["entry"])) / float(frozen["risk_unit"])
            _require(abs(expected_r - float(case["outcome"]["net_r"])) < 1e-10, "outcome net R does not reconcile")
    for candidate in state["candidates"].values():
        _require(sha256(_candidate_fingerprint(candidate)) == candidate["frozen_sha256"], "frozen candidate mutated")
        _require(sha256(candidate["proposed_change"]) == candidate["challenger_version"], "candidate policy hash drift")
        _require(candidate["source_module"] == "TR-03" and candidate["target_module"] == "TR-03" and candidate["promotion_route"] == PROMOTION_ROUTE and candidate["trade_execution_authority"] is False, "candidate authority drift")
        parent = state["versions"][candidate["baseline_version"]]
        _require(float(candidate["proposed_change"]["minimum_score_distance"]) == float(parent["policy"]["minimum_score_distance"]) + 1, "candidate outside bounded step")
        selected = candidate["evaluation_case_ids"]
        _require(len(selected) == len(set(selected)) and len(selected) <= PRIMARY_N, "invalid fixed evaluation sample")
        previous = None
        for cid in selected:
            _require(cid in state["cases"], "evaluation evidence missing")
            case = state["cases"][cid]
            _require(case["frozen"]["production_admitted"], "counterfactual primary evidence")
            _require(candidate.get("runtime_binding") is not None and case["frozen"]["manifest"]["runtime_code_sha256"] == candidate["runtime_binding"]["runtime_code_sha256"], "candidate runtime code cohort mismatch")
            at = _time(case["frozen"]["decision_at"])
            _require(at > _time(candidate["activation_boundary"]), "preboundary sample")
            _require(previous is None or at >= previous + timedelta(hours=PURGE_HOURS), "overlapping outcome labels in primary sample")
            previous = at
        gate = candidate.get("gate")
        if gate and gate["status"] == "PASS":
            recomputed = _candidate_gate(state, candidate, gate["evaluated_at"], enforce_current_evaluator=False)
            _require(recomputed == gate, "promotion evidence no longer reconciles")
        if candidate["status"] == "PROMOTED":
            _require(gate is not None and gate["status"] == "PASS", "promotion without independent passing gate")
    _validate_governance_timeline(state)
    _require(_effective_version(state, state["updated_at"])["version_id"] == state["active_version_id"], "active policy differs from governed event timeline")
    for version in state["versions"].values():
        _validate_monitoring(state, version)


def validate_transition(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    validate_state(after)
    _require(_time(after["updated_at"]) >= _time(before["updated_at"]), "policy state time regression")
    for cid, old in before.get("cases", {}).items():
        _require(cid in after["cases"] and old["frozen"] == after["cases"][cid]["frozen"], "case history rewrite")
        if old["outcome"] is not None:
            _require(old["outcome"] == after["cases"][cid]["outcome"], "closed policy outcome rewrite")
        if old["status"] == "CENSORED":
            _require(after["cases"][cid]["status"] == "CENSORED" and after["cases"][cid]["outcome"] is None and old["censoring"] == after["cases"][cid].get("censoring"), "terminal counterfactual censoring rewrite")
    for cid, old in before.get("candidates", {}).items():
        _require(cid in after["candidates"] and old["frozen_sha256"] == after["candidates"][cid]["frozen_sha256"], "candidate history rewrite")
        _require(after["candidates"][cid]["evaluation_case_ids"][:len(old["evaluation_case_ids"])] == old["evaluation_case_ids"], "outcome-selected evaluation sample")
        if old.get("runtime_binding") is not None:
            _require(old["runtime_binding"] == after["candidates"][cid]["runtime_binding"], "runtime cohort binding rewrite")
        if old.get("gate") and old["gate"]["status"] in {"PASS", "FAIL"}:
            _require(old["gate"] == after["candidates"][cid]["gate"], "terminal gate rewrite")
    for vid, old in before.get("versions", {}).items():
        _require(vid in after["versions"], "production version removed")
        for key in ("policy", "payload_sha256", "activated_at", "parent_version_id", "candidate_id", "owner_module", "materialization_route"):
            _require(old[key] == after["versions"][vid][key], "production version rewrite: " + key)
        _require(old.get("runtime_code_sha256") == after["versions"][vid].get("runtime_code_sha256"), "version runtime code binding rewrite")
        _require(after["versions"][vid]["monitor_case_ids"][:len(old["monitor_case_ids"])] == old["monitor_case_ids"], "monitor sample history rewrite")
        _require(after["versions"][vid]["monitor_reviews"][:len(old["monitor_reviews"])] == old["monitor_reviews"], "monitor review history rewrite")
    _require(after["events"][:len(before.get("events", []))] == before.get("events", []), "policy event history rewrite")
    _require(after["rollback_events"][:len(before.get("rollback_events", []))] == before.get("rollback_events", []), "rollback history rewrite")
    if before.get("active_version_id") != after.get("active_version_id"):
        _require(len(after["events"]) > len(before["events"]), "policy changed without governed event")
        _require(after["events"][-1]["event"] in {"PROMOTION", "ROLLBACK"}, "unauthorized policy mutation")
