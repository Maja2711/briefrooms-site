#!/usr/bin/env python3
"""Hypothesis -> Experiment Compiler + prospective shadow launcher.

The compiler is deterministic and authority-free. It converts hypotheses from
Lesson/Hypothesis Registry v1 into immutable shadow experiment contracts that
are compatible with ValidationEpoch candidate definitions.

Launching an experiment commits ValidationEpoch *before* the experiment may
consume formal evidence. The runtime registry is stored in the supplied private
state directory; production policy remains untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import validation_epoch as ve
    from lesson_hypothesis_registry import (
        DEFAULT_REGISTRY,
        load_registry,
        ready_hypotheses,
        validate_registry,
    )
except ModuleNotFoundError:  # pragma: no cover
    from scripts import validation_epoch as ve
    from scripts.lesson_hypothesis_registry import (
        DEFAULT_REGISTRY,
        load_registry,
        ready_hypotheses,
        validate_registry,
    )

SCHEMA_VERSION = "briefrooms-compiled-experiment-registry-v1"
RUNTIME_FILENAME = "compiled_experiment_registry_v1.json"
POLICY_SHADOW_FILENAME = "policy_shadow_outcomes.jsonl"

ZERO_AUTHORITY = {
    "production_policy_writeback": False,
    "production_ranking_writeback": False,
    "production_sizing_writeback": False,
    "trade_execution": False,
    "automatic_promotion": False,
    "learning_ledger_writeback": False,
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(value: str | datetime) -> str:
    return _parse_time(value).isoformat().replace("+00:00", "Z")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise ValueError(f"non-object row {line_no} in {path}")
        rows.append(row)
    return rows


def _stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}-{_sha(payload)[:24]}"


def compile_hypothesis(hypothesis: Mapping[str, Any]) -> dict[str, Any]:
    spec = hypothesis.get("experiment_spec")
    if not isinstance(spec, Mapping):
        raise ValueError("hypothesis has no experiment_spec")

    immutable = {
        "hypothesis_id": str(hypothesis.get("hypothesis_id") or ""),
        "lesson_ids": sorted(str(x) for x in hypothesis.get("lesson_ids") or []),
        "stage": str(spec.get("stage") or ""),
        "engine_id": str(spec.get("engine_id") or ""),
        "parameter": str(spec.get("parameter") or ""),
        "gate": str(spec.get("gate") or ""),
        "from_value": spec.get("from_value"),
        "to_value": spec.get("to_value"),
        "promotion_methodology_version": int(spec.get("promotion_methodology_version") or 0),
        "validation_target_n": int(spec.get("validation_target_n") or 0),
    }
    experiment_id = _stable_id("hexp", immutable)
    candidate_id = _stable_id("hxcand", {"experiment_id": experiment_id, **immutable})

    candidate = {
        "candidate_id": candidate_id,
        "engine_id": immutable["engine_id"],
        "parameter": immutable["parameter"],
        "gate": immutable["gate"],
        "from_value": immutable["from_value"],
        "to_value": immutable["to_value"],
        "promotion_methodology_version": immutable["promotion_methodology_version"],
        "validation_target_n": immutable["validation_target_n"],
    }

    inference = hypothesis.get("inference") if isinstance(hypothesis.get("inference"), Mapping) else {}
    fixed_n = int(inference.get("fixed_n") or candidate["validation_target_n"])
    if fixed_n != int(candidate["validation_target_n"]):
        raise ValueError(f"{immutable['hypothesis_id']}: fixed_n disagrees with validation_target_n")

    primary_plan = {
        "method": str(inference.get("formal_test") or "fixed_n_once"),
        "fixed_n": fixed_n,
        "formal_evaluation_count": 1,
        "repeated_looks_allowed": bool(inference.get("repeated_looks_allowed_for_formal_decision")),
        "historical_backfill": bool(inference.get("historical_backfill")),
        "success_criteria": [dict(x) for x in hypothesis.get("success_criteria") or []],
        "falsification_criteria": [dict(x) for x in hypothesis.get("falsification_criteria") or []],
    }
    if primary_plan["repeated_looks_allowed"]:
        raise ValueError(f"{immutable['hypothesis_id']}: formal repeated looks are forbidden")
    if primary_plan["historical_backfill"]:
        raise ValueError(f"{immutable['hypothesis_id']}: historical backfill is forbidden")

    shadow_anytime_plan = {
        "enabled": bool(inference.get("shadow_anytime_monitoring", True)),
        "decision_authority": False,
        "purpose": "descriptive_diagnostics_only_until_fixed_n_formal_test",
    }

    return {
        "experiment_id": experiment_id,
        "hypothesis_id": immutable["hypothesis_id"],
        "lesson_ids": immutable["lesson_ids"],
        "system_class": "LAB",
        "mode": "SHADOW",
        "stage": immutable["stage"],
        "status": "COMPILED",
        "claim": str(hypothesis.get("claim") or ""),
        "sample_unit": spec.get("sample_unit"),
        "candidate": candidate,
        "primary_inference_plan": primary_plan,
        "shadow_anytime_plan": shadow_anytime_plan,
        "validation_epoch": None,
        "production_impact": False,
        "automatic_promotion": False,
        "authority": dict(ZERO_AUTHORITY),
        "contract_sha256": _sha({
            "immutable": immutable,
            "candidate": candidate,
            "primary_inference_plan": primary_plan,
            "shadow_anytime_plan": shadow_anytime_plan,
        }),
    }


def compile_registry(source_registry: Mapping[str, Any]) -> dict[str, Any]:
    validate_registry(source_registry)
    experiments = [compile_hypothesis(row) for row in ready_hypotheses(source_registry)]
    ids = [str(row["experiment_id"]) for row in experiments]
    if len(ids) != len(set(ids)):
        raise ValueError("compiled experiment id collision")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_registry_sha256": source_registry["registry_sha256"],
        "mode": "prospective_shadow_research",
        "experiments": experiments,
        "summary": {
            "total": len(experiments),
            "compiled": len(experiments),
            "running_shadow": 0,
        },
        "authority": dict(ZERO_AUTHORITY),
    }
    payload["registry_sha256"] = _sha(payload)
    return payload


def _runtime_hash(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("registry_sha256", None)
    return _sha(body)


def validate_compiled_registry(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("compiled experiment registry schema mismatch")
    for key, expected in ZERO_AUTHORITY.items():
        if (payload.get("authority") or {}).get(key) is not expected:
            raise ValueError(f"compiled registry authority violation: {key}")
    experiments = payload.get("experiments")
    if not isinstance(experiments, list):
        raise ValueError("compiled experiments must be a list")
    ids: set[str] = set()
    for row in experiments:
        if not isinstance(row, Mapping):
            raise ValueError("compiled experiment row is not an object")
        eid = str(row.get("experiment_id") or "")
        if not eid or eid in ids:
            raise ValueError("duplicate/empty compiled experiment id")
        ids.add(eid)
        if row.get("production_impact") is not False or row.get("automatic_promotion") is not False:
            raise ValueError(f"experiment has production authority: {eid}")
        candidate = row.get("candidate")
        if not isinstance(candidate, Mapping):
            raise ValueError(f"experiment candidate missing: {eid}")
        ve.candidate_definition(candidate)
        if str(row.get("stage") or "") not in ve.STAGES:
            raise ValueError(f"unsupported ValidationEpoch stage: {eid}")
    if str(payload.get("registry_sha256") or "") != _runtime_hash(payload):
        raise ValueError("compiled experiment registry hash mismatch")
    return {
        "ok": True,
        "experiments": len(experiments),
        "running_shadow": sum(1 for row in experiments if row.get("status") == "RUNNING_SHADOW"),
        "zero_authority": True,
    }


def _write_registry(path: Path, payload: Mapping[str, Any]) -> None:
    body = dict(payload)
    body.pop("registry_sha256", None)
    body["registry_sha256"] = _sha(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def launch_shadow_experiments(
    source_registry: Mapping[str, Any],
    state_dir: Path,
    *,
    committed_at: str | datetime,
    shadow_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compile and preregister all READY_FOR_SHADOW hypotheses.

    ValidationEpoch is committed first. Only after a valid epoch reference exists
    is the runtime experiment marked RUNNING_SHADOW.
    """
    runtime = compile_registry(source_registry)
    rows = list(shadow_rows) if shadow_rows is not None else _read_jsonl(state_dir / POLICY_SHADOW_FILENAME)
    commit_iso = _iso(committed_at)

    for experiment in runtime["experiments"]:
        candidate = experiment["candidate"]
        reference = ve.commit_epoch(
            state_dir,
            candidate,
            stage=str(experiment["stage"]),
            committed_at=commit_iso,
            primary_inference_plan=experiment["primary_inference_plan"],
            shadow_anytime_plan=experiment["shadow_anytime_plan"],
            shadow_rows=rows,
        )
        event = ve.verify_epoch_reference(
            state_dir,
            candidate,
            stage=str(experiment["stage"]),
            reference=reference,
        )
        boundary = ve.eligible_after(event)
        if boundary != _parse_time(commit_iso):
            raise RuntimeError("ValidationEpoch boundary differs from launch timestamp")
        experiment["validation_epoch"] = reference
        experiment["status"] = "RUNNING_SHADOW"
        experiment["started_at"] = commit_iso

    runtime["summary"]["running_shadow"] = sum(
        1 for row in runtime["experiments"] if row.get("status") == "RUNNING_SHADOW"
    )
    runtime["summary"]["compiled"] = sum(
        1 for row in runtime["experiments"] if row.get("status") == "COMPILED"
    )
    runtime["launched_at"] = commit_iso
    runtime["validation_epoch_ledger"] = ve.LEDGER_FILENAME
    runtime["authority"] = dict(ZERO_AUTHORITY)

    path = state_dir / RUNTIME_FILENAME
    _write_registry(path, runtime)
    stored = json.loads(path.read_text(encoding="utf-8"))
    validate_compiled_registry(stored)
    ve.verify_chain(state_dir / ve.LEDGER_FILENAME)
    return stored


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile/launch hypothesis-driven shadow experiments")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--now")
    args = parser.parse_args()

    source = load_registry(args.registry)
    if not args.launch:
        payload = compile_registry(source)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.state_dir is None:
        raise SystemExit("--state-dir is required with --launch")
    now = _parse_time(args.now) if args.now else datetime.now(timezone.utc)
    payload = launch_shadow_experiments(source, args.state_dir, committed_at=now)
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
