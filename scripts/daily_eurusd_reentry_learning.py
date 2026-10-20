"""TR-03 local, prospective learning of post-loss re-entry criteria.

Stored inside the existing production policy file and owned by its writer.
A verified reference entry is a PAPER counterfactual unless a canonical actual
trade exists. It never creates a trade or grants execution authority.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    import daily_eurusd_reentry_safety as safety
    from briefrooms_evolution_contracts import EvolutionCandidate, ProductionVersion, PromotionGate, RollbackEvent, sha256, stable_id
except ImportError:
    from scripts import daily_eurusd_reentry_safety as safety
    from scripts.briefrooms_evolution_contracts import EvolutionCandidate, ProductionVersion, PromotionGate, RollbackEvent, sha256, stable_id

SCHEMA = "eurusd-adaptive-reentry-state-v1"
COMPONENT = "TR-03:post_loss_reentry_criteria"
ROUTE = "daily_eurusd_canonical_writer"
MAX_PARALLEL, MAX_TESTS = 6, 18
PRIMARY_N, MIN_DAYS, PURGE_HOURS = 40, 15, 27
MONITOR_N, MONITOR_DAYS = 20, 10
MIN_DELTA_R, MAX_INCREMENTAL_DRAWDOWN_R = .05, 2.0
CONFIDENCE = 1 - .05 / MAX_TESTS
EVALUATOR_SHA256 = sha256({
    "learning": Path(__file__).read_bytes().hex(),
    "criteria": Path(safety.__file__).read_bytes().hex(),
})
AUTHORITY = {
    "owner_module": "TR-03", "writer": ROUTE, "criteria_mutation_allowed": True,
    "direction_mutation_allowed": False, "risk_mutation_allowed": False,
    "trade_execution_authority": False, "historical_backfill": False,
}


def _time(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("reentry learning requires timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: Any) -> str:
    return _time(value).isoformat().replace("+00:00", "Z")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError("adaptive reentry: " + message)


def _version(criteria: Mapping[str, Any], at: str, candidate: str, parent: str | None, code: str) -> dict[str, Any]:
    result = ProductionVersion(
        version_id=stable_id("eurusd-reentry-policy", {"criteria": criteria, "at": at, "candidate": candidate}),
        component_id=COMPONENT, candidate_id=candidate, activated_at=at, parent_version_id=parent,
        owner_module="TR-03", materialization_route=ROUTE, payload_sha256=sha256(criteria),
    ).to_dict()
    result.update(criteria=copy.deepcopy(dict(criteria)), runtime_code_sha256=code,
                  monitor_case_ids=[], monitor_reviews=[])
    return result


def initial(now: Any, code: str) -> dict[str, Any]:
    at = _iso(now)
    baseline = _version(safety.normalize_criteria(), at, "INITIAL_CRITERIA", None, code)
    return {
        "schema_version": SCHEMA, "authority": dict(AUTHORITY), "activated_at": at, "updated_at": at,
        "runtime_code_sha256": code, "active_version_id": baseline["version_id"],
        "versions": {baseline["version_id"]: baseline}, "candidates": {}, "cases": {},
        "active_case_id": None, "events": [], "rollback_events": [],
    }


def effective(state: Mapping[str, Any], at: Any) -> Mapping[str, Any]:
    baseline = next(row for row in state["versions"].values() if row["parent_version_id"] is None)
    selected = baseline
    for event in state["events"]:
        if _time(event["at"]) <= _time(at):
            selected = state["versions"][event["version_id"]]
    return selected


def context(state: Mapping[str, Any] | None, at: Any) -> dict[str, Any]:
    if state is None:
        return {"version_id": None, "policy_sha256": sha256(safety.normalize_criteria()),
                "criteria": safety.normalize_criteria(), "status": "INITIAL_CRITERIA"}
    version = effective(state, at)
    return {"version_id": version["version_id"], "policy_sha256": version["payload_sha256"],
            "criteria": copy.deepcopy(version["criteria"]), "status": "GOVERNED_ADAPTIVE_CRITERIA",
            "activation_boundary": version["activated_at"]}


def contenders(state: Mapping[str, Any] | None, at: Any) -> dict[str, dict[str, Any]]:
    if state is None:
        return {}
    version = effective(state, at)
    result = {version["version_id"]: copy.deepcopy(version["criteria"])}
    if version.get("parent_version_id"):
        parent = state["versions"][version["parent_version_id"]]
        result[parent["version_id"]] = copy.deepcopy(parent["criteria"])
    for row in state["candidates"].values():
        if row["status"] == "OOS_RUNNING" and row["baseline_version"] == version["version_id"]:
            result[row["candidate_id"]] = copy.deepcopy(row["proposed_change"])
    return result


def _proposals(criteria: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    changes: list[tuple[str, dict[str, Any]]] = []
    def add(name: str, **update: Any) -> None:
        value = {**copy.deepcopy(dict(criteria)), **update}
        try:
            value = safety.normalize_criteria(value)
        except ValueError:
            return
        if value != criteria and all(prior != value for _, prior in changes):
            changes.append((name, value))
    for sign, label in ((-1, "LOWER"), (1, "HIGHER")):
        add("RETRACEMENT_" + label, minimum_retracement=round(criteria["minimum_retracement"] + sign * .05, 4))
        add("SIGNAL_" + label,
            minimum_score_distance=criteria["minimum_score_distance"] + sign * 2,
            minimum_confidence=round(criteria["minimum_confidence"] + sign * .15, 4))
        add("SCORE_MARGIN_" + label, minimum_score_distance=criteria["minimum_score_distance"] + sign * 2)
        add("CONFIDENCE_" + label, minimum_confidence=round(criteria["minimum_confidence"] + sign * .10, 4))
        add("THESIS_CHANGE_" + label,
            material_score_improvement=criteria["material_score_improvement"] + sign,
            material_confidence_improvement=round(criteria["material_confidence_improvement"] + sign * .02, 4))
        add("WAIT_" + label, minimum_wait_minutes=criteria["minimum_wait_minutes"] + sign * 15)
        add("BELIEF_COUNT_" + label, minimum_aligned_beliefs=criteria["minimum_aligned_beliefs"] + sign)
        add("SUPPORT_ZONE_" + label, zone_atr=round(criteria["zone_atr"] + sign * .025, 4),
            maximum_entry_distance_atr=round(criteria["maximum_entry_distance_atr"] + sign * .05, 4))
    add("SUPPORT_DEFINITION", support_mode="PRICE_REJECTION" if criteria["support_mode"] == "D1_LEVEL" else "D1_LEVEL")
    add("MA_PERIODS", ma_periods=[20, 50, 100, 200] if criteria["ma_periods"] == [60, 100, 200] else [60, 100, 200])
    add("POST_LOSS_SOURCE_REQUIREMENT", require_post_loss_trend=not criteria["require_post_loss_trend"])
    add("MATERIAL_CHANGE_REQUIREMENT", require_material_change=not criteria["require_material_change"])
    for minutes in (1, 5, 15):
        add("CONFIRMATION_" + str(minutes), confirmation_minutes=minutes)
    return changes


def _ensure(state: dict[str, Any], at: str, memory: Mapping[str, Any]) -> None:
    active = state["versions"][state["active_version_id"]]
    cohort = [row for row in state["candidates"].values()
              if row["metadata"]["runtime_code_sha256"] == state["runtime_code_sha256"]
              and row["metadata"]["evaluator_sha256"] == EVALUATOR_SHA256]
    running = [row for row in cohort if row["status"] == "OOS_RUNNING"]
    slots = min(MAX_PARALLEL - len(running), MAX_TESTS - len(cohort))
    if slots <= 0:
        return
    seen = {sha256(row["proposed_change"]) for row in cohort}
    # Discovery may prioritize research, but contributes zero OOS observations.
    weak_signal_losses = any(row["finding"] == "LOW_BELIEF_CONFIDENCE" and row["losses"]
                             for row in memory.get("lessons") or [])
    priorities = {"RETRACEMENT_LOWER": 1, "RETRACEMENT_HIGHER": 2,
                  "SUPPORT_DEFINITION": 3, "SIGNAL_LOWER": 4, "MA_PERIODS": 5,
                  "SIGNAL_HIGHER": 0 if weak_signal_losses else 6}
    proposals = sorted(_proposals(active["criteria"]), key=lambda item: (priorities.get(item[0], 10), item[0]))
    for axis, proposed in proposals:
        if slots <= 0:
            break
        if sha256(proposed) in seen:
            continue
        specification = {
            "primary_n": PRIMARY_N, "minimum_days": MIN_DAYS, "purge_hours": PURGE_HOURS,
            "confidence": CONFIDENCE, "maximum_tests_per_cohort": MAX_TESTS,
            "minimum_delta_r": MIN_DELTA_R, "maximum_incremental_drawdown_r": MAX_INCREMENTAL_DRAWDOWN_R,
            "runtime_code_sha256": state["runtime_code_sha256"], "evaluator_sha256": EVALUATOR_SHA256,
            "selection_rule": "MAX_PASS_LOWER_CI_THEN_MEAN_THEN_CANDIDATE_ID",
            "evidence": "PROSPECTIVE_PAIRED_ADMISSION_ACTUAL_OR_LABELLED_COUNTERFACTUAL",
            "discovery_is_oos": False, "axis": axis,
            "trial_number": len(cohort) + 1,
            "reflection_sha256": (memory.get("iterations") or [{}])[-1].get("iteration_sha256"),
        }
        identity = {"parent": active["version_id"], "criteria": proposed, "at": at, "spec": specification}
        cid = stable_id("eurusd-reentry-candidate", identity)
        row = EvolutionCandidate(
            candidate_id=cid, candidate_type="adaptive_post_loss_reentry_criteria",
            source_module="TR-03", target_module="TR-03", component_id=COMPONENT,
            created_at=at, activation_boundary=at, status="OOS_RUNNING",
            evaluator_profile="eurusd_reentry_prospective_paired_v1", promotion_route=ROUTE,
            baseline_version=active["version_id"], challenger_version=sha256(proposed),
            proposed_change=proposed, source_ref="tr03://prospective_post_loss_opportunities",
            source_sha256=sha256(identity), automatic_promotion_allowed=True, metadata=specification,
        ).to_dict()
        row["frozen_sha256"] = sha256(row)
        row.update(evaluation_case_ids=[], gate=None, terminal_at=None)
        state["candidates"][cid] = row
        cohort.append(row)
        seen.add(sha256(proposed))
        slots -= 1


def _selected(state: Mapping[str, Any], ids: list[str], case: Mapping[str, Any], maximum: int) -> bool:
    return len(ids) < maximum and (not ids or _time(case["frozen"]["decision_at"]) >=
            _time(state["cases"][ids[-1]]["frozen"]["decision_at"]) + timedelta(hours=PURGE_HOURS))


def freeze(
    state: Mapping[str, Any], *, manifest: Mapping[str, Any], position: Mapping[str, Any],
    evaluations: Mapping[str, Any], canonical_admitted: bool, now: Any,
) -> tuple[dict[str, Any], str | None]:
    result, at = copy.deepcopy(state), _iso(now)
    if result["active_case_id"]:
        return result, None
    at_decision = manifest["decision_at"]
    active = effective(result, at_decision)
    baseline = evaluations.get(active["version_id"]) or {}
    proof = baseline.get("technical") or {}
    if not proof.get("required") or not proof.get("prior_trade_id"):
        return result, None
    if not any(row.get("allowed") is True for row in evaluations.values()):
        return result, None
    loss_id = str(proof["prior_trade_id"])
    if any(row["frozen"]["loss_id"] == loss_id for row in result["cases"].values()):
        return result, None
    latest = max(result["cases"].values(), key=lambda row: _time(row["frozen"]["decision_at"]), default=None)
    if latest and _time(at_decision) < _time(latest["frozen"]["decision_at"]) + timedelta(hours=PURGE_HOURS):
        return result, None
    _require(_time(at_decision) <= _time(at), "future research decision")
    execution = manifest.get("execution_price_engine") or {}
    _require(execution.get("verified") is True and execution.get("status") == "VERIFIED_FILL"
             and execution.get("execution_cost_contract") == "DAILY_EURUSD_BID_ASK_V1", "unverified reference execution")
    _require(manifest.get("runtime_code_sha256") == result["runtime_code_sha256"], "mixed runtime evidence")
    eligible = {key: value for key, value in evaluations.items() if key in contenders(result, at_decision)}
    votes = {key: bool(value["allowed"]) for key, value in eligible.items()}
    _require(active["version_id"] in votes and votes[active["version_id"]] == canonical_admitted,
             "canonical admission differs from active criteria")
    entry, stop = safety._number(position["entry"]), safety._number(position["stop"])
    _require(abs(entry - stop) > 0 and position.get("decision_manifest") == manifest, "frozen reference geometry missing")
    frozen = {
        "decision_at": at_decision, "frozen_at": at, "loss_id": loss_id,
        "production_version_id": active["version_id"], "canonical_admitted": canonical_admitted,
        "manifest": copy.deepcopy(dict(manifest)), "position": copy.deepcopy(dict(position)),
        "risk_unit": abs(entry - stop), "votes": votes, "evaluations": copy.deepcopy(eligible),
        "slices": {"direction": position["direction"], "session": _time(at_decision).hour // 8},
        "execution_authority": False,
    }
    cid = stable_id("eurusd-reentry-case", {"manifest": manifest["manifest_sha256"], "loss": loss_id})
    case = {"case_id": cid, "frozen": frozen, "frozen_sha256": sha256(frozen),
            "status": "OPEN", "outcome": None, "runtime_position": copy.deepcopy(dict(position)),
            "coverage_failures": [], "last_coverage_at": at}
    result["cases"][cid], result["active_case_id"] = case, cid
    for candidate in result["candidates"].values():
        if (candidate["status"] == "OOS_RUNNING" and candidate["baseline_version"] == active["version_id"]
                and candidate["candidate_id"] in votes and _time(at_decision) > _time(candidate["activation_boundary"])
                and _selected(result, candidate["evaluation_case_ids"], case, PRIMARY_N)):
            candidate["evaluation_case_ids"].append(cid)
    parent = active.get("parent_version_id")
    if parent and _time(at_decision) > _time(active["activated_at"]) and _selected(
            result, active["monitor_case_ids"], case, MONITOR_N * (len(active["monitor_reviews"]) + 1)):
        active["monitor_case_ids"].append(cid)
    result["updated_at"] = at
    return result, cid


def _outcome(case: Mapping[str, Any], trade: Mapping[str, Any], at: str, source: str) -> dict[str, Any]:
    frozen, position = case["frozen"], case["frozen"]["position"]
    _require(trade.get("trade_id") == position["trade_id"] and trade.get("direction") == position["direction"], "outcome identity mismatch")
    _require(trade.get("decision_manifest") == frozen["manifest"], "outcome manifest mismatch")
    _require(_time(frozen["frozen_at"]) <= _time(trade["closed_at"]) <= _time(at), "outcome outside prospective time")
    exit_price = safety._number(trade["exit_price"])
    net_r = (1 if position["direction"] == "LONG" else -1) * (exit_price - position["entry"]) / frozen["risk_unit"]
    return {"source": source, "net_r": net_r, "exit_price": exit_price,
            "closed_at": trade["closed_at"], "bound_at": at, "trade_sha256": sha256(trade),
            "execution_authority": False, "broker_fill_claim": False}


def settle(state: Mapping[str, Any], history: Mapping[str, Any], *, now: Any,
           evaluator: Callable | None = None) -> dict[str, Any]:
    result, at = copy.deepcopy(state), _iso(now)
    case = result["cases"].get(result["active_case_id"])
    if not case:
        result["updated_at"] = at
        return result
    if case["frozen"]["canonical_admitted"]:
        trade = next((row for row in history.get("trades") or []
                      if row.get("trade_id") == case["frozen"]["position"]["trade_id"]), None)
        if trade is not None:
            case["outcome"] = _outcome(case, trade, at, "CANONICAL_ACTUAL")
    elif evaluator is not None:
        response = evaluator(copy.deepcopy(case["runtime_position"]), copy.deepcopy(case))
        if response.get("coverage_complete") is not True:
            case["status"] = "DATA_INCOMPLETE"
            if not case["coverage_failures"] or case["coverage_failures"][-1] != at:
                case["coverage_failures"].append(at)
        else:
            case["status"], case["last_coverage_at"] = "OPEN", at
            if response.get("position") is not None:
                updated = response["position"]
                for key in ("trade_id", "opened_at", "direction", "entry", "stop", "target", "decision_manifest"):
                    _require(updated.get(key) == case["runtime_position"].get(key), "reference lifecycle changed frozen fact")
                case["runtime_position"] = copy.deepcopy(dict(updated))
            if response.get("closed_trade") is not None:
                case["outcome"] = _outcome(case, response["closed_trade"], at, "PROSPECTIVE_COUNTERFACTUAL")
    if case["outcome"] is not None:
        case["status"], result["active_case_id"] = "CLOSED", None
    result["updated_at"] = at
    return result


def _metrics(state: Mapping[str, Any], ids: list[str], baseline: str, candidate: str, stats: Callable) -> tuple:
    return stats([state["cases"][cid] for cid in ids], baseline, candidate, sha256(ids),
                 confidence=CONFIDENCE)


def _gate(state: Mapping[str, Any], candidate: Mapping[str, Any], at: str, stats: Callable) -> dict[str, Any]:
    ids = candidate["evaluation_case_ids"]
    resolved = sum(state["cases"][cid]["outcome"] is not None for cid in ids)
    metrics, checks, blockers = {"selected_n": len(ids), "resolved_n": resolved}, [], []
    status = "COLLECTING"
    if len(ids) < PRIMARY_N or resolved < PRIMARY_N:
        blockers.append("fixed_prospective_reentry_sample_incomplete")
    else:
        metrics, checks, blockers = _metrics(state, ids, candidate["baseline_version"], candidate["candidate_id"], stats)
        if metrics["independent_days"] < MIN_DAYS:
            blockers.append("minimum_independent_days")
        if metrics["stressed_mean_delta_r"] < MIN_DELTA_R or metrics["block_ci_low_r"] <= 0:
            blockers.append("net_economic_and_confidence_gate")
        if metrics["challenger_max_drawdown_r"] > metrics["champion_max_drawdown_r"] + MAX_INCREMENTAL_DRAWDOWN_R:
            blockers.append("incremental_drawdown_budget")
        if metrics["first_half_block_mean_r"] <= 0 or metrics["second_half_block_mean_r"] <= 0:
            blockers.append("temporal_stability")
        status = "FAIL" if blockers else "PASS"
    return PromotionGate(
        gate_id=stable_id("eurusd-reentry-gate", {"candidate": candidate["frozen_sha256"], "metrics": metrics, "blockers": blockers}),
        candidate_id=candidate["candidate_id"], evaluated_at=at, status=status,
        evaluator_profile=candidate["evaluator_profile"], prospective_only=True,
        minimum_sample=PRIMARY_N, observed_sample=resolved, criteria=candidate["metadata"],
        metrics=metrics, segment_checks=tuple(checks), blockers=tuple(blockers),
        source_sha256=sha256([state["cases"][cid] for cid in ids]),
    ).to_dict()


def _rank(row: Mapping[str, Any]) -> tuple:
    return (row["gate"]["metrics"]["block_ci_low_r"], row["gate"]["metrics"]["stressed_mean_delta_r"],
            row["candidate_id"])


def _rollback(state: dict[str, Any], at: str, reason: str, metrics: Mapping[str, Any]) -> None:
    active = state["versions"][state["active_version_id"]]
    parent = active["parent_version_id"]
    if parent is None:
        return
    event = RollbackEvent(
        rollback_id=stable_id("eurusd-reentry-rollback", {"from": active["version_id"], "at": at, "reason": reason}),
        component_id=COMPONENT, from_version_id=active["version_id"], to_version_id=parent,
        candidate_id=active["candidate_id"], detected_at=at, effective_at=at, reason=reason,
        metrics=dict(metrics), owner_module="TR-03", materialization_route=ROUTE, automatic=True,
    ).to_dict()
    state["rollback_events"].append(event)
    active["status"] = "ROLLED_BACK"
    state["versions"][parent]["status"], state["active_version_id"] = "ACTIVE", parent
    state["candidates"][active["candidate_id"]]["status"] = "ROLLED_BACK"
    state["events"].append({"event": "ROLLBACK", "at": at, "version_id": parent, "rollback_id": event["rollback_id"]})


def advance(state: Mapping[str, Any] | None, *, now: Any, code: str,
            memory: Mapping[str, Any], stats: Callable) -> dict[str, Any]:
    at = _iso(now)
    result = copy.deepcopy(state) if state is not None else initial(at, code)
    result["runtime_code_sha256"] = code
    active = result["versions"][result["active_version_id"]]
    while active["parent_version_id"] and active["runtime_code_sha256"] != code:
        _rollback(result, at, "INVARIANT_FAILURE", {"reason": "runtime_code_semantics_changed"})
        active = result["versions"][result["active_version_id"]]
    for candidate in result["candidates"].values():
        if candidate["status"] == "OOS_RUNNING" and (
                candidate["metadata"]["runtime_code_sha256"] != code
                or candidate["metadata"]["evaluator_sha256"] != EVALUATOR_SHA256
                or candidate["baseline_version"] != result["active_version_id"]):
            candidate.update(status="PARKED", terminal_at=at)
    case = result["cases"].get(result["active_case_id"])
    if case and case["frozen"]["manifest"]["runtime_code_sha256"] != code and not case["frozen"]["canonical_admitted"]:
        case.update(status="CENSORED", censoring={"at": at, "reason": "RUNTIME_CODE_EPOCH_CHANGED"})
        result["active_case_id"], case = None, None
    gap = bool(case and len(case["coverage_failures"]) >= 3
               and _time(at) - _time(case["last_coverage_at"]) >= timedelta(hours=2))
    if gap:
        _rollback(result, at, "DATA_QUALITY_FAILURE", {"case_id": case["case_id"], "missing_is_not_zero": True})
    active = result["versions"][result["active_version_id"]]
    parent = active["parent_version_id"]
    if parent:
        reviewed = len(active["monitor_reviews"])
        ids = active["monitor_case_ids"][reviewed * MONITOR_N:(reviewed + 1) * MONITOR_N]
        if len(ids) == MONITOR_N and all(result["cases"][cid]["outcome"] is not None for cid in ids):
            metrics, checks, _ = _metrics(result, ids, parent, active["version_id"], stats)
            active["monitor_reviews"].append({"at": at, "case_ids": ids, "metrics": metrics, "segment_checks": checks})
            reason, details = None, metrics
            if any(row["status"] == "FAIL" for row in checks):
                reason, details = "SEGMENT_SAFETY_FAILURE", {"checks": checks}
            elif metrics["independent_days"] >= MONITOR_DAYS:
                if metrics["stressed_mean_delta_r"] <= -MIN_DELTA_R and metrics["block_ci_high_r"] < 0:
                    reason = "POST_PROMOTION_DEGRADATION"
                elif metrics["challenger_max_drawdown_r"] > metrics["champion_max_drawdown_r"] + MAX_INCREMENTAL_DRAWDOWN_R:
                    reason, details = "SEGMENT_SAFETY_FAILURE", {"reason": "incremental_drawdown_budget", **metrics}
            if reason:
                _rollback(result, at, reason, details)
    passing = []
    for candidate in result["candidates"].values():
        if candidate["status"] != "OOS_RUNNING" or candidate["baseline_version"] != result["active_version_id"]:
            continue
        gate = _gate(result, candidate, at, stats)
        if gate["status"] == "PASS" and case and case["status"] == "DATA_INCOMPLETE":
            # The frozen passing test remains unchanged while activation waits.
            continue
        candidate["gate"] = gate
        if gate["status"] == "PASS" and not gap:
            passing.append(candidate)
        elif gate["status"] == "FAIL":
            candidate.update(status="REJECTED", terminal_at=at)
    if passing:
        winner = max(passing, key=_rank)
        parent = result["active_version_id"]
        version = _version(winner["proposed_change"], at, winner["candidate_id"], parent, code)
        result["versions"][parent]["status"] = "SUPERSEDED"
        result["versions"][version["version_id"]], result["active_version_id"] = version, version["version_id"]
        winner.update(status="PROMOTED", terminal_at=at)
        result["events"].append({"event": "PROMOTION", "at": at, "version_id": version["version_id"],
                                 "candidate_id": winner["candidate_id"], "gate_id": winner["gate"]["gate_id"]})
        for row in result["candidates"].values():
            if row["status"] == "OOS_RUNNING" and row["baseline_version"] == parent:
                row.update(status="PARKED", terminal_at=at)
    _ensure(result, at, memory)
    result["updated_at"] = at
    validate(result, stats)
    if state is not None:
        validate_transition(state, result)
    return result


def validate(state: Mapping[str, Any], stats: Callable) -> None:
    _require(state.get("schema_version") == SCHEMA and state.get("authority") == AUTHORITY, "state authority drift")
    _time(state["activated_at"])
    _require(sum(row["status"] == "OOS_RUNNING" for row in state["candidates"].values()) <= MAX_PARALLEL,
             "parallel candidate budget exceeded")
    active = [row["version_id"] for row in state["versions"].values() if row["status"] == "ACTIVE"]
    _require(active == [state["active_version_id"]], "active version mismatch")
    opened = [row["case_id"] for row in state["cases"].values() if row["outcome"] is None and row["status"] != "CENSORED"]
    _require(len(opened) <= 1 and state["active_case_id"] == (opened[0] if opened else None), "reference position overlap")
    losses, previous = set(), None
    for cid, case in sorted(state["cases"].items(), key=lambda pair: _time(pair[1]["frozen"]["decision_at"])):
        frozen = case["frozen"]
        _require(cid == case["case_id"] and sha256(frozen) == case["frozen_sha256"], "frozen case mutated")
        _require(frozen["loss_id"] not in losses, "same loss counted twice")
        losses.add(frozen["loss_id"])
        at = _time(frozen["decision_at"])
        _require(previous is None or at >= previous + timedelta(hours=PURGE_HOURS), "overlapping reference samples")
        previous = at
        _require(frozen["execution_authority"] is False, "reference cannot execute")
        manifest = frozen["manifest"]
        body = dict(manifest)
        recorded = body.pop("manifest_sha256", None)
        _require(sha256(body) == recorded and manifest["decision_at"] == frozen["decision_at"]
                 and frozen["position"]["decision_manifest"] == manifest, "reference manifest differs from frozen facts")
        _require(_time(frozen["decision_at"]) <= _time(frozen["frozen_at"]), "reference frozen before decision")
        entry, stop = frozen["position"]["entry"], frozen["position"]["stop"]
        _require(abs(entry - stop) > 0 and abs(abs(entry - stop) - frozen["risk_unit"]) < 1e-10,
                 "reference initial risk changed")
        _require(frozen["production_version_id"] == effective(state, at)["version_id"], "reference baseline differs from governed policy")
        _require(case["status"] in {"OPEN", "CLOSED", "DATA_INCOMPLETE", "CENSORED"}, "unknown case status")
        if case["status"] == "CENSORED":
            _require(not frozen["canonical_admitted"] and case["outcome"] is None
                     and case["censoring"]["reason"] == "RUNTIME_CODE_EPOCH_CHANGED", "invalid counterfactual censoring")
        expected = contenders_for_case(state, frozen)
        for vote, allowed in frozen["votes"].items():
            _require(vote in expected and isinstance(allowed, bool), "unknown policy vote")
            evaluation = frozen["evaluations"][vote]
            proof = evaluation["technical"]
            _require(safety.normalize_criteria(proof["criteria"]) == expected[vote], "case criteria differ from version")
            _require(proof["criteria_sha256"] == safety._digest(expected[vote]), "criteria proof hash mismatch")
            _require(allowed == bool(evaluation["allowed"]), "case vote mismatch")
            _require(not allowed or (proof["allowed"] is True and evaluation["thesis_allowed"] is True), "vetoed policy voted admission")
        _require(frozen["canonical_admitted"] == frozen["votes"][frozen["production_version_id"]], "actual/reference boundary drift")
        if case["outcome"] is not None:
            outcome = case["outcome"]
            source = "CANONICAL_ACTUAL" if frozen["canonical_admitted"] else "PROSPECTIVE_COUNTERFACTUAL"
            _require(outcome["source"] == source and outcome["execution_authority"] is False, "actual/counterfactual source mismatch")
            value = (1 if frozen["position"]["direction"] == "LONG" else -1) * (outcome["exit_price"] - frozen["position"]["entry"]) / frozen["risk_unit"]
            _require(abs(value - outcome["net_r"]) < 1e-10, "net outcome does not reconcile")
            _require(_time(frozen["frozen_at"]) <= _time(outcome["closed_at"]) <= _time(outcome["bound_at"]), "nonprospective outcome")
    budgets: dict[tuple[str, str], list[int]] = {}
    for row in state["candidates"].values():
        core = {key: value for key, value in row.items() if key not in
                {"frozen_sha256", "evaluation_case_ids", "gate", "terminal_at", "status"}}
        core["status"] = "OOS_RUNNING"
        _require(sha256(core) == row["frozen_sha256"], "frozen candidate changed")
        _require(row["component_id"] == COMPONENT and row["trade_execution_authority"] is False, "candidate authority drift")
        _require(sha256(safety.normalize_criteria(row["proposed_change"])) == row["challenger_version"], "candidate policy mismatch")
        metadata = row["metadata"]
        _require(metadata["maximum_tests_per_cohort"] == MAX_TESTS and metadata["confidence"] == CONFIDENCE, "test-family drift")
        key = (metadata["runtime_code_sha256"], metadata["evaluator_sha256"])
        budgets.setdefault(key, []).append(metadata["trial_number"])
        ids = row["evaluation_case_ids"]
        _require(len(ids) <= PRIMARY_N and len(ids) == len(set(ids)), "fixed primary sample changed")
        expected_ids = [cid for cid, case in sorted(state["cases"].items(),
                        key=lambda pair: _time(pair[1]["frozen"]["decision_at"]))
                        if row["candidate_id"] in case["frozen"]["votes"]
                        and case["frozen"]["production_version_id"] == row["baseline_version"]
                        and case["frozen"]["manifest"]["runtime_code_sha256"] == metadata["runtime_code_sha256"]
                        and _time(case["frozen"]["decision_at"]) > _time(row["activation_boundary"])]
        _require(ids == expected_ids[:PRIMARY_N], "primary sample is not the first frozen eligible cases")
        for cid in ids:
            case = state["cases"][cid]
            _require(_time(case["frozen"]["decision_at"]) > _time(row["activation_boundary"]), "preboundary learning evidence")
            _require(case["frozen"]["manifest"]["runtime_code_sha256"] == metadata["runtime_code_sha256"], "mixed code cohort")
            _require(case["frozen"]["production_version_id"] == row["baseline_version"]
                     and row["candidate_id"] in case["frozen"]["votes"], "candidate sample baseline mismatch")
        if row.get("gate") and row["gate"]["status"] in {"PASS", "FAIL"}:
            _require(_gate(state, row, row["gate"]["evaluated_at"], stats) == row["gate"], "terminal gate cannot be reproduced")
    for trials in budgets.values():
        _require(len(trials) <= MAX_TESTS and len(trials) == len(set(trials))
                 and sorted(trials) == list(range(1, len(trials) + 1)), "research budget reset or exceeded")
    baseline = [row for row in state["versions"].values() if row["parent_version_id"] is None]
    _require(len(baseline) == 1 and baseline[0]["criteria"] == safety.normalize_criteria(), "initial policy rewritten")
    head, seen, rollback_ids = baseline[0]["version_id"], {baseline[0]["version_id"]}, set()
    statuses = {head: "ACTIVE"}
    prior_at = _time(state["activated_at"])
    for event in state["events"]:
        at = _time(event["at"])
        _require(prior_at <= at <= _time(state["updated_at"]), "governance time regression")
        prior_at = at
        if event["event"] == "PROMOTION":
            version = state["versions"][event["version_id"]]
            row = state["candidates"][event["candidate_id"]]
            _require(version["version_id"] not in seen and version["parent_version_id"] == head
                     and row["baseline_version"] == head, "invalid promotion ancestry")
            gate = row.get("gate") or {}
            _require(gate.get("status") == "PASS" and gate["gate_id"] == event["gate_id"]
                     and gate["evaluated_at"] == event["at"] == version["activated_at"], "promotion lacks exact passing gate")
            _require(version["payload_sha256"] == row["challenger_version"], "unevaluated production policy")
            _require(all(_time(state["cases"][cid]["outcome"]["bound_at"]) <= at for cid in row["evaluation_case_ids"]),
                     "promotion predates known outcomes")
            candidates = [item for item in state["candidates"].values() if item["baseline_version"] == head
                          and (item.get("gate") or {}).get("status") == "PASS"
                          and item["gate"]["evaluated_at"] == event["at"]]
            _require(max(candidates, key=_rank)["candidate_id"] == row["candidate_id"], "parallel selection rule violated")
            statuses[head], statuses[version["version_id"]] = "SUPERSEDED", "ACTIVE"
            head = version["version_id"]
            seen.add(head)
        elif event["event"] == "ROLLBACK":
            rollback = next((row for row in state["rollback_events"] if row["rollback_id"] == event["rollback_id"]), None)
            _require(rollback is not None and rollback["rollback_id"] not in rollback_ids, "missing/replayed rollback")
            _require(rollback["owner_module"] == "TR-03" and rollback["materialization_route"] == ROUTE
                     and rollback["trade_execution_authority"] is False
                     and rollback["effective_at"] == event["at"] and _time(rollback["detected_at"]) <= at
                     and rollback["candidate_id"] == state["versions"][head]["candidate_id"], "rollback authority/time drift")
            _require(rollback["from_version_id"] == head and rollback["to_version_id"] == state["versions"][head]["parent_version_id"]
                     and rollback["to_version_id"] == event["version_id"], "rollback ancestry mismatch")
            statuses[head], statuses[event["version_id"]] = "ROLLED_BACK", "ACTIVE"
            head = event["version_id"]
            rollback_ids.add(rollback["rollback_id"])
        else:
            raise ValueError("adaptive reentry: unknown governance event")
    _require(head == state["active_version_id"] and seen == set(state["versions"])
             and len(rollback_ids) == len(state["rollback_events"]), "orphan production version/rollback")
    for vid, version in state["versions"].items():
        _require(version["status"] == statuses[vid] and version["component_id"] == COMPONENT
                 and version["owner_module"] == "TR-03" and version["materialization_route"] == ROUTE,
                 "production authority/status mismatch")
        _require(sha256(safety.normalize_criteria(version["criteria"])) == version["payload_sha256"], "production criteria hash mismatch")
        parent = version["parent_version_id"]
        expected_monitor = [cid for cid, case in sorted(state["cases"].items(),
                            key=lambda pair: _time(pair[1]["frozen"]["decision_at"]))
                            if case["frozen"]["production_version_id"] == vid
                            and _time(case["frozen"]["decision_at"]) > _time(version["activated_at"])]
        if parent is None:
            _require(not version["monitor_case_ids"] and not version["monitor_reviews"], "baseline cannot monitor a parent")
        else:
            _require(version["monitor_case_ids"] == expected_monitor[:MONITOR_N * (len(version["monitor_reviews"]) + 1)],
                     "monitor sample is not the first frozen eligible cases")
        for review_index, review in enumerate(version["monitor_reviews"]):
            ids = version["monitor_case_ids"][review_index * MONITOR_N:(review_index + 1) * MONITOR_N]
            _require(len(ids) == MONITOR_N and review["case_ids"] == ids, "monitor sample changed")
            metrics, checks, _ = _metrics(state, ids, parent, vid, stats)
            _require(metrics == review["metrics"] and checks == review["segment_checks"], "monitor review mismatch")


def contenders_for_case(state: Mapping[str, Any], frozen: Mapping[str, Any]) -> dict[str, Any]:
    return {**{key: row["criteria"] for key, row in state["versions"].items()},
            **{key: row["proposed_change"] for key, row in state["candidates"].items()}}


def validate_history(state: Mapping[str, Any], history: Mapping[str, Any]) -> None:
    trades = {row["trade_id"]: row for row in history.get("trades") or []}
    for case in state["cases"].values():
        if case["outcome"] is not None and case["frozen"]["canonical_admitted"]:
            tid = case["frozen"]["position"]["trade_id"]
            _require(tid in trades, "canonical actual absent from history")
            _require(_outcome(case, trades[tid], case["outcome"]["bound_at"], "CANONICAL_ACTUAL") == case["outcome"],
                     "actual outcome differs from history")


def validate_transition(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> None:
    if before is None:
        return
    _require(after is not None and before["activated_at"] == after["activated_at"], "learning epoch removed")
    for cid, old in before["cases"].items():
        new = after["cases"].get(cid) or {}
        _require(old["frozen"] == new.get("frozen"), "frozen opportunity rewritten")
        if old["outcome"] is not None:
            _require(old["outcome"] == new.get("outcome"), "closed evidence rewritten")
        if old["status"] == "CENSORED":
            _require(new.get("status") == "CENSORED" and new.get("censoring") == old["censoring"], "censoring rewritten")
    for cid, old in before["candidates"].items():
        new = after["candidates"].get(cid) or {}
        _require(old["frozen_sha256"] == new.get("frozen_sha256"), "candidate removed or changed")
        _require(new["evaluation_case_ids"][:len(old["evaluation_case_ids"])] == old["evaluation_case_ids"], "selected sample rewritten")
        if old.get("gate") and old["gate"]["status"] in {"PASS", "FAIL"}:
            _require(old["gate"] == new.get("gate"), "terminal gate rewritten")
    for vid, old in before["versions"].items():
        new = after["versions"].get(vid) or {}
        for key in ("criteria", "payload_sha256", "activated_at", "parent_version_id", "candidate_id", "runtime_code_sha256"):
            _require(old[key] == new.get(key), "production version rewritten")
        for key in ("monitor_case_ids", "monitor_reviews"):
            _require(new[key][:len(old[key])] == old[key], "monitor history rewritten")
    _require(after["events"][:len(before["events"])] == before["events"]
             and after["rollback_events"][:len(before["rollback_events"])] == before["rollback_events"], "governance history rewritten")
