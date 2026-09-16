#!/usr/bin/env python3
"""Autonomous production close-out for the PR35/PR36 learning loop.

PR35/PR36 methodology v2 deliberately ends at a research-only
``PROMOTION_ELIGIBLE_BUT_FROZEN`` state.  This module is the separately governed
production actuator.  It never weakens the research gates.  Instead it consumes
only candidates that have already passed both prospective fixed-N stages,
re-verifies their immutable ValidationEpoch lineage, then materializes exactly
one allowlisted scalar policy change into the checked-in production config.

The production state is committed beside the configs, so a policy change and
its durable lineage are one Git transaction.  Subsequent evidence is monitored
with the existing rollback trigger; a deterioration restores the immutable
parent policy and blocks the failed transition before research can propose it
again.

Scope is intentionally narrow: GPW/US Daily score thresholds only.  No Python
code mutation, arbitrary parameter mutation, hard-safety-gate weakening, sizing
writeback or trade execution is permitted here.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    import autonomous_policy_promotion as ap
    import autonomous_policy_promotion_v2 as pr35v2
    import promotion_learning_integrity as pli
    import statistical_promotion_gate_v2 as pr36v2
    import validation_epoch as ve
    from policy_runtime_overlay import POLICY_ALLOWLIST
except ModuleNotFoundError:  # pragma: no cover
    from scripts import autonomous_policy_promotion as ap
    from scripts import autonomous_policy_promotion_v2 as pr35v2
    from scripts import promotion_learning_integrity as pli
    from scripts import statistical_promotion_gate_v2 as pr36v2
    from scripts import validation_epoch as ve
    from scripts.policy_runtime_overlay import POLICY_ALLOWLIST

CONFIG_PATH = "data/investments/autonomous_policy_closed_loop_config.json"
STATE_PATH = "data/investments/autonomous_policy_production_state.json"
REPORT_FILENAME = "autonomous_policy_closed_loop_report.json"
CONFIG_SCHEMA = "briefrooms-autonomous-policy-closed-loop-config-v1"
STATE_SCHEMA = "briefrooms-autonomous-policy-production-state-v1"
REPORT_SCHEMA = "briefrooms-autonomous-policy-closed-loop-report-v1"
TERMINAL_PROMOTED = "PRODUCTION_PROMOTED"
TERMINAL_ROLLED_BACK = "PRODUCTION_ROLLED_BACK"
TERMINAL_STALE = "PRODUCTION_STALE_SUPERSEDED"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime:
    return ap._parse_time(str(value or ""))


def _finite(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric policy value")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite policy value")
    return number


def _read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    os.replace(tmp, path)


def _state_hash(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("state_sha256", None)
    return _sha(body)


def _write_state(path: Path, payload: Mapping[str, Any]) -> None:
    body = copy.deepcopy(dict(payload))
    body.pop("state_sha256", None)
    body["state_sha256"] = _state_hash(body)
    _atomic(path, body)


def load_config(repo_root: Path) -> dict[str, Any]:
    payload = _read(repo_root / CONFIG_PATH)
    if not isinstance(payload, dict) or payload.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError("closed-loop config schema mismatch")
    required_true = (
        "closed_loop_enabled",
        "automatic_materialization_enabled",
        "automatic_rollback_enabled",
        "require_pr35_pass",
        "require_pr36_pass",
        "require_disjoint_validation_samples",
        "require_hash_committed_validation_epochs",
    )
    required_false = (
        "manual_approval_required",
        "code_mutation_allowed",
        "arbitrary_parameter_mutation_allowed",
        "hard_safety_gate_mutation_allowed",
        "trade_execution_allowed",
    )
    if any(payload.get(key) is not True for key in required_true):
        raise ValueError("closed-loop required control is disabled")
    if any(payload.get(key) is not False for key in required_false):
        raise ValueError("closed-loop safety control is not fail-closed")
    if int(payload.get("required_research_methodology_version") or 0) != pr35v2.PROMOTION_METHODOLOGY_VERSION:
        raise ValueError("closed-loop research methodology mismatch")
    if int(payload.get("maximum_promotions_per_engine_per_run") or 0) != 1:
        raise ValueError("closed-loop promotion concurrency must remain one per engine")
    return payload


def _engine_spec(engine_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = ap.POLICY_SPECS.get(engine_id)
    allow = POLICY_ALLOWLIST.get(engine_id)
    if not isinstance(spec, dict) or not isinstance(allow, dict):
        raise ValueError(f"engine not allowlisted for autonomous production: {engine_id}")
    return spec, allow


def _config_snapshot(repo_root: Path, engine_id: str) -> dict[str, Any]:
    spec, _ = _engine_spec(engine_id)
    path = repo_root / str(spec["config_path"])
    payload = _read(path)
    if not isinstance(payload, dict):
        raise ValueError(f"missing production config for {engine_id}")
    parameter = str(spec["parameter"])
    if parameter not in payload or not payload.get("policy_version"):
        raise ValueError(f"production config lacks policy contract for {engine_id}")
    return {
        "path": str(spec["config_path"]),
        "parameter": parameter,
        "value": _finite(payload[parameter]),
        "policy_version": str(payload["policy_version"]),
        "payload": payload,
    }


def _bootstrap_state(repo_root: Path, now: datetime) -> dict[str, Any]:
    engines: dict[str, Any] = {}
    for engine_id in sorted(ap.POLICY_SPECS):
        snap = _config_snapshot(repo_root, engine_id)
        if "+auto" in snap["policy_version"]:
            raise RuntimeError("production state missing while an autonomous policy version is already materialized")
        engines[engine_id] = {
            "engine_id": engine_id,
            "status": "ACTIVE",
            "revision": 0,
            "parameter": snap["parameter"],
            "baseline_policy_version": snap["policy_version"],
            "baseline_value": snap["value"],
            "effective_policy_version": snap["policy_version"],
            "value": snap["value"],
            "activated_at": None,
            "source_candidate_id": None,
            "parent": None,
            "blocked_until": None,
            "live_monitor": None,
        }
    state = {
        "schema_version": STATE_SCHEMA,
        "updated_at": _iso(now),
        "controls": {
            "closed_loop_enabled": True,
            "automatic_materialization_enabled": True,
            "automatic_rollback_enabled": True,
            "manual_approval_required": False,
            "code_mutation_allowed": False,
            "arbitrary_parameter_mutation_allowed": False,
            "hard_safety_gate_mutation_allowed": False,
            "trade_execution_allowed": False,
        },
        "engines": engines,
        "candidate_outcomes": {},
    }
    state["state_sha256"] = _state_hash(state)
    return state


def load_state(repo_root: Path, *, create: bool, now: datetime) -> dict[str, Any] | None:
    path = repo_root / STATE_PATH
    payload = _read(path)
    if payload is None:
        return _bootstrap_state(repo_root, now) if create else None
    if not isinstance(payload, dict) or payload.get("schema_version") != STATE_SCHEMA:
        raise ValueError("production state schema mismatch")
    stored = str(payload.get("state_sha256") or "")
    if not stored or stored != _state_hash(payload):
        raise ValueError("production state hash mismatch")
    return payload


def _bounds(engine_id: str, parameter: str) -> tuple[float, float]:
    _, allow = _engine_spec(engine_id)
    params = allow.get("parameters") if isinstance(allow.get("parameters"), Mapping) else {}
    rule = params.get(parameter) if isinstance(params.get(parameter), Mapping) else None
    if rule is None:
        raise ValueError(f"parameter not allowlisted: {engine_id}.{parameter}")
    return float(rule["min"]), float(rule["max"])


def verify_state(repo_root: Path, state: Mapping[str, Any], *, require_config_match: bool = True) -> dict[str, Any]:
    if state.get("schema_version") != STATE_SCHEMA:
        raise ValueError("production state schema mismatch")
    if str(state.get("state_sha256") or "") != _state_hash(state):
        raise ValueError("production state hash mismatch")
    controls = state.get("controls") if isinstance(state.get("controls"), Mapping) else {}
    expected = {
        "closed_loop_enabled": True,
        "automatic_materialization_enabled": True,
        "automatic_rollback_enabled": True,
        "manual_approval_required": False,
        "code_mutation_allowed": False,
        "arbitrary_parameter_mutation_allowed": False,
        "hard_safety_gate_mutation_allowed": False,
        "trade_execution_allowed": False,
    }
    if dict(controls) != expected:
        raise ValueError("production state controls changed")
    engines = state.get("engines") if isinstance(state.get("engines"), Mapping) else {}
    if set(engines) != set(ap.POLICY_SPECS):
        raise ValueError("production state engine set mismatch")
    for engine_id, engine in engines.items():
        if not isinstance(engine, Mapping) or engine.get("status") != "ACTIVE":
            raise ValueError(f"invalid active production state for {engine_id}")
        spec, _ = _engine_spec(engine_id)
        parameter = str(spec["parameter"])
        if engine.get("parameter") != parameter:
            raise ValueError(f"production state parameter mismatch for {engine_id}")
        value = _finite(engine.get("value"))
        lo, hi = _bounds(engine_id, parameter)
        if not lo <= value <= hi:
            raise ValueError(f"production state value outside immutable bounds for {engine_id}")
        revision = int(engine.get("revision") or 0)
        if revision < 0:
            raise ValueError("negative autonomous revision")
        if revision == 0 and engine.get("parent") is not None:
            raise ValueError("baseline production state cannot have parent")
        if revision > 0 and not isinstance(engine.get("parent"), Mapping):
            raise ValueError("autonomous production revision has no rollback parent")
        if require_config_match:
            snap = _config_snapshot(repo_root, engine_id)
            if abs(float(snap["value"]) - value) > 1e-9:
                raise RuntimeError(f"materialized production value diverges for {engine_id}")
            if snap["policy_version"] != str(engine.get("effective_policy_version") or ""):
                raise RuntimeError(f"materialized production version diverges for {engine_id}")
    outcomes = state.get("candidate_outcomes") if isinstance(state.get("candidate_outcomes"), Mapping) else {}
    if len(outcomes) != len(set(outcomes)):
        raise ValueError("duplicate production candidate outcome")
    return {
        "ok": True,
        "engines": len(engines),
        "candidate_outcomes": len(outcomes),
        "active_revisions": {engine_id: int(row.get("revision") or 0) for engine_id, row in engines.items()},
    }


def _research_registry(research_state_dir: Path) -> dict[str, Any]:
    payload = ap._read_json(research_state_dir / ap.REGISTRY_FILENAME)
    if not isinstance(payload, dict):
        raise RuntimeError("closed loop requires an existing autonomous research registry")
    ap.validate_registry(payload)
    return payload


def _terminal_from_outcome(outcome: Mapping[str, Any]) -> str:
    status = str(outcome.get("status") or "")
    if status == "ROLLED_BACK":
        return TERMINAL_ROLLED_BACK
    if status == "STALE":
        return TERMINAL_STALE
    return TERMINAL_PROMOTED


def _append_block(registry: dict[str, Any], outcome: Mapping[str, Any], now: datetime) -> None:
    if str(outcome.get("status") or "") != "ROLLED_BACK":
        return
    candidate_id = str(outcome.get("candidate_id") or "")
    for row in registry.get("rejected_transitions") or []:
        if str(row.get("candidate_id") or "") == candidate_id and row.get("reason") == "autonomous_closed_loop_rollback":
            return
    registry.setdefault("rejected_transitions", []).append({
        "engine_id": outcome.get("engine_id"),
        "parameter": outcome.get("parameter"),
        "from": outcome.get("from_value"),
        "to": outcome.get("to_value"),
        "candidate_id": candidate_id,
        "reason": "autonomous_closed_loop_rollback",
        "blocked_until": outcome.get("blocked_until") or _iso(now + timedelta(days=30)),
    })


def reconcile_research(research_state_dir: Path, repo_root: Path, *, now: Optional[datetime] = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = load_state(repo_root, create=False, now=now)
    if state is None:
        return {"changed": False, "reason": "production_state_not_initialized"}
    verify_state(repo_root, state)
    registry = _research_registry(research_state_dir)
    changed = 0
    outcomes = state.get("candidate_outcomes") if isinstance(state.get("candidate_outcomes"), Mapping) else {}
    for candidate_id, outcome in outcomes.items():
        candidate = (registry.get("candidates") or {}).get(candidate_id)
        if not isinstance(candidate, dict):
            continue
        terminal = _terminal_from_outcome(outcome)
        if candidate.get("status") != terminal:
            candidate["status"] = terminal
            candidate["production_reconciled_at"] = _iso(now)
            candidate["production_policy_version"] = outcome.get("effective_policy_version")
            changed += 1
        _append_block(registry, outcome, now)
        if terminal == TERMINAL_ROLLED_BACK:
            engine = (registry.get("engines") or {}).get(str(outcome.get("engine_id") or ""))
            if isinstance(engine, dict):
                engine["blocked_until"] = outcome.get("blocked_until")
    if changed:
        registry["updated_at"] = _iso(now)
        ap._atomic_json(research_state_dir / ap.REGISTRY_FILENAME, registry)
    return {"changed": bool(changed), "candidates_reconciled": changed}


def _require_candidate_evidence(candidate: Mapping[str, Any], research_state_dir: Path, repo_root: Path) -> None:
    if candidate.get("status") != "PROMOTION_ELIGIBLE_BUT_FROZEN":
        raise ValueError("candidate is not at the PR36 production handoff state")
    if int(candidate.get("promotion_methodology_version") or 0) != pr35v2.PROMOTION_METHODOLOGY_VERSION:
        raise ValueError("candidate research methodology mismatch")
    promotion = candidate.get("promotion_gate") if isinstance(candidate.get("promotion_gate"), Mapping) else {}
    statistical = candidate.get("statistical_gate") if isinstance(candidate.get("statistical_gate"), Mapping) else {}
    if promotion.get("status") != "PASS" or promotion.get("formal_test_performed") is not True:
        raise ValueError("candidate has no formal PR35 PASS")
    if int(promotion.get("formal_sample_n") or 0) != pr35v2.VALIDATION_FIXED_N:
        raise ValueError("candidate PR35 sample size mismatch")
    cfg = pr36v2.load_config(repo_root)
    fixed_n = int(cfg["fixed_paired_n"])
    if statistical.get("status") != "PASS" or statistical.get("formal_test_performed") is not True:
        raise ValueError("candidate has no formal PR36 PASS")
    if statistical.get("fresh_holdout") is not True or int(statistical.get("formal_sample_n") or 0) != fixed_n:
        raise ValueError("candidate PR36 fresh-holdout contract mismatch")
    if list(statistical.get("blocking_reasons") or []):
        raise ValueError("candidate PR36 PASS contains blocking reasons")
    pr35_ids = [str(x) for x in promotion.get("sample_shadow_outcome_ids") or []]
    pr36_ids = [str(x) for x in statistical.get("sample_shadow_outcome_ids") or []]
    if len(pr35_ids) != pr35v2.VALIDATION_FIXED_N or len(set(pr35_ids)) != len(pr35_ids):
        raise ValueError("candidate PR35 sample ids invalid")
    if len(pr36_ids) != fixed_n or len(set(pr36_ids)) != len(pr36_ids):
        raise ValueError("candidate PR36 sample ids invalid")
    if set(pr35_ids) & set(pr36_ids):
        raise ValueError("PR35 and PR36 formal samples overlap")
    refs = candidate.get("validation_epochs") if isinstance(candidate.get("validation_epochs"), Mapping) else {}
    if not isinstance(refs.get("pr35"), Mapping) or not isinstance(refs.get("pr36"), Mapping):
        raise ValueError("candidate lacks hash-committed ValidationEpoch lineage")
    pr35_epoch = ve.verify_epoch_reference(research_state_dir, candidate, stage="PR35", reference=refs["pr35"])
    pr36_epoch = ve.verify_epoch_reference(research_state_dir, candidate, stage="PR36", reference=refs["pr36"])
    if _parse(pr36_epoch["committed_at"]) < _parse(pr35_epoch["committed_at"]):
        raise ValueError("PR36 ValidationEpoch predates PR35")


def _require_allowlisted_transition(candidate: Mapping[str, Any], engine_state: Mapping[str, Any]) -> None:
    engine_id = str(candidate.get("engine_id") or "")
    spec, _ = _engine_spec(engine_id)
    parameter = str(spec["parameter"])
    if str(candidate.get("parameter") or "") != parameter:
        raise ValueError("candidate parameter is not the engine allowlisted parameter")
    if str(candidate.get("gate") or "") != str(spec["gate"]):
        raise ValueError("candidate gate is not the allowlisted research gate")
    current = _finite(candidate.get("from_value"))
    proposed = _finite(candidate.get("to_value"))
    if abs(_finite(engine_state.get("value")) - current) > 1e-9:
        raise RuntimeError("candidate was validated against a stale production value")
    expected = max(float(spec["lower"]), current - float(spec["step"]))
    if abs(proposed - expected) > 1e-9:
        raise ValueError("candidate transition is not the pre-authorized one-step calibration")
    lo, hi = _bounds(engine_id, parameter)
    if not lo <= proposed <= hi:
        raise ValueError("candidate transition is outside immutable bounds")


def _engine_parent(engine: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(engine))


def _materialize_engine(repo_root: Path, engine_id: str, engine: Mapping[str, Any]) -> bool:
    snap = _config_snapshot(repo_root, engine_id)
    parent = engine.get("parent") if isinstance(engine.get("parent"), Mapping) else None
    allowed_versions = {str(engine.get("effective_policy_version") or "")}
    allowed_values = {_finite(engine.get("value"))}
    if parent is not None:
        allowed_versions.add(str(parent.get("effective_policy_version") or ""))
        allowed_values.add(_finite(parent.get("value")))
    if snap["policy_version"] not in allowed_versions or not any(abs(float(snap["value"]) - value) <= 1e-9 for value in allowed_values):
        raise RuntimeError(f"manual policy divergence blocks autonomous materialization for {engine_id}")
    target_value = _finite(engine.get("value"))
    target_version = str(engine.get("effective_policy_version") or "")
    if abs(float(snap["value"]) - target_value) <= 1e-9 and snap["policy_version"] == target_version:
        return False
    payload = dict(snap["payload"])
    original = payload[snap["parameter"]]
    payload[snap["parameter"]] = int(target_value) if isinstance(original, int) and target_value.is_integer() else target_value
    payload["policy_version"] = target_version
    _atomic(repo_root / snap["path"], payload)
    return True


def _mark_stale(registry: dict[str, Any], state: dict[str, Any], candidate: dict[str, Any], now: datetime, reason: str) -> None:
    candidate_id = str(candidate["candidate_id"])
    candidate["status"] = TERMINAL_STALE
    candidate["production_stale_at"] = _iso(now)
    candidate["production_stale_reason"] = reason
    state.setdefault("candidate_outcomes", {})[candidate_id] = {
        "candidate_id": candidate_id,
        "engine_id": candidate.get("engine_id"),
        "parameter": candidate.get("parameter"),
        "from_value": candidate.get("from_value"),
        "to_value": candidate.get("to_value"),
        "status": "STALE",
        "reason": reason,
        "decided_at": _iso(now),
        "effective_policy_version": None,
    }
    ap.append_audit(
        Path(registry["_state_dir"]) / ap.AUDIT_FILENAME,
        "autonomous_production_candidate_stale",
        {"candidate_id": candidate_id, "reason": reason},
        _iso(now),
    )


def _promote_candidate(
    registry: dict[str, Any], state: dict[str, Any], candidate: dict[str, Any], research_state_dir: Path, repo_root: Path, now: datetime
) -> dict[str, Any]:
    candidate_id = str(candidate["candidate_id"])
    engine_id = str(candidate["engine_id"])
    if candidate_id in (state.get("candidate_outcomes") or {}):
        return {"candidate_id": candidate_id, "engine_id": engine_id, "status": "ALREADY_CONSUMED"}
    engine = state["engines"][engine_id]
    blocked_until = engine.get("blocked_until")
    if blocked_until and _parse(blocked_until) > now:
        return {"candidate_id": candidate_id, "engine_id": engine_id, "status": "ENGINE_BLOCKED"}
    _require_candidate_evidence(candidate, research_state_dir, repo_root)
    try:
        _require_allowlisted_transition(candidate, engine)
    except RuntimeError as exc:
        _mark_stale(registry, state, candidate, now, str(exc))
        return {"candidate_id": candidate_id, "engine_id": engine_id, "status": "STALE"}
    snap = _config_snapshot(repo_root, engine_id)
    if snap["policy_version"] != str(engine.get("effective_policy_version") or "") or abs(float(snap["value"]) - _finite(engine.get("value"))) > 1e-9:
        raise RuntimeError("production config and durable production state diverge before promotion")
    parent = _engine_parent(engine)
    revision = int(engine.get("revision") or 0) + 1
    baseline = str(engine["baseline_policy_version"])
    effective = f"{baseline}+auto{revision}"
    new_engine = {
        "engine_id": engine_id,
        "status": "ACTIVE",
        "revision": revision,
        "parameter": str(candidate["parameter"]),
        "baseline_policy_version": baseline,
        "baseline_value": engine["baseline_value"],
        "effective_policy_version": effective,
        "value": _finite(candidate["to_value"]),
        "activated_at": _iso(now),
        "source_candidate_id": candidate_id,
        "parent": parent,
        "blocked_until": None,
        "live_monitor": None,
    }
    state["engines"][engine_id] = new_engine
    outcome = {
        "candidate_id": candidate_id,
        "engine_id": engine_id,
        "parameter": candidate["parameter"],
        "from_value": candidate["from_value"],
        "to_value": candidate["to_value"],
        "status": "PROMOTED",
        "promoted_at": _iso(now),
        "effective_policy_version": effective,
        "pr35_formal_sample_n": candidate["promotion_gate"]["formal_sample_n"],
        "pr36_formal_sample_n": candidate["statistical_gate"]["formal_sample_n"],
    }
    state.setdefault("candidate_outcomes", {})[candidate_id] = outcome
    candidate["status"] = TERMINAL_PROMOTED
    candidate["production_promoted_at"] = _iso(now)
    candidate["production_policy_version"] = effective
    candidate["production_revision"] = revision
    ap.append_audit(
        research_state_dir / ap.AUDIT_FILENAME,
        "autonomous_production_promoted",
        {"candidate": copy.deepcopy(candidate), "production_policy": copy.deepcopy(new_engine)},
        _iso(now),
    )
    return {"candidate_id": candidate_id, "engine_id": engine_id, "status": "PROMOTED", "effective_policy_version": effective}


def _monitor_rollbacks(
    registry: dict[str, Any], state: dict[str, Any], research_state_dir: Path, repo_root: Path, now: datetime
) -> list[dict[str, Any]]:
    rolled: list[dict[str, Any]] = []
    for engine_id, engine in list(state["engines"].items()):
        if int(engine.get("revision") or 0) <= 0 or not isinstance(engine.get("parent"), Mapping) or not engine.get("activated_at"):
            continue
        rows = ap._resolved_history(repo_root, engine_id, str(engine["effective_policy_version"]), _parse(engine["activated_at"]))
        trigger, metrics = ap._rollback_trigger(rows)
        engine["live_monitor"] = {**metrics, "evaluated_at": _iso(now), "rollback_triggered": trigger}
        if not trigger:
            continue
        failed = copy.deepcopy(engine)
        restored = copy.deepcopy(dict(engine["parent"]))
        restored["status"] = "ACTIVE"
        restored["blocked_until"] = _iso(now + timedelta(days=14))
        restored["rollback_from_policy_version"] = failed["effective_policy_version"]
        restored["rollback_at"] = _iso(now)
        restored["live_monitor"] = None
        state["engines"][engine_id] = restored
        candidate_id = str(failed.get("source_candidate_id") or "")
        outcome = (state.get("candidate_outcomes") or {}).get(candidate_id)
        if isinstance(outcome, dict):
            outcome["status"] = "ROLLED_BACK"
            outcome["rolled_back_at"] = _iso(now)
            outcome["rollback_metrics"] = metrics
            outcome["blocked_until"] = _iso(now + timedelta(days=30))
        candidate = (registry.get("candidates") or {}).get(candidate_id)
        if isinstance(candidate, dict):
            candidate["status"] = TERMINAL_ROLLED_BACK
            candidate["production_rolled_back_at"] = _iso(now)
            candidate["production_rollback_metrics"] = metrics
        if isinstance(outcome, Mapping):
            _append_block(registry, outcome, now)
        research_engine = (registry.get("engines") or {}).get(engine_id)
        if isinstance(research_engine, dict):
            research_engine["blocked_until"] = _iso(now + timedelta(days=14))
        ap.append_audit(
            research_state_dir / ap.AUDIT_FILENAME,
            "autonomous_production_rolled_back",
            {"failed_policy": failed, "restored_policy": restored, "live_metrics": metrics},
            _iso(now),
        )
        rolled.append({"engine_id": engine_id, "candidate_id": candidate_id, "metrics": metrics})
    return rolled


def apply_closed_loop(research_state_dir: Path, repo_root: Path, *, now: Optional[datetime] = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cfg = load_config(repo_root)
    # Research remains deliberately frozen.  Production authority is added only
    # here, after its integrity verifier has succeeded.
    integrity = pli.verify(research_state_dir, repo_root)
    if integrity.get("production_promotion_enabled") is not False:
        raise RuntimeError("research gate must remain frozen; closed-loop authority belongs only to this actuator")
    registry = _research_registry(research_state_dir)
    registry["_state_dir"] = str(research_state_dir)
    state = load_state(repo_root, create=True, now=now)
    assert state is not None
    if (repo_root / STATE_PATH).exists():
        verify_state(repo_root, state)

    rollbacks = _monitor_rollbacks(registry, state, research_state_dir, repo_root, now)

    eligible = [
        candidate for candidate in (registry.get("candidates") or {}).values()
        if isinstance(candidate, dict) and candidate.get("status") == "PROMOTION_ELIGIBLE_BUT_FROZEN"
    ]
    by_engine: dict[str, list[dict[str, Any]]] = {}
    for candidate in eligible:
        by_engine.setdefault(str(candidate.get("engine_id") or ""), []).append(candidate)
    if any(len(rows) > int(cfg["maximum_promotions_per_engine_per_run"]) for rows in by_engine.values()):
        raise RuntimeError("multiple simultaneous promotion-eligible candidates for one engine")

    promotions: list[dict[str, Any]] = []
    for engine_id in sorted(by_engine):
        candidate = sorted(by_engine[engine_id], key=lambda row: str(row.get("promotion_eligible_at") or row.get("created_at") or ""))[0]
        promotions.append(_promote_candidate(registry, state, candidate, research_state_dir, repo_root, now))

    registry.pop("_state_dir", None)
    registry["updated_at"] = _iso(now)
    ap._atomic_json(research_state_dir / ap.REGISTRY_FILENAME, registry)

    changed_configs: list[str] = []
    for engine_id, engine in state["engines"].items():
        if _materialize_engine(repo_root, engine_id, engine):
            changed_configs.append(str(ap.POLICY_SPECS[engine_id]["config_path"]))

    state["updated_at"] = _iso(now)
    _write_state(repo_root / STATE_PATH, state)
    persisted = load_state(repo_root, create=False, now=now)
    assert persisted is not None
    verification = verify_state(repo_root, persisted)
    report = {
        "schema_version": REPORT_SCHEMA,
        "generated_at": _iso(now),
        "research_methodology_version": pr35v2.PROMOTION_METHODOLOGY_VERSION,
        "closed_loop_enabled": True,
        "research_gate_remains_frozen": True,
        "promotions": promotions,
        "rollbacks": rollbacks,
        "materialized_config_paths": changed_configs,
        "production_state_path": STATE_PATH,
        "verification": verification,
        "safety": {
            "hard_safety_gate_mutation": False,
            "arbitrary_parameter_mutation": False,
            "code_mutation": False,
            "trade_execution": False,
            "manual_approval_required_after_formal_gates": False,
        },
    }
    _atomic(research_state_dir / REPORT_FILENAME, report)
    return report


def verify_closed_loop(research_state_dir: Path, repo_root: Path) -> dict[str, Any]:
    cfg = load_config(repo_root)
    integrity = pli.verify(research_state_dir, repo_root)
    if integrity.get("production_promotion_enabled") is not False:
        raise RuntimeError("research promotion authority unexpectedly enabled")
    state = load_state(repo_root, create=False, now=datetime.now(timezone.utc))
    if state is None:
        return {"ok": True, "initialized": False, "closed_loop_enabled": cfg["closed_loop_enabled"]}
    result = verify_state(repo_root, state)
    return {"ok": True, "initialized": True, "closed_loop_enabled": True, **result}


def main() -> int:
    parser = argparse.ArgumentParser(description="BriefRooms autonomous policy closed loop")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("reconcile", "apply", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--research-state-dir", required=True)
        p.add_argument("--repo-root", default=".")
        p.add_argument("--now")
    args = parser.parse_args()
    research_state = Path(args.research_state_dir)
    repo_root = Path(args.repo_root)
    now = _parse(args.now) if args.now else datetime.now(timezone.utc)
    if args.command == "reconcile":
        result = reconcile_research(research_state, repo_root, now=now)
    elif args.command == "apply":
        result = apply_closed_loop(research_state, repo_root, now=now)
    else:
        result = verify_closed_loop(research_state, repo_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
