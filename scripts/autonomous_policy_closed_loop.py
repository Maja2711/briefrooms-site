#!/usr/bin/env python3
"""Bounded autonomous production actuator for the PR35 -> PR36 learning loop.

PR35/PR36 stay research-only and retain their strict prospective fixed-N gates.
This actuator is the separate production authority: it consumes only a candidate
that already reached ``PROMOTION_ELIGIBLE_BUT_FROZEN``, re-verifies PR35, the
fresh PR36 holdout, disjoint formal samples and hash-committed ValidationEpochs,
then materializes one explicitly allowlisted scalar policy change.

Production state is committed beside the configs.  Subsequent resolved live
outcomes are monitored with the existing conservative rollback trigger; when it
fires, the immutable parent policy is restored and the failed transition is
blocked.  No code mutation, arbitrary config mutation, hard-gate weakening,
sizing writeback or trade execution is allowed here.
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
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime:
    return ap._parse_time(str(value or ""))


def _finite(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric policy value")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError("non-finite policy value")
    return out


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


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    body = copy.deepcopy(dict(state))
    body.pop("state_sha256", None)
    body["state_sha256"] = _state_hash(body)
    _atomic(path, body)


def load_config(repo_root: Path) -> dict[str, Any]:
    payload = _read(repo_root / CONFIG_PATH)
    if not isinstance(payload, dict) or payload.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError("closed-loop config schema mismatch")
    for key in (
        "closed_loop_enabled", "automatic_rollback_enabled",
        "require_pr35_pass", "require_pr36_pass", "require_disjoint_validation_samples",
        "require_hash_committed_validation_epochs",
    ):
        if payload.get(key) is not True:
            raise ValueError(f"closed-loop required control disabled: {key}")
    materialization = payload.get("automatic_materialization_enabled")
    if materialization not in {True, False}:
        raise ValueError("automatic_materialization_enabled must be boolean")
    if materialization is False and payload.get("production_authority") != "RETIRED_TO_STOCK_TRADING_COMPONENT_PROMOTION":
        raise ValueError("disabled materialization requires explicit retired production authority")
    for key in (
        "manual_approval_required", "code_mutation_allowed", "arbitrary_parameter_mutation_allowed",
        "hard_safety_gate_mutation_allowed", "trade_execution_allowed",
    ):
        if payload.get(key) is not False:
            raise ValueError(f"closed-loop safety control not fail-closed: {key}")
    if int(payload.get("required_research_methodology_version") or 0) != pr35v2.PROMOTION_METHODOLOGY_VERSION:
        raise ValueError("closed-loop research methodology mismatch")
    if int(payload.get("maximum_promotions_per_engine_per_run") or 0) != 1:
        raise ValueError("closed-loop permits exactly one promotion per engine per run")
    return payload


def _spec(engine_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    spec, allow = ap.POLICY_SPECS.get(engine_id), POLICY_ALLOWLIST.get(engine_id)
    if not isinstance(spec, dict) or not isinstance(allow, dict):
        raise ValueError(f"engine not allowlisted: {engine_id}")
    return spec, allow


def _config_snapshot(repo_root: Path, engine_id: str) -> dict[str, Any]:
    spec, _ = _spec(engine_id)
    path = repo_root / str(spec["config_path"])
    payload = _read(path)
    if not isinstance(payload, dict):
        raise ValueError(f"missing production config for {engine_id}")
    parameter = str(spec["parameter"])
    if parameter not in payload or not payload.get("policy_version"):
        raise ValueError(f"production config lacks policy contract for {engine_id}")
    return {
        "path": str(spec["config_path"]), "parameter": parameter,
        "value": _finite(payload[parameter]), "policy_version": str(payload["policy_version"]),
        "payload": payload,
    }


def _bounds(engine_id: str, parameter: str) -> tuple[float, float]:
    _, allow = _spec(engine_id)
    params = allow.get("parameters") if isinstance(allow.get("parameters"), Mapping) else {}
    rule = params.get(parameter) if isinstance(params.get(parameter), Mapping) else None
    if rule is None:
        raise ValueError(f"parameter not allowlisted: {engine_id}.{parameter}")
    return float(rule["min"]), float(rule["max"])


def _bootstrap_state(repo_root: Path, now: datetime) -> dict[str, Any]:
    engines: dict[str, Any] = {}
    for engine_id in sorted(ap.POLICY_SPECS):
        snap = _config_snapshot(repo_root, engine_id)
        if "+auto" in snap["policy_version"]:
            raise RuntimeError("production state missing while an autonomous version is materialized")
        engines[engine_id] = {
            "engine_id": engine_id, "status": "ACTIVE", "revision": 0,
            "parameter": snap["parameter"], "baseline_policy_version": snap["policy_version"],
            "baseline_value": snap["value"], "effective_policy_version": snap["policy_version"],
            "value": snap["value"], "activated_at": None, "source_candidate_id": None,
            "parent": None, "blocked_until": None, "live_monitor": None,
        }
    state = {
        "schema_version": STATE_SCHEMA, "updated_at": _iso(now),
        "controls": {
            "closed_loop_enabled": True, "automatic_materialization_enabled": True,
            "automatic_rollback_enabled": True, "manual_approval_required": False,
            "code_mutation_allowed": False, "arbitrary_parameter_mutation_allowed": False,
            "hard_safety_gate_mutation_allowed": False, "trade_execution_allowed": False,
        },
        "engines": engines, "candidate_outcomes": {},
    }
    state["state_sha256"] = _state_hash(state)
    return state


def load_state(repo_root: Path, *, create: bool, now: datetime) -> dict[str, Any] | None:
    payload = _read(repo_root / STATE_PATH)
    if payload is None:
        return _bootstrap_state(repo_root, now) if create else None
    if not isinstance(payload, dict) or payload.get("schema_version") != STATE_SCHEMA:
        raise ValueError("production state schema mismatch")
    if str(payload.get("state_sha256") or "") != _state_hash(payload):
        raise ValueError("production state hash mismatch")
    return payload


def verify_state(repo_root: Path, state: Mapping[str, Any], *, require_config_match: bool = True) -> dict[str, Any]:
    if state.get("schema_version") != STATE_SCHEMA or str(state.get("state_sha256") or "") != _state_hash(state):
        raise ValueError("invalid production state")
    expected_controls = {
        "closed_loop_enabled": True, "automatic_materialization_enabled": True,
        "automatic_rollback_enabled": True, "manual_approval_required": False,
        "code_mutation_allowed": False, "arbitrary_parameter_mutation_allowed": False,
        "hard_safety_gate_mutation_allowed": False, "trade_execution_allowed": False,
    }
    if dict(state.get("controls") or {}) != expected_controls:
        raise ValueError("production state controls changed")
    engines = state.get("engines") if isinstance(state.get("engines"), Mapping) else {}
    if set(engines) != set(ap.POLICY_SPECS):
        raise ValueError("production state engine set mismatch")
    for engine_id, engine in engines.items():
        spec, _ = _spec(engine_id)
        parameter = str(spec["parameter"])
        if not isinstance(engine, Mapping) or engine.get("status") != "ACTIVE" or engine.get("parameter") != parameter:
            raise ValueError(f"invalid active state for {engine_id}")
        value = _finite(engine.get("value"))
        lo, hi = _bounds(engine_id, parameter)
        if not lo <= value <= hi:
            raise ValueError(f"value outside immutable bounds for {engine_id}")
        revision = int(engine.get("revision") or 0)
        if revision < 0 or (revision == 0 and engine.get("parent") is not None) or (revision > 0 and not isinstance(engine.get("parent"), Mapping)):
            raise ValueError(f"invalid revision lineage for {engine_id}")
        if require_config_match:
            snap = _config_snapshot(repo_root, engine_id)
            if abs(float(snap["value"]) - value) > 1e-9 or snap["policy_version"] != str(engine.get("effective_policy_version") or ""):
                raise RuntimeError(f"materialized production state diverges for {engine_id}")
    return {
        "ok": True, "engines": len(engines),
        "candidate_outcomes": len(state.get("candidate_outcomes") or {}),
        "active_revisions": {key: int(value.get("revision") or 0) for key, value in engines.items()},
    }


def _research_registry(state_dir: Path) -> dict[str, Any]:
    registry = ap._read_json(state_dir / ap.REGISTRY_FILENAME)
    if not isinstance(registry, dict):
        raise RuntimeError("closed loop requires research registry")
    ap.validate_registry(registry)
    return registry


def _terminal(outcome: Mapping[str, Any]) -> str:
    return {"ROLLED_BACK": TERMINAL_ROLLED_BACK, "STALE": TERMINAL_STALE}.get(str(outcome.get("status") or ""), TERMINAL_PROMOTED)


def _ensure_rollback_block(registry: dict[str, Any], outcome: Mapping[str, Any], now: datetime) -> bool:
    if outcome.get("status") != "ROLLED_BACK":
        return False
    candidate_id = str(outcome.get("candidate_id") or "")
    if any(str(row.get("candidate_id") or "") == candidate_id and row.get("reason") == "autonomous_closed_loop_rollback" for row in registry.get("rejected_transitions") or []):
        return False
    registry.setdefault("rejected_transitions", []).append({
        "engine_id": outcome.get("engine_id"), "parameter": outcome.get("parameter"),
        "from": outcome.get("from_value"), "to": outcome.get("to_value"),
        "candidate_id": candidate_id, "reason": "autonomous_closed_loop_rollback",
        "blocked_until": outcome.get("blocked_until") or _iso(now + timedelta(days=30)),
    })
    return True


def reconcile_research(research_state_dir: Path, repo_root: Path, *, now: Optional[datetime] = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = load_state(repo_root, create=False, now=now)
    if state is None:
        return {"changed": False, "reason": "production_state_not_initialized"}
    verify_state(repo_root, state)
    registry = _research_registry(research_state_dir)
    changed = 0
    for candidate_id, outcome in (state.get("candidate_outcomes") or {}).items():
        candidate = (registry.get("candidates") or {}).get(candidate_id)
        if isinstance(candidate, dict):
            terminal = _terminal(outcome)
            if candidate.get("status") != terminal:
                candidate["status"] = terminal
                candidate["production_reconciled_at"] = _iso(now)
                candidate["production_policy_version"] = outcome.get("effective_policy_version")
                changed += 1
        if _ensure_rollback_block(registry, outcome, now):
            changed += 1
        if outcome.get("status") == "ROLLED_BACK":
            engine = (registry.get("engines") or {}).get(str(outcome.get("engine_id") or ""))
            if isinstance(engine, dict) and engine.get("blocked_until") != outcome.get("blocked_until"):
                engine["blocked_until"] = outcome.get("blocked_until")
                changed += 1
    if changed:
        registry["updated_at"] = _iso(now)
        ap._atomic_json(research_state_dir / ap.REGISTRY_FILENAME, registry)
    return {"changed": bool(changed), "changes": changed}


def _require_evidence(candidate: Mapping[str, Any], research_state_dir: Path, repo_root: Path) -> None:
    if candidate.get("status") != "PROMOTION_ELIGIBLE_BUT_FROZEN" or int(candidate.get("promotion_methodology_version") or 0) != 2:
        raise ValueError("candidate is not at the formal PR36 production handoff")
    pr35 = candidate.get("promotion_gate") if isinstance(candidate.get("promotion_gate"), Mapping) else {}
    pr36 = candidate.get("statistical_gate") if isinstance(candidate.get("statistical_gate"), Mapping) else {}
    if pr35.get("status") != "PASS" or pr35.get("formal_test_performed") is not True or int(pr35.get("formal_sample_n") or 0) != pr35v2.VALIDATION_FIXED_N:
        raise ValueError("candidate has no formal PR35 PASS")
    cfg = pr36v2.load_config(repo_root)
    fixed_n = int(cfg["fixed_paired_n"])
    if pr36.get("status") != "PASS" or pr36.get("formal_test_performed") is not True or pr36.get("fresh_holdout") is not True or int(pr36.get("formal_sample_n") or 0) != fixed_n or list(pr36.get("blocking_reasons") or []):
        raise ValueError("candidate has no clean formal PR36 PASS")
    ids35 = [str(x) for x in pr35.get("sample_shadow_outcome_ids") or []]
    ids36 = [str(x) for x in pr36.get("sample_shadow_outcome_ids") or []]
    if len(ids35) != 30 or len(set(ids35)) != 30 or len(ids36) != fixed_n or len(set(ids36)) != fixed_n:
        raise ValueError("formal sample ids invalid")
    if set(ids35) & set(ids36):
        raise ValueError("PR35 and PR36 formal samples overlap")
    refs = candidate.get("validation_epochs") if isinstance(candidate.get("validation_epochs"), Mapping) else {}
    if not isinstance(refs.get("pr35"), Mapping) or not isinstance(refs.get("pr36"), Mapping):
        raise ValueError("candidate lacks hash-committed ValidationEpoch lineage")
    e35 = ve.verify_epoch_reference(research_state_dir, candidate, stage="PR35", reference=refs["pr35"])
    e36 = ve.verify_epoch_reference(research_state_dir, candidate, stage="PR36", reference=refs["pr36"])
    if _parse(e36["committed_at"]) < _parse(e35["committed_at"]):
        raise ValueError("PR36 epoch predates PR35")


def _require_transition(candidate: Mapping[str, Any], engine: Mapping[str, Any]) -> None:
    engine_id = str(candidate.get("engine_id") or "")
    spec, _ = _spec(engine_id)
    parameter = str(spec["parameter"])
    if candidate.get("parameter") != parameter:
        raise ValueError("candidate parameter is not allowlisted")
    if candidate.get("gate") != spec["gate"]:
        raise ValueError("candidate gate is not allowlisted")
    current, proposed = _finite(candidate.get("from_value")), _finite(candidate.get("to_value"))
    if abs(_finite(engine.get("value")) - current) > 1e-9:
        raise RuntimeError("candidate was validated against a stale production value")
    expected = max(float(spec["lower"]), current - float(spec["step"]))
    if abs(proposed - expected) > 1e-9:
        raise ValueError("candidate is not the pre-authorized one-step calibration")
    lo, hi = _bounds(engine_id, parameter)
    if not lo <= proposed <= hi:
        raise ValueError("candidate outside immutable bounds")


def _materialize(repo_root: Path, engine_id: str, engine: Mapping[str, Any]) -> bool:
    snap = _config_snapshot(repo_root, engine_id)
    allowed_versions = {str(engine.get("effective_policy_version") or "")}
    allowed_values = {_finite(engine.get("value"))}
    parent = engine.get("parent") if isinstance(engine.get("parent"), Mapping) else None
    if parent:
        allowed_versions.add(str(parent.get("effective_policy_version") or ""))
        allowed_values.add(_finite(parent.get("value")))
    if engine.get("rollback_from_policy_version"):
        allowed_versions.add(str(engine["rollback_from_policy_version"]))
    if engine.get("rollback_from_value") is not None:
        allowed_values.add(_finite(engine["rollback_from_value"]))
    if snap["policy_version"] not in allowed_versions or not any(abs(float(snap["value"]) - value) <= 1e-9 for value in allowed_values):
        raise RuntimeError(f"manual policy divergence blocks materialization for {engine_id}")
    target_value, target_version = _finite(engine.get("value")), str(engine.get("effective_policy_version") or "")
    if abs(float(snap["value"]) - target_value) <= 1e-9 and snap["policy_version"] == target_version:
        return False
    payload = dict(snap["payload"])
    original = payload[snap["parameter"]]
    payload[snap["parameter"]] = int(target_value) if isinstance(original, int) and target_value.is_integer() else target_value
    payload["policy_version"] = target_version
    _atomic(repo_root / snap["path"], payload)
    return True


def _audit(state_dir: Path, event_type: str, payload: Mapping[str, Any], now: datetime) -> None:
    ap.append_audit(state_dir / ap.AUDIT_FILENAME, event_type, payload, _iso(now))


def _mark_stale(registry: dict[str, Any], state: dict[str, Any], candidate: dict[str, Any], state_dir: Path, now: datetime, reason: str) -> dict[str, Any]:
    cid = str(candidate["candidate_id"])
    candidate["status"] = TERMINAL_STALE
    candidate["production_stale_at"] = _iso(now)
    candidate["production_stale_reason"] = reason
    state.setdefault("candidate_outcomes", {})[cid] = {
        "candidate_id": cid, "engine_id": candidate.get("engine_id"), "parameter": candidate.get("parameter"),
        "from_value": candidate.get("from_value"), "to_value": candidate.get("to_value"),
        "status": "STALE", "reason": reason, "decided_at": _iso(now), "effective_policy_version": None,
    }
    _audit(state_dir, "autonomous_production_candidate_stale", {"candidate_id": cid, "reason": reason}, now)
    return {"candidate_id": cid, "engine_id": candidate.get("engine_id"), "status": "STALE"}


def _promote(registry: dict[str, Any], state: dict[str, Any], candidate: dict[str, Any], state_dir: Path, repo_root: Path, now: datetime) -> dict[str, Any]:
    cid, engine_id = str(candidate["candidate_id"]), str(candidate["engine_id"])
    if cid in (state.get("candidate_outcomes") or {}):
        return {"candidate_id": cid, "engine_id": engine_id, "status": "ALREADY_CONSUMED"}
    engine = state["engines"][engine_id]
    if engine.get("blocked_until") and _parse(engine["blocked_until"]) > now:
        return {"candidate_id": cid, "engine_id": engine_id, "status": "ENGINE_BLOCKED"}
    _require_evidence(candidate, state_dir, repo_root)
    try:
        _require_transition(candidate, engine)
    except RuntimeError as exc:
        return _mark_stale(registry, state, candidate, state_dir, now, str(exc))
    snap = _config_snapshot(repo_root, engine_id)
    if snap["policy_version"] != engine["effective_policy_version"] or abs(float(snap["value"]) - _finite(engine["value"])) > 1e-9:
        raise RuntimeError("production config and durable state diverge before promotion")
    parent = copy.deepcopy(engine)
    revision = int(engine.get("revision") or 0) + 1
    effective = f"{engine['baseline_policy_version']}+auto{revision}"
    new_engine = {
        "engine_id": engine_id, "status": "ACTIVE", "revision": revision,
        "parameter": candidate["parameter"], "baseline_policy_version": engine["baseline_policy_version"],
        "baseline_value": engine["baseline_value"], "effective_policy_version": effective,
        "value": _finite(candidate["to_value"]), "activated_at": _iso(now), "source_candidate_id": cid,
        "parent": parent, "blocked_until": None, "live_monitor": None,
    }
    state["engines"][engine_id] = new_engine
    outcome = {
        "candidate_id": cid, "engine_id": engine_id, "parameter": candidate["parameter"],
        "from_value": candidate["from_value"], "to_value": candidate["to_value"],
        "status": "PROMOTED", "promoted_at": _iso(now), "effective_policy_version": effective,
        "pr35_formal_sample_n": candidate["promotion_gate"]["formal_sample_n"],
        "pr36_formal_sample_n": candidate["statistical_gate"]["formal_sample_n"],
    }
    state.setdefault("candidate_outcomes", {})[cid] = outcome
    candidate["status"] = TERMINAL_PROMOTED
    candidate["production_promoted_at"] = _iso(now)
    candidate["production_policy_version"] = effective
    candidate["production_revision"] = revision
    _audit(state_dir, "autonomous_production_promoted", {"candidate": copy.deepcopy(candidate), "production_policy": copy.deepcopy(new_engine)}, now)
    return {"candidate_id": cid, "engine_id": engine_id, "status": "PROMOTED", "effective_policy_version": effective}


def _monitor_rollbacks(registry: dict[str, Any], state: dict[str, Any], state_dir: Path, repo_root: Path, now: datetime) -> list[dict[str, Any]]:
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
        restored.update({
            "status": "ACTIVE", "blocked_until": _iso(now + timedelta(days=14)),
            "rollback_from_policy_version": failed["effective_policy_version"],
            "rollback_from_value": failed["value"], "rollback_at": _iso(now), "live_monitor": None,
        })
        state["engines"][engine_id] = restored
        cid = str(failed.get("source_candidate_id") or "")
        outcome = (state.get("candidate_outcomes") or {}).get(cid)
        if isinstance(outcome, dict):
            outcome.update({"status": "ROLLED_BACK", "rolled_back_at": _iso(now), "rollback_metrics": metrics, "blocked_until": _iso(now + timedelta(days=30))})
            _ensure_rollback_block(registry, outcome, now)
        candidate = (registry.get("candidates") or {}).get(cid)
        if isinstance(candidate, dict):
            candidate["status"] = TERMINAL_ROLLED_BACK
            candidate["production_rolled_back_at"] = _iso(now)
            candidate["production_rollback_metrics"] = metrics
        research_engine = (registry.get("engines") or {}).get(engine_id)
        if isinstance(research_engine, dict):
            research_engine["blocked_until"] = _iso(now + timedelta(days=14))
        _audit(state_dir, "autonomous_production_rolled_back", {"failed_policy": failed, "restored_policy": restored, "live_metrics": metrics}, now)
        rolled.append({"engine_id": engine_id, "candidate_id": cid, "metrics": metrics})
    return rolled


def apply_closed_loop(research_state_dir: Path, repo_root: Path, *, now: Optional[datetime] = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cfg = load_config(repo_root)
    integrity = pli.verify(research_state_dir, repo_root)
    if integrity.get("production_promotion_enabled") is not False:
        raise RuntimeError("PR35/PR36 research gate must remain frozen")

    if cfg.get("automatic_materialization_enabled") is False:
        state = load_state(repo_root, create=False, now=now)
        verification = (
            verify_state(repo_root, state)
            if state is not None
            else {"ok": True, "initialized": False}
        )
        report = {
            "schema_version": REPORT_SCHEMA,
            "generated_at": _iso(now),
            "research_methodology_version": 2,
            "closed_loop_enabled": True,
            "status": "RESEARCH_ONLY_PRODUCTION_AUTHORITY_RETIRED",
            "production_authority": cfg.get("production_authority"),
            "research_gate_remains_frozen": True,
            "promotions": [],
            "rollbacks": [],
            "materialized_config_paths": [],
            "production_state_path": STATE_PATH,
            "verification": verification,
            "safety": {
                "hard_safety_gate_mutation": False,
                "arbitrary_parameter_mutation": False,
                "code_mutation": False,
                "trade_execution": False,
                "production_materialization": False,
                "manual_approval_required_after_formal_gates": False,
            },
        }
        _atomic(research_state_dir / REPORT_FILENAME, report)
        return report

    registry = _research_registry(research_state_dir)
    state = load_state(repo_root, create=True, now=now)
    assert state is not None
    if (repo_root / STATE_PATH).exists():
        verify_state(repo_root, state)

    rollbacks = _monitor_rollbacks(registry, state, research_state_dir, repo_root, now)
    eligible = [row for row in (registry.get("candidates") or {}).values() if isinstance(row, dict) and row.get("status") == "PROMOTION_ELIGIBLE_BUT_FROZEN"]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in eligible:
        grouped.setdefault(str(row.get("engine_id") or ""), []).append(row)
    if any(len(rows) > int(cfg["maximum_promotions_per_engine_per_run"]) for rows in grouped.values()):
        raise RuntimeError("multiple simultaneous eligible candidates for one engine")
    promotions = [_promote(registry, state, rows[0], research_state_dir, repo_root, now) for _, rows in sorted(grouped.items())]

    registry["updated_at"] = _iso(now)
    ap._atomic_json(research_state_dir / ap.REGISTRY_FILENAME, registry)
    changed: list[str] = []
    for engine_id, engine in state["engines"].items():
        if _materialize(repo_root, engine_id, engine):
            changed.append(str(ap.POLICY_SPECS[engine_id]["config_path"]))
    state["updated_at"] = _iso(now)
    _write_state(repo_root / STATE_PATH, state)
    persisted = load_state(repo_root, create=False, now=now)
    assert persisted is not None
    verification = verify_state(repo_root, persisted)
    report = {
        "schema_version": REPORT_SCHEMA, "generated_at": _iso(now),
        "research_methodology_version": 2, "closed_loop_enabled": True,
        "research_gate_remains_frozen": True, "promotions": promotions, "rollbacks": rollbacks,
        "materialized_config_paths": changed, "production_state_path": STATE_PATH,
        "verification": verification,
        "safety": {
            "hard_safety_gate_mutation": False, "arbitrary_parameter_mutation": False,
            "code_mutation": False, "trade_execution": False,
            "manual_approval_required_after_formal_gates": False,
        },
    }
    _atomic(research_state_dir / REPORT_FILENAME, report)
    return report


def verify_closed_loop(research_state_dir: Path, repo_root: Path) -> dict[str, Any]:
    cfg = load_config(repo_root)
    integrity = pli.verify(research_state_dir, repo_root)
    if integrity.get("production_promotion_enabled") is not False:
        raise RuntimeError("research gate unexpectedly has production authority")
    state = load_state(repo_root, create=False, now=datetime.now(timezone.utc))
    if state is None:
        return {"ok": True, "initialized": False, "closed_loop_enabled": cfg["closed_loop_enabled"]}
    return {"ok": True, "initialized": True, "closed_loop_enabled": True, **verify_state(repo_root, state)}


def main() -> int:
    parser = argparse.ArgumentParser(description="BriefRooms autonomous policy closed loop")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("reconcile", "apply", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--research-state-dir", required=True)
        p.add_argument("--repo-root", default=".")
        p.add_argument("--now")
    args = parser.parse_args()
    state_dir, repo_root = Path(args.research_state_dir), Path(args.repo_root)
    now = _parse(args.now) if args.now else datetime.now(timezone.utc)
    if args.command == "reconcile":
        result = reconcile_research(state_dir, repo_root, now=now)
    elif args.command == "apply":
        result = apply_closed_loop(state_dir, repo_root, now=now)
    else:
        result = verify_closed_loop(state_dir, repo_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
