#!/usr/bin/env python3
"""PR37 — read-only Autonomous Policy Observatory.

Consumes validated private PR35/PR36 state and emits a small sanitized public
snapshot. It never mutates policy state. The public file changes only when a
meaningful policy/candidate/metric state changes, avoiding timestamp-only churn.

PR-A adds methodology-v2 awareness: PR36_HOLDOUT and
PROMOTION_ELIGIBLE_BUT_FROZEN remain visible as research challengers while the
public safety block explicitly reports that production promotion is frozen.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    import autonomous_policy_promotion as ap
    import statistical_promotion_gate as sg
    import statistical_promotion_gate_v2 as sg2
except ModuleNotFoundError:  # pragma: no cover
    from scripts import autonomous_policy_promotion as ap
    from scripts import statistical_promotion_gate as sg
    from scripts import statistical_promotion_gate_v2 as sg2

SCHEMA = "briefrooms-autonomous-policy-observatory-v1"
PUBLIC_PATH = "data/public/autonomous_policy_observatory.json"
PRIVATE_PATH = "autonomous_policy_observatory_private.json"
BASELINES_PATH = "data/investments/autonomous_policy_baselines.json"

VISIBLE_EVENTS = {
    "candidate_created",
    "candidate_created_methodology_v2",
    "candidate_methodology_v2_reset",
    "policy_promoted",
    "policy_rolled_back",
    "statistical_promotion_pass",
    "statistical_promotion_hold",
    "statistical_promotion_rejected",
    "pr35_fixed_validation_pass",
    "pr35_fixed_validation_rejected",
    "pr36_fresh_holdout_pass_promotion_frozen",
    "pr36_fresh_holdout_rejected",
    "legacy_policy_frozen_for_methodology_v2",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    tmp.replace(path)


def _candidate_priority(candidate: Mapping[str, Any]) -> int:
    order = {
        "SHADOW_VALIDATION": 0,
        "PR36_HOLDOUT": 1,
        "PROMOTION_ELIGIBLE_BUT_FROZEN": 2,
        "PROMOTED": 3,
        "STATISTICAL_REJECTED": 4,
        "ROLLED_BACK": 5,
        "REJECTED": 6,
    }
    return order.get(str(candidate.get("status") or ""), 9)


def _current_candidate(registry: Mapping[str, Any], engine_id: str) -> Mapping[str, Any] | None:
    rows = [
        row for row in (registry.get("candidates") or {}).values()
        if isinstance(row, Mapping) and row.get("engine_id") == engine_id
    ]
    active_statuses = {"SHADOW_VALIDATION", "PR36_HOLDOUT", "PROMOTION_ELIGIBLE_BUT_FROZEN", "PROMOTED"}
    active = [row for row in rows if row.get("status") in active_statuses]
    return sorted(active, key=lambda row: (_candidate_priority(row), str(row.get("created_at") or "")))[0] if active else None


def _progress(candidate: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    validation = candidate.get("validation") if isinstance(candidate.get("validation"), Mapping) else {}
    statistical = candidate.get("statistical_gate") if isinstance(candidate.get("statistical_gate"), Mapping) else {}
    metrics = statistical.get("metrics") if isinstance(statistical.get("metrics"), Mapping) else {}
    if candidate.get("status") in {"PR36_HOLDOUT", "PROMOTION_ELIGIBLE_BUT_FROZEN"}:
        observed = int(statistical.get("observed_n") or metrics.get("n") or 0)
        target = int(statistical.get("target_n") or 30)
        stage = "PR36_FRESH_HOLDOUT"
    else:
        observed = int(validation.get("n") or 0)
        target = int(candidate.get("validation_target_n") or 25)
        stage = "PR35_VALIDATION"
    return {
        "stage": stage,
        "paired_n": observed,
        "required_n": target,
        "progress_percent": min(100, round(100 * observed / target)) if target else 0,
    }


def _engine_view(
    registry: Mapping[str, Any],
    auth: Mapping[str, Any],
    baselines: Mapping[str, Any],
    engine_id: str,
    repo_root: Path,
) -> dict[str, Any]:
    state = (registry.get("engines") or {}).get(engine_id) or {}
    spec = ap.POLICY_SPECS.get(engine_id) or {}
    baseline_spec = (baselines.get("engines") or {}).get(engine_id) or {}
    parameter = str(spec.get("parameter") or baseline_spec.get("parameter") or "")
    config = _read_json(repo_root / str(spec.get("config_path") or ""), {}) if spec.get("config_path") else {}
    overrides = state.get("overrides") if isinstance(state.get("overrides"), Mapping) else {}
    if parameter in overrides:
        active_value = overrides[parameter]
    elif int(state.get("revision") or 0) == 0 and baseline_spec.get("baseline_value") is not None:
        active_value = baseline_spec.get("baseline_value")
    else:
        active_value = config.get(parameter)
    candidate = _current_candidate(registry, engine_id)
    authorized = int(state.get("revision") or 0) == 0 or str(state.get("policy_id") or "") in (auth.get("authorizations") or {})
    row: dict[str, Any] = {
        "engine": engine_id,
        "active": {
            "policy_version": state.get("effective_policy_version"),
            "revision": int(state.get("revision") or 0),
            "parameter": parameter,
            "value": active_value,
            "baseline": int(state.get("revision") or 0) == 0,
            "statistically_authorized": bool(authorized),
        },
        "blocked_until": state.get("blocked_until"),
        "challenger": None,
    }
    if candidate:
        validation = candidate.get("validation") if isinstance(candidate.get("validation"), Mapping) else {}
        stat = candidate.get("statistical_gate") if isinstance(candidate.get("statistical_gate"), Mapping) else {}
        metrics = stat.get("metrics") if isinstance(stat.get("metrics"), Mapping) else {}
        row["challenger"] = {
            "status": candidate.get("status"),
            "parameter": candidate.get("parameter"),
            "from_value": candidate.get("from_value"),
            "to_value": candidate.get("to_value"),
            "promotion_methodology_version": candidate.get("promotion_methodology_version"),
            "training_n": int((candidate.get("training") or {}).get("n") or 0),
            "validation_n": int(validation.get("n") or 0),
            "statistical_status": stat.get("status") or "WAITING_FOR_PR35_GATE",
            "progress": _progress(candidate),
            "net_incremental_mean_percent": metrics.get("paired_net_incremental_mean_percent"),
            "net_positive_rate": metrics.get("paired_net_positive_rate"),
            "confidence_interval_percent": [metrics.get("bootstrap_ci_low_percent"), metrics.get("bootstrap_ci_high_percent")],
            "cost_stress_percent": metrics.get("cost_stress_percent"),
            "blocking_reasons": list(stat.get("blocking_reasons") or []),
        }
    return row


def _timeline(state_dir: Path, limit: int = 12) -> list[dict[str, Any]]:
    rows = ap._read_jsonl(state_dir / ap.AUDIT_FILENAME)
    visible: list[dict[str, Any]] = []
    for event in rows:
        event_type = str(event.get("event_type") or "")
        if event_type not in VISIBLE_EVENTS:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        candidate = payload.get("candidate") if isinstance(payload.get("candidate"), Mapping) else payload
        visible.append({
            "at": event.get("occurred_at"),
            "type": event_type,
            "engine": candidate.get("engine_id") or payload.get("engine_id"),
            "from_value": candidate.get("from_value"),
            "to_value": candidate.get("to_value"),
        })
    return visible[-limit:]


def build(state_dir: Path, repo_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    ap.verify_state(state_dir)
    registry = _read_json(state_dir / ap.REGISTRY_FILENAME, {})
    governance = registry.get("governance") if isinstance(registry.get("governance"), Mapping) else {}
    methodology_v2 = int(governance.get("promotion_methodology_version") or 0) == 2
    if methodology_v2:
        sg2.verify(state_dir, repo_root)
        auth = sg._load_authorizations(state_dir / sg.AUTH_FILENAME)
        statistical_report = _read_json(state_dir / sg2.REPORT_FILENAME, {})
    else:
        sg.verify_state(state_dir)
        auth = sg._load_authorizations(state_dir / sg.AUTH_FILENAME)
        statistical_report = _read_json(state_dir / sg.REPORT_FILENAME, {})
    baselines = _read_json(repo_root / BASELINES_PATH, {})
    if baselines.get("schema_version") != "briefrooms-autonomous-policy-baselines-v1":
        raise ValueError("observatory baseline schema mismatch")
    engines = [_engine_view(registry, auth, baselines, engine_id, repo_root) for engine_id in sorted(registry.get("engines") or {})]
    public_body = {
        "schema_version": SCHEMA,
        "mode": "read_only_observatory",
        "autonomous_scope": sorted(registry.get("engines") or {}),
        "engines": engines,
        "timeline": _timeline(state_dir),
        "safety": {
            "code_rewriting": False,
            "arbitrary_parameter_changes": False,
            "trade_execution": False,
            "human_approval_required": False,
            "statistical_gate_required": True,
            "automatic_rollback": True,
            "promotion_mode": governance.get("promotion_mode") or "LEGACY_PR35_PR36",
            "production_promotion_enabled": governance.get("production_promotion_enabled", True),
            "promotion_methodology_version": governance.get("promotion_methodology_version", 1),
        },
    }
    public_body["state_digest"] = _digest(public_body)
    private = {
        "schema_version": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "public": public_body,
        "registry": registry,
        "authorizations": auth,
        "statistical_report": statistical_report,
        "promotion_status": _read_json(state_dir / ap.STATUS_FILENAME, {}),
    }
    return public_body, private


def publish(state_dir: Path, repo_root: Path) -> dict[str, Any]:
    public, private = build(state_dir, repo_root)
    target = repo_root / PUBLIC_PATH
    existing = _read_json(target, {})
    changed = not isinstance(existing, Mapping) or existing.get("state_digest") != public.get("state_digest")
    if changed:
        _write(target, public)
    _write(state_dir / PRIVATE_PATH, private)
    return {"changed": changed, "public_path": PUBLIC_PATH, "state_digest": public["state_digest"], "engines": len(public["engines"])}


def main() -> int:
    parser = argparse.ArgumentParser(description="PR37 Autonomous Policy Observatory")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    repo_root = Path(args.repo_root)
    if args.verify:
        public, _ = build(Path(args.state_dir), repo_root)
        print(json.dumps({"ok": True, "state_digest": public["state_digest"], "engines": len(public["engines"])}, sort_keys=True))
        return 0
    print(json.dumps(publish(Path(args.state_dir), repo_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
