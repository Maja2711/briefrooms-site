#!/usr/bin/env python3
"""Build the canonical Research Lab health contract.

Health is derived from source contracts, freshness, closed-loop invariants and
latest GitHub Actions conclusions.  It is not a frontend-load indicator.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data/investments/lab_health.json"
SCHEMA = "briefrooms-lab-health-v1"

SOURCES = {
    "experiment_registry": {
        "path": "data/investments/experiment_registry.json",
        "schema": "briefrooms-experiment-registry-v1",
        "timestamp": "generated_at",
        "max_age_hours": 12,
        "workflow": "experiment-registry.yml",
    },
    "experience_store": {
        "path": "data/investments/experience_store_public.json",
        "schema": "briefrooms-experience-store-public-v1",
        "timestamp": "generated_at",
        "max_age_hours": 6,
        "workflow": "experience-store-public.yml",
    },
    "research_lab": {
        "path": "data/investments/research_lab_report.json",
        "schema": "briefrooms-research-lab-report-v2",
        "timestamp": "generated_at",
        "max_age_hours": 192,
        "workflow": "research-lab.yml",
    },
}


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def age_hours(value: Any, now: datetime) -> float | None:
    dt = parse_time(value)
    if not dt:
        return None
    return max(0.0, (now - dt.astimezone(timezone.utc)).total_seconds() / 3600.0)


def source_health(root: Path, name: str, cfg: dict[str, Any], workflows: dict[str, Any], now: datetime) -> dict[str, Any]:
    path = root / cfg["path"]
    payload = load(path)
    reasons: list[str] = []
    severity = "HEALTHY"
    if not payload:
        return {"state": "ERROR", "path": cfg["path"], "reasons": ["missing_or_invalid_json"]}

    if payload.get("schema_version") != cfg["schema"]:
        reasons.append("unexpected_schema")
        severity = "ERROR"

    generated_at = payload.get(cfg["timestamp"])
    age = age_hours(generated_at, now)
    if age is None:
        reasons.append("missing_or_invalid_timestamp")
        severity = "ERROR"
    elif age > float(cfg["max_age_hours"]):
        reasons.append("stale_source")
        if severity != "ERROR":
            severity = "DEGRADED"

    if name == "experiment_registry":
        errors = int((payload.get("summary") or {}).get("errors") or 0)
        if errors:
            reasons.append(f"registry_errors:{errors}")
            severity = "ERROR"
    elif name == "research_lab":
        if payload.get("execution_loop_closed") is not True:
            reasons.append("execution_loop_not_closed")
            severity = "ERROR"
        if int(payload.get("queue_remaining") or 0) != 0:
            reasons.append("research_queue_not_drained")
            severity = "ERROR"

    wf = workflows.get(cfg["workflow"]) or {}
    conclusion = str(wf.get("conclusion") or "").lower()
    status = str(wf.get("status") or "").lower()
    if wf:
        if status not in {"completed", ""}:
            reasons.append(f"workflow_status:{status}")
            if severity != "ERROR":
                severity = "DEGRADED"
        elif conclusion != "success":
            reasons.append(f"workflow_conclusion:{conclusion or 'unknown'}")
            severity = "ERROR"
    else:
        reasons.append("workflow_status_unavailable")
        if severity != "ERROR":
            severity = "DEGRADED"

    return {
        "state": severity,
        "path": cfg["path"],
        "generated_at": generated_at,
        "age_hours": round(age, 3) if age is not None else None,
        "max_age_hours": cfg["max_age_hours"],
        "workflow": cfg["workflow"],
        "workflow_status": wf or None,
        "reasons": reasons,
    }


def build(root: Path, workflows: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sources = {name: source_health(root, name, cfg, workflows, now) for name, cfg in SOURCES.items()}
    states = [row["state"] for row in sources.values()]
    overall = "ERROR" if "ERROR" in states else ("DEGRADED" if "DEGRADED" in states else "HEALTHY")
    reasons = [f"{name}:{reason}" for name, row in sources.items() for reason in row.get("reasons", [])]
    return {
        "schema_version": SCHEMA,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "status": overall,
        "read_only": True,
        "production_authority": False,
        "sources": sources,
        "reasons": reasons,
        "contract": {
            "healthy_means": "all canonical Lab sources valid and fresh, closed-loop invariants satisfied, and latest source workflows successful",
            "frontend_load_success_is_not_health": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--workflow-status", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    workflows = load(args.workflow_status) if args.workflow_status else {}
    payload = build(args.root.resolve(), workflows)
    output = args.output if args.output.is_absolute() else args.root.resolve() / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "reasons": payload["reasons"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
