#!/usr/bin/env python3
"""Engine-specific BriefRooms learning framework.

Design invariant: one engine = one isolated learning loop.

The framework shares schemas, hash-chain helpers, registry validation, a read-only
Lesson knowledge bus, and a read-only meta-learning observatory. It never shares
engine-local state and never writes production policy, rankings, sizing, model
configuration, or trades.

GPW/US Daily remain delegated to the existing PR35 closed loop. BRACE and WES
have first-class engine-local evidence adapters. The remaining canonical public
experiments are registered with isolated loop partitions and canonical snapshot
adapters so each engine has an explicit local learning surface.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import experiment_registry as public_registry
except ModuleNotFoundError:  # pragma: no cover
    from scripts import experiment_registry as public_registry

REGISTRY_SCHEMA = "briefrooms-learning-engine-registry-v1"
EVIDENCE_SCHEMA = "briefrooms-engine-learning-evidence-v1"
LESSON_SCHEMA = "briefrooms-engine-learning-lesson-v1"
HYPOTHESIS_SCHEMA = "briefrooms-engine-learning-hypothesis-v1"
EXPERIMENT_SCHEMA = "briefrooms-engine-learning-experiment-v1"
EPOCH_SCHEMA = "briefrooms-engine-learning-validation-epoch-v1"
RESULT_SCHEMA = "briefrooms-engine-learning-result-v1"
KNOWLEDGE_SCHEMA = "briefrooms-learning-knowledge-bus-v1"
LOOP_STATE_SCHEMA = "briefrooms-engine-learning-loop-state-v1"
HYPOTHESIS_INPUT_SCHEMA = "briefrooms-engine-hypothesis-input-bundle-v1"
OBSERVATORY_SCHEMA = "briefrooms-meta-learning-observatory-v1"

DEFAULT_REGISTRY = Path("data/investments/learning_engine_registry_v1.json")
KNOWLEDGE_BUS_FILENAME = "knowledge_bus.jsonl"
OBSERVATORY_FILENAME = "meta_learning_observatory.json"
HYPOTHESIS_INPUT_FILENAME = "hypothesis_inputs.json"
LOOP_STATE_FILENAME = "loop_state.json"

LIFECYCLE = [
    "LESSON",
    "HYPOTHESIS",
    "EXPERIMENT",
    "VALIDATION_EPOCH",
    "EVIDENCE",
    "RESULT",
    "LESSON",
]

ZERO_AUTHORITY = {
    "production_policy_writeback": False,
    "production_ranking_writeback": False,
    "production_sizing_writeback": False,
    "trade_execution": False,
    "automatic_promotion": False,
    "automatic_cross_engine_writeback": False,
    "automatic_model_tuning": False,
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}-{_sha(payload)[:24]}"


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def registry_hash(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("registry_sha256", None)
    return _sha(body)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object: {path}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not isinstance(row, dict):
            raise ValueError(f"non-object row {line_no}: {path}")
        rows.append(row)
    return rows


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_line(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _verify_chain(path: Path, *, schema: str, id_field: str) -> dict[str, Any]:
    rows = _read_jsonl(path)
    previous = "GENESIS"
    ids: set[str] = set()
    for index, row in enumerate(rows):
        if row.get("schema_version") != schema:
            raise ValueError(f"ledger schema mismatch at {index}: {path}")
        event_id = str(row.get(id_field) or "")
        if not event_id or event_id in ids:
            raise ValueError(f"duplicate/empty {id_field} at {index}: {path}")
        ids.add(event_id)
        if row.get("previous_hash") != previous:
            raise ValueError(f"ledger chain break at {index}: {path}")
        body = dict(row)
        stored = str(body.pop("row_sha256", ""))
        if not stored or stored != _sha(body):
            raise ValueError(f"ledger hash mismatch at {index}: {path}")
        previous = stored
    return {"ok": True, "events": len(rows), "head_hash": previous}


def _append_chained(path: Path, *, schema: str, id_field: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    chain = _verify_chain(path, schema=schema, id_field=id_field)
    row = dict(payload)
    row["schema_version"] = schema
    row["previous_hash"] = chain["head_hash"]
    row["row_sha256"] = _sha(row)
    _append_line(path, row)
    _verify_chain(path, schema=schema, id_field=id_field)
    return row


def _ids(path: Path, id_field: str) -> set[str]:
    return {str(row.get(id_field) or "") for row in _read_jsonl(path) if row.get(id_field)}


def validate_learning_registry(payload: Mapping[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    _require(payload.get("schema_version") == REGISTRY_SCHEMA, "learning engine registry schema mismatch")
    governance = payload.get("governance")
    _require(isinstance(governance, Mapping), "learning registry governance missing")
    for key, expected in ZERO_AUTHORITY.items():
        _require(governance.get(key) is expected, f"governance invariant violated: {key}")
    _require(governance.get("shared_framework_only") is True, "framework must be shared-contract-only")
    _require(governance.get("engine_state_isolation_required") is True, "engine state isolation required")
    _require(governance.get("cross_engine_lessons_require_local_revalidation") is True, "cross-engine revalidation required")
    _require(governance.get("meta_observatory_decision_authority") is False, "meta observatory must be read-only")

    engines = payload.get("engines")
    _require(isinstance(engines, list) and engines, "engines must be a non-empty list")
    engine_ids: set[str] = set()
    partitions: set[str] = set()
    ledger_paths: set[str] = set()
    mapped_public: set[str] = set()

    for row in engines:
        _require(isinstance(row, Mapping), "engine registry row is not an object")
        engine_id = str(row.get("engine_id") or "")
        _require(engine_id and engine_id not in engine_ids, f"duplicate/empty engine_id: {engine_id}")
        engine_ids.add(engine_id)
        _require(row.get("loop_status") == "ACTIVE", f"engine loop must be ACTIVE: {engine_id}")
        _require(row.get("lifecycle") == LIFECYCLE, f"lifecycle mismatch: {engine_id}")

        partition = str(row.get("state_partition") or "")
        _require(partition and partition not in partitions, f"state partition collision: {engine_id}")
        partitions.add(partition)

        authority = row.get("authority")
        _require(isinstance(authority, Mapping), f"authority missing: {engine_id}")
        for key, expected in ZERO_AUTHORITY.items():
            _require(authority.get(key) is expected, f"engine authority violation {engine_id}: {key}")

        exchange = row.get("knowledge_exchange")
        _require(isinstance(exchange, Mapping), f"knowledge exchange missing: {engine_id}")
        _require(exchange.get("automatic_cross_engine_writeback") is False, f"cross-engine writeback enabled: {engine_id}")
        _require(exchange.get("external_lesson_requires_local_experiment") is True, f"external lesson revalidation missing: {engine_id}")
        _require(exchange.get("consume_external_lessons_as_hypothesis_inputs_only") is True, f"external lessons may bypass hypothesis: {engine_id}")

        adapter = row.get("adapter")
        _require(isinstance(adapter, Mapping), f"adapter missing: {engine_id}")
        _require(bool(str(adapter.get("kind") or "")), f"adapter kind missing: {engine_id}")
        _require(bool(str(adapter.get("source") or "")), f"adapter source missing: {engine_id}")

        ledgers = row.get("local_ledgers")
        _require(isinstance(ledgers, Mapping), f"local ledgers missing: {engine_id}")
        for name in ("lessons", "hypotheses", "experiments", "validation_epochs", "evidence", "results"):
            path = str(ledgers.get(name) or "")
            _require(path.startswith(partition + "/"), f"ledger outside local partition {engine_id}: {name}")
            _require(path not in ledger_paths, f"ledger collision: {path}")
            ledger_paths.add(path)

        public_id = row.get("public_experiment_id")
        if public_id is not None:
            public_text = str(public_id)
            _require(public_text not in mapped_public, f"public experiment mapped twice: {public_text}")
            mapped_public.add(public_text)

    _require({"gpw_daily", "us_daily", "brace_spx", "wes"}.issubset(engine_ids), "core engine loops missing")
    by_engine = {str(row["engine_id"]): row for row in engines}
    _require((by_engine["brace_spx"].get("adapter") or {}).get("kind") == "brace_spx_shadow_snapshot", "BRACE adapter missing")
    _require((by_engine["wes"].get("adapter") or {}).get("kind") == "wes_incremental_alpha_snapshot", "WES adapter missing")
    _require((by_engine["gpw_daily"].get("adapter") or {}).get("mode") == "DELEGATED_EXISTING_LOOP", "GPW Daily loop ownership changed")
    _require((by_engine["us_daily"].get("adapter") or {}).get("mode") == "DELEGATED_EXISTING_LOOP", "US Daily loop ownership changed")

    if root is not None:
        public = public_registry.build_registry(root)
        expected = {str(row.get("id")) for row in public.get("experiments", []) if isinstance(row, Mapping)}
        _require(mapped_public == expected, f"learning registry public coverage mismatch: missing={sorted(expected-mapped_public)} extra={sorted(mapped_public-expected)}")

    stored = str(payload.get("registry_sha256") or "")
    _require(stored and stored == registry_hash(payload), "learning engine registry hash mismatch")
    return {
        "ok": True,
        "engines": len(engines),
        "isolated_partitions": len(partitions),
        "public_experiments_mapped": len(mapped_public),
        "zero_authority": True,
    }


def load_learning_registry(path: Path = DEFAULT_REGISTRY, *, root: Path | None = None) -> dict[str, Any]:
    payload = _read_json(path)
    validate_learning_registry(payload, root=root)
    return payload


def _entry_map(registry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["engine_id"]): dict(row) for row in registry.get("engines", []) if isinstance(row, Mapping)}


def _partition(state_root: Path, engine_id: str) -> Path:
    return state_root / engine_id


def _evidence_path(state_root: Path, engine_id: str) -> Path:
    return _partition(state_root, engine_id) / "evidence.jsonl"


def _lesson_path(state_root: Path, engine_id: str) -> Path:
    return _partition(state_root, engine_id) / "lessons.jsonl"


def _source_digest(payload: Any) -> str:
    return _sha(payload)


def _evidence_row(*, engine_id: str, observed_at: str, source: str, source_event_key: str,
                  sample_count: int | float | None, sample_unit: str | None,
                  metrics: Mapping[str, Any], facts: Mapping[str, Any], source_payload: Any) -> dict[str, Any]:
    source_sha = _source_digest(source_payload)
    identity = {"engine_id": engine_id, "source": source, "source_event_key": source_event_key, "source_sha256": source_sha}
    return {
        "evidence_id": _stable_id("levidence", identity),
        "engine_id": engine_id,
        "observed_at": observed_at,
        "source": source,
        "source_event_key": source_event_key,
        "source_sha256": source_sha,
        "sample_count": sample_count,
        "sample_unit": sample_unit,
        "metrics": dict(metrics),
        "facts": dict(facts),
        "formal_decision_authority": False,
        "authority": dict(ZERO_AUTHORITY),
    }


def _collect_brace(root: Path, entry: Mapping[str, Any], now: str) -> list[dict[str, Any]]:
    source = str((entry.get("adapter") or {}).get("source") or "")
    data = _read_json(root / source)
    shadow = data.get("shadow") if isinstance(data.get("shadow"), Mapping) else {}
    development = data.get("development") if isinstance(data.get("development"), Mapping) else {}
    governance = data.get("governance") if isinstance(data.get("governance"), Mapping) else {}
    observed = str(shadow.get("updated_at") or data.get("generated_at") or now)
    sample_count = shadow.get("observations_collected")
    metrics = {
        "observations_collected": sample_count,
        "warmup_required": shadow.get("warmup_required"),
        "observations_remaining": shadow.get("observations_remaining"),
        "strict_gate_passed": 1 if development.get("strict_gate_passed") is True else 0,
        "single_champion_authorized": 1 if development.get("single_champion_authorized") is True else 0,
        "live_orders": 1 if shadow.get("live_orders") is True else 0,
    }
    facts = {
        "generation_id": data.get("generation_id"),
        "shadow_status": shadow.get("status"),
        "latest_market_date": shadow.get("latest_market_date"),
        "candidate_mutation_allowed": governance.get("candidate_mutation_allowed"),
        "orders_allowed": governance.get("orders_allowed"),
        "sealed_holdout_accessed": (data.get("sealed_holdout") or {}).get("accessed") if isinstance(data.get("sealed_holdout"), Mapping) else None,
    }
    event_key = f"{data.get('generation_id')}:{shadow.get('latest_market_date')}:{sample_count}"
    return [_evidence_row(engine_id=str(entry["engine_id"]), observed_at=_iso(observed), source=source,
                          source_event_key=event_key, sample_count=sample_count if isinstance(sample_count, (int, float)) else None,
                          sample_unit="shadow_sessions", metrics=metrics, facts=facts, source_payload=data)]


def _collect_wes(root: Path, entry: Mapping[str, Any], now: str) -> list[dict[str, Any]]:
    source = str((entry.get("adapter") or {}).get("source") or "")
    data = _read_json(root / source)
    overall = data.get("overall") if isinstance(data.get("overall"), Mapping) else {}
    sample = data.get("sample") if isinstance(data.get("sample"), Mapping) else {}
    resolved = overall.get("resolved_pairs")
    metrics = {
        "resolved_pairs": resolved,
        "mean_incremental_alpha_percent": overall.get("mean_incremental_alpha_percent"),
        "median_incremental_alpha_percent": overall.get("median_incremental_alpha_percent"),
        "wes_better_than_v5_rate": overall.get("wes_better_than_v5_rate"),
        "best_incremental_alpha_percent": overall.get("best_incremental_alpha_percent"),
        "worst_incremental_alpha_percent": overall.get("worst_incremental_alpha_percent"),
        "economic_decisions": sample.get("economic_decisions"),
        "effective_samples": sample.get("effective_samples"),
    }
    facts = {
        "sample_status": sample.get("status"),
        "minimum_before_descriptive_analysis": sample.get("minimum_before_descriptive_analysis"),
        "baseline_definition": data.get("baseline_definition"),
        "historical_backfill_allowed": data.get("historical_backfill_allowed"),
        "active_decision_influence": data.get("active_decision_influence"),
        "bounded_influence_enabled": data.get("bounded_influence_enabled"),
    }
    source_sha = _source_digest(data)
    event_key = f"resolved={resolved}:snapshot={source_sha[:16]}"
    return [_evidence_row(engine_id=str(entry["engine_id"]), observed_at=now, source=source,
                          source_event_key=event_key, sample_count=resolved if isinstance(resolved, (int, float)) else None,
                          sample_unit="resolved_counterfactual_pairs", metrics=metrics, facts=facts, source_payload=data)]


def _public_row(root: Path, experiment_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    registry = public_registry.build_registry(root)
    for row in registry.get("experiments", []):
        if isinstance(row, Mapping) and str(row.get("id") or "") == experiment_id:
            return dict(row), registry
    raise ValueError(f"public experiment not found: {experiment_id}")


def _collect_registry_snapshot(root: Path, entry: Mapping[str, Any], now: str) -> list[dict[str, Any]]:
    experiment_id = str(entry.get("public_experiment_id") or "")
    row, registry = _public_row(root, experiment_id)
    metric = row.get("primary_metric") if isinstance(row.get("primary_metric"), Mapping) else {}
    observed = str(row.get("last_updated") or registry.get("generated_at") or now)
    metrics = {
        "sample_count": row.get("sample_count"),
        "primary_metric_value": metric.get("value"),
        "delta_vs_benchmark": row.get("delta_vs_benchmark"),
        "max_drawdown": row.get("max_drawdown"),
    }
    facts = {
        "public_experiment_id": experiment_id,
        "status": row.get("status"),
        "category": row.get("category"),
        "family": row.get("family"),
        "sample_unit": row.get("sample_unit"),
        "primary_metric_label": metric.get("label"),
        "production_impact": row.get("production_impact"),
        "automatic_promotion": row.get("automatic_promotion"),
    }
    event_key = f"{experiment_id}:{_source_digest(row)[:20]}"
    return [_evidence_row(engine_id=str(entry["engine_id"]), observed_at=_iso(observed),
                          source=str((entry.get("adapter") or {}).get("source") or row.get("source") or ""),
                          source_event_key=event_key, sample_count=row.get("sample_count") if isinstance(row.get("sample_count"), (int, float)) else None,
                          sample_unit=str(row.get("sample_unit") or "") or None, metrics=metrics, facts=facts, source_payload=row)]


def collect_engine_evidence(root: Path, entry: Mapping[str, Any], *, now: str) -> list[dict[str, Any]]:
    adapter = entry.get("adapter") if isinstance(entry.get("adapter"), Mapping) else {}
    kind = str(adapter.get("kind") or "")
    if kind == "brace_spx_shadow_snapshot":
        return _collect_brace(root, entry, now)
    if kind == "wes_incremental_alpha_snapshot":
        return _collect_wes(root, entry, now)
    if kind == "experiment_registry_snapshot":
        return _collect_registry_snapshot(root, entry, now)
    if kind in {"daily_policy_shadow", "external_artifact_snapshot"}:
        return []
    raise ValueError(f"unsupported learning adapter: {kind}")


def _ensure_bootstrap_lesson(state_root: Path, entry: Mapping[str, Any], *, now: str) -> int:
    bootstrap = entry.get("bootstrap_lesson")
    if not isinstance(bootstrap, Mapping):
        return 0
    engine_id = str(entry["engine_id"])
    path = _lesson_path(state_root, engine_id)
    existing = _ids(path, "lesson_id")
    lesson_id = str(bootstrap.get("lesson_id") or "")
    if not lesson_id or lesson_id in existing:
        return 0
    payload = {
        "lesson_id": lesson_id,
        "engine_id": engine_id,
        "derived_at": now[:10],
        "status": "OBSERVED",
        "statement": str(bootstrap.get("statement") or ""),
        "evidence": [dict(x) for x in bootstrap.get("evidence") or [] if isinstance(x, Mapping)],
        "hypothesis_input_ready": True,
        "production_authority": False,
        "authority": dict(ZERO_AUTHORITY),
        "created_at": now,
    }
    _append_chained(path, schema=LESSON_SCHEMA, id_field="lesson_id", payload=payload)
    return 1


def _phase_path(state_root: Path, engine_id: str, phase: str) -> Path:
    names = {
        "hypotheses": "hypotheses.jsonl",
        "experiments": "experiments.jsonl",
        "validation_epochs": "validation_epochs.jsonl",
        "results": "results.jsonl",
    }
    if phase not in names:
        raise ValueError(f"unsupported local phase ledger: {phase}")
    return _partition(state_root, engine_id) / names[phase]


def record_local_hypothesis(state_root: Path, *, engine_id: str, lesson_ids: Sequence[str], claim: str,
                            experiment_family: str, success_criteria: Sequence[Mapping[str, Any]],
                            falsification_criteria: Sequence[Mapping[str, Any]], created_at: str) -> dict[str, Any]:
    if not lesson_ids:
        raise ValueError("local hypothesis requires at least one lesson input")
    if not claim.strip():
        raise ValueError("local hypothesis claim is required")
    hypothesis_id = _stable_id("lhyp", {
        "engine_id": engine_id,
        "lesson_ids": sorted(str(x) for x in lesson_ids),
        "claim": claim,
        "experiment_family": experiment_family,
        "success_criteria": [dict(x) for x in success_criteria],
        "falsification_criteria": [dict(x) for x in falsification_criteria],
    })
    path = _phase_path(state_root, engine_id, "hypotheses")
    if hypothesis_id in _ids(path, "hypothesis_id"):
        return next(row for row in _read_jsonl(path) if row.get("hypothesis_id") == hypothesis_id)
    payload = {
        "hypothesis_id": hypothesis_id,
        "engine_id": engine_id,
        "lesson_ids": sorted(str(x) for x in lesson_ids),
        "claim": claim,
        "experiment_family": experiment_family,
        "success_criteria": [dict(x) for x in success_criteria],
        "falsification_criteria": [dict(x) for x in falsification_criteria],
        "status": "READY_FOR_LOCAL_EXPERIMENT",
        "created_at": created_at,
        "cross_engine_inputs_have_direct_authority": False,
        "authority": dict(ZERO_AUTHORITY),
    }
    return _append_chained(path, schema=HYPOTHESIS_SCHEMA, id_field="hypothesis_id", payload=payload)


def record_local_experiment(state_root: Path, *, engine_id: str, hypothesis_id: str,
                            contract: Mapping[str, Any], created_at: str) -> dict[str, Any]:
    hypotheses = _ids(_phase_path(state_root, engine_id, "hypotheses"), "hypothesis_id")
    if hypothesis_id not in hypotheses:
        raise ValueError("local experiment references unknown engine-local hypothesis")
    if contract.get("prospective_only") is not True:
        raise ValueError("local experiment must be prospective_only")
    if contract.get("historical_backfill") is not False:
        raise ValueError("local experiment must forbid historical backfill")
    experiment_id = _stable_id("lexp", {"engine_id": engine_id, "hypothesis_id": hypothesis_id, "contract": dict(contract)})
    path = _phase_path(state_root, engine_id, "experiments")
    if experiment_id in _ids(path, "experiment_id"):
        return next(row for row in _read_jsonl(path) if row.get("experiment_id") == experiment_id)
    payload = {
        "experiment_id": experiment_id,
        "engine_id": engine_id,
        "hypothesis_id": hypothesis_id,
        "contract": dict(contract),
        "status": "COMPILED",
        "created_at": created_at,
        "production_impact": False,
        "automatic_promotion": False,
        "authority": dict(ZERO_AUTHORITY),
    }
    return _append_chained(path, schema=EXPERIMENT_SCHEMA, id_field="experiment_id", payload=payload)


def commit_local_validation_epoch(state_root: Path, *, engine_id: str, experiment_id: str,
                                  committed_at: str) -> dict[str, Any]:
    experiments = _ids(_phase_path(state_root, engine_id, "experiments"), "experiment_id")
    if experiment_id not in experiments:
        raise ValueError("ValidationEpoch references unknown engine-local experiment")
    path = _phase_path(state_root, engine_id, "validation_epochs")
    for row in _read_jsonl(path):
        if row.get("experiment_id") == experiment_id:
            return row
    evidence = _verify_chain(_evidence_path(state_root, engine_id), schema=EVIDENCE_SCHEMA, id_field="evidence_id")
    epoch_id = _stable_id("lepoch", {
        "engine_id": engine_id,
        "experiment_id": experiment_id,
        "committed_at": committed_at,
        "evidence_head_hash": evidence["head_hash"],
        "evidence_events_before": evidence["events"],
    })
    payload = {
        "epoch_id": epoch_id,
        "engine_id": engine_id,
        "experiment_id": experiment_id,
        "committed_at": committed_at,
        "evidence_boundary": {
            "evidence_events_before": evidence["events"],
            "evidence_head_hash_before": evidence["head_hash"],
            "strict_observed_at_after": committed_at,
            "older_evidence_formally_eligible": False,
        },
        "eligibility_rule": {
            "prospective_only": True,
            "historical_backfill": False,
            "same_sample_train_and_validate": False,
        },
        "authority": dict(ZERO_AUTHORITY),
    }
    return _append_chained(path, schema=EPOCH_SCHEMA, id_field="epoch_id", payload=payload)


def record_local_result(state_root: Path, *, engine_id: str, experiment_id: str, epoch_id: str,
                        verdict: str, metrics: Mapping[str, Any], completed_at: str) -> dict[str, Any]:
    if verdict not in {"SUPPORTED", "REJECTED", "INCONCLUSIVE"}:
        raise ValueError("unsupported local result verdict")
    experiments = _ids(_phase_path(state_root, engine_id, "experiments"), "experiment_id")
    epochs = _ids(_phase_path(state_root, engine_id, "validation_epochs"), "epoch_id")
    if experiment_id not in experiments or epoch_id not in epochs:
        raise ValueError("local result lineage is incomplete")
    result_id = _stable_id("lresult", {
        "engine_id": engine_id,
        "experiment_id": experiment_id,
        "epoch_id": epoch_id,
        "metrics": dict(metrics),
        "verdict": verdict,
    })
    path = _phase_path(state_root, engine_id, "results")
    if result_id in _ids(path, "result_id"):
        return next(row for row in _read_jsonl(path) if row.get("result_id") == result_id)
    payload = {
        "result_id": result_id,
        "engine_id": engine_id,
        "experiment_id": experiment_id,
        "epoch_id": epoch_id,
        "verdict": verdict,
        "metrics": dict(metrics),
        "completed_at": completed_at,
        "formal_decision_authority": False,
        "authority": dict(ZERO_AUTHORITY),
    }
    return _append_chained(path, schema=RESULT_SCHEMA, id_field="result_id", payload=payload)


def derive_local_lesson_from_result(state_root: Path, *, engine_id: str, result: Mapping[str, Any],
                                    statement: str) -> dict[str, Any]:
    if str(result.get("engine_id") or "") != engine_id:
        raise ValueError("result belongs to another engine")
    result_id = str(result.get("result_id") or "")
    if not result_id:
        raise ValueError("result_id missing")
    path = _lesson_path(state_root, engine_id)
    lesson_id = _stable_id("llesson", {"engine_id": engine_id, "result_id": result_id, "statement": statement})
    if lesson_id in _ids(path, "lesson_id"):
        return next(row for row in _read_jsonl(path) if row.get("lesson_id") == lesson_id)
    payload = {
        "lesson_id": lesson_id,
        "engine_id": engine_id,
        "derived_at": str(result.get("completed_at") or "")[:10],
        "status": "OBSERVED",
        "statement": statement,
        "evidence": [{
            "kind": "engine_local_result",
            "source": f"local:results.jsonl#{result_id}",
            "detail": f"experiment_id={result.get('experiment_id')}; epoch_id={result.get('epoch_id')}; verdict={result.get('verdict')}",
        }],
        "lineage": {
            "source_result_id": result_id,
            "source_experiment_id": result.get("experiment_id"),
            "source_epoch_id": result.get("epoch_id"),
        },
        "hypothesis_input_ready": True,
        "production_authority": False,
        "authority": dict(ZERO_AUTHORITY),
        "created_at": result.get("completed_at"),
    }
    return _append_chained(path, schema=LESSON_SCHEMA, id_field="lesson_id", payload=payload)


def run_engine(root: Path, state_root: Path, entry: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    engine_id = str(entry["engine_id"])
    partition = _partition(state_root, engine_id)
    partition.mkdir(parents=True, exist_ok=True)
    created_lessons = _ensure_bootstrap_lesson(state_root, entry, now=now)
    adapter = entry.get("adapter") if isinstance(entry.get("adapter"), Mapping) else {}
    delegated = adapter.get("mode") == "DELEGATED_EXISTING_LOOP"
    evidence_path = _evidence_path(state_root, engine_id)
    _verify_chain(evidence_path, schema=EVIDENCE_SCHEMA, id_field="evidence_id")
    before_ids = _ids(evidence_path, "evidence_id")
    created_evidence = 0
    if not delegated:
        for evidence in collect_engine_evidence(root, entry, now=now):
            evidence_id = str(evidence["evidence_id"])
            if evidence_id in before_ids:
                continue
            _append_chained(evidence_path, schema=EVIDENCE_SCHEMA, id_field="evidence_id", payload=evidence)
            before_ids.add(evidence_id)
            created_evidence += 1
    evidence_state = _verify_chain(evidence_path, schema=EVIDENCE_SCHEMA, id_field="evidence_id")
    lesson_state = _verify_chain(_lesson_path(state_root, engine_id), schema=LESSON_SCHEMA, id_field="lesson_id")
    if delegated:
        phase = "DELEGATED_EXISTING_LOOP"
        next_action = "CONTINUE_EXISTING_ENGINE_LOCAL_DAILY_LOOP"
    elif adapter.get("kind") == "external_artifact_snapshot" and evidence_state["events"] == 0:
        phase = "AWAITING_EXTERNAL_EVIDENCE"
        next_action = "RESTORE_ENGINE_LOCAL_ARTIFACT_BEFORE_LOCAL_HYPOTHESIS"
    elif lesson_state["events"] > 0:
        phase = "READY_FOR_LOCAL_HYPOTHESIS"
        next_action = "COMPILE_ENGINE_LOCAL_HYPOTHESIS_ONLY"
    else:
        phase = "COLLECTING_EVIDENCE"
        next_action = "DERIVE_ENGINE_LOCAL_LESSON_BEFORE_HYPOTHESIS"
    state = {
        "schema_version": LOOP_STATE_SCHEMA,
        "engine_id": engine_id,
        "updated_at": now,
        "runtime_owner": entry.get("runtime_owner"),
        "state_partition": entry.get("state_partition"),
        "phase": phase,
        "next_action": next_action,
        "lifecycle": list(LIFECYCLE),
        "evidence_events": evidence_state["events"],
        "lesson_events": lesson_state["events"],
        "evidence_created": created_evidence,
        "lessons_created": created_lessons,
        "knowledge_policy": dict(entry.get("knowledge_exchange") or {}),
        "authority": dict(ZERO_AUTHORITY),
    }
    body = dict(state)
    body["state_sha256"] = _sha(state)
    _write_json(partition / LOOP_STATE_FILENAME, body)
    return body


def sync_knowledge_bus(state_root: Path, registry: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    path = state_root / KNOWLEDGE_BUS_FILENAME
    _verify_chain(path, schema=KNOWLEDGE_SCHEMA, id_field="knowledge_id")
    existing = _ids(path, "knowledge_id")
    created = 0
    for entry in registry.get("engines", []):
        if not isinstance(entry, Mapping):
            continue
        engine_id = str(entry.get("engine_id") or "")
        for lesson in _read_jsonl(_lesson_path(state_root, engine_id)):
            lesson_id = str(lesson.get("lesson_id") or "")
            knowledge_id = _stable_id("knowledge", {
                "source_engine_id": engine_id,
                "lesson_id": lesson_id,
                "lesson_row_sha256": lesson.get("row_sha256"),
            })
            if knowledge_id in existing:
                continue
            payload = {
                "knowledge_id": knowledge_id,
                "source_engine_id": engine_id,
                "source_lesson_id": lesson_id,
                "published_at": now,
                "statement": lesson.get("statement"),
                "source_lesson_sha256": lesson.get("row_sha256"),
                "recipient_use": "HYPOTHESIS_INPUT_ONLY",
                "requires_recipient_local_experiment": True,
                "automatic_cross_engine_writeback": False,
                "authority": dict(ZERO_AUTHORITY),
            }
            _append_chained(path, schema=KNOWLEDGE_SCHEMA, id_field="knowledge_id", payload=payload)
            existing.add(knowledge_id)
            created += 1
    chain = _verify_chain(path, schema=KNOWLEDGE_SCHEMA, id_field="knowledge_id")
    return {"ok": True, "created": created, "events": chain["events"], "head_hash": chain["head_hash"]}


def write_hypothesis_inputs(state_root: Path, registry: Mapping[str, Any], *, now: str) -> None:
    bus = _read_jsonl(state_root / KNOWLEDGE_BUS_FILENAME)
    for entry in registry.get("engines", []):
        if not isinstance(entry, Mapping):
            continue
        engine_id = str(entry.get("engine_id") or "")
        local_lessons = _read_jsonl(_lesson_path(state_root, engine_id))
        external = [{
            "knowledge_id": row.get("knowledge_id"),
            "source_engine_id": row.get("source_engine_id"),
            "source_lesson_id": row.get("source_lesson_id"),
            "statement": row.get("statement"),
            "validated_by_recipient": False,
            "allowed_use": "HYPOTHESIS_INPUT_ONLY",
        } for row in bus if str(row.get("source_engine_id") or "") != engine_id]
        payload = {
            "schema_version": HYPOTHESIS_INPUT_SCHEMA,
            "engine_id": engine_id,
            "updated_at": now,
            "local_lessons": [{
                "lesson_id": row.get("lesson_id"),
                "statement": row.get("statement"),
                "hypothesis_input_ready": row.get("hypothesis_input_ready"),
            } for row in local_lessons],
            "external_lessons": external,
            "rules": {
                "hypothesis_generation_scope": "ENGINE_LOCAL_ONLY",
                "external_lesson_requires_local_hypothesis": True,
                "external_lesson_requires_local_experiment": True,
                "external_lesson_has_direct_policy_authority": False,
                "automatic_cross_engine_writeback": False,
            },
            "authority": dict(ZERO_AUTHORITY),
        }
        payload["bundle_sha256"] = _sha(payload)
        _write_json(_partition(state_root, engine_id) / HYPOTHESIS_INPUT_FILENAME, payload)


def build_meta_observatory(root: Path, state_root: Path, registry: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    public = public_registry.build_registry(root)
    rows: list[dict[str, Any]] = []
    for entry in registry.get("engines", []):
        if not isinstance(entry, Mapping):
            continue
        engine_id = str(entry.get("engine_id") or "")
        state_path = _partition(state_root, engine_id) / LOOP_STATE_FILENAME
        state = _read_json(state_path) if state_path.exists() else {}
        rows.append({
            "engine_id": engine_id,
            "runtime_owner": entry.get("runtime_owner"),
            "phase": state.get("phase") or entry.get("loop_phase"),
            "state_partition": entry.get("state_partition"),
            "evidence_events": state.get("evidence_events", 0),
            "lesson_events": state.get("lesson_events", 0),
            "public_experiment_id": entry.get("public_experiment_id"),
        })
    expected_public = {str(row.get("id")) for row in public.get("experiments", []) if isinstance(row, Mapping)}
    mapped_public = {str(row.get("public_experiment_id")) for row in registry.get("engines", []) if isinstance(row, Mapping) and row.get("public_experiment_id")}
    bus_state = _verify_chain(state_root / KNOWLEDGE_BUS_FILENAME, schema=KNOWLEDGE_SCHEMA, id_field="knowledge_id")
    payload = {
        "schema_version": OBSERVATORY_SCHEMA,
        "updated_at": now,
        "purpose": "Read-only observability over isolated engine learning loops; never a central learning controller.",
        "engines": rows,
        "coverage": {
            "engine_loops_total": len(rows),
            "public_experiments_total": len(expected_public),
            "public_experiments_mapped": len(mapped_public),
            "unmapped_public_experiments": sorted(expected_public - mapped_public),
            "unique_state_partitions": len({str(row.get("state_partition")) for row in rows}),
        },
        "knowledge_bus_events": bus_state["events"],
        "authority": {
            **ZERO_AUTHORITY,
            "decision_authority": False,
            "engine_state_writeback": False,
            "engine_configuration_writeback": False,
        },
    }
    payload["observatory_sha256"] = _sha(payload)
    _write_json(state_root / OBSERVATORY_FILENAME, payload)
    return payload


def run_all(root: Path, state_root: Path, registry: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    validate_learning_registry(registry, root=root)
    states = [run_engine(root, state_root, entry, now=now) for entry in registry.get("engines", []) if isinstance(entry, Mapping)]
    bus = sync_knowledge_bus(state_root, registry, now=now)
    write_hypothesis_inputs(state_root, registry, now=now)
    observatory = build_meta_observatory(root, state_root, registry, now=now)
    return {
        "engines": len(states),
        "delegated_existing_loops": sum(row.get("phase") == "DELEGATED_EXISTING_LOOP" for row in states),
        "engine_local_loops": sum(row.get("phase") != "DELEGATED_EXISTING_LOOP" for row in states),
        "knowledge_bus_events": bus["events"],
        "public_experiments_mapped": observatory["coverage"]["public_experiments_mapped"],
        "authority": dict(ZERO_AUTHORITY),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run/validate engine-specific BriefRooms learning loops")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--engine")
    parser.add_argument("--run-all", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--now")
    args = parser.parse_args()
    registry_path = args.registry if args.registry.is_absolute() else args.root / args.registry
    registry = load_learning_registry(registry_path, root=args.root)
    if args.validate or (not args.run_all and not args.engine):
        print(json.dumps(validate_learning_registry(registry, root=args.root), ensure_ascii=False, sort_keys=True))
        return 0
    if args.state_root is None:
        raise SystemExit("--state-root is required when running engine loops")
    now = _iso(args.now) if args.now else _now_iso()
    if args.engine:
        entry = _entry_map(registry).get(args.engine)
        if entry is None:
            raise SystemExit(f"unknown engine: {args.engine}")
        print(json.dumps(run_engine(args.root, args.state_root, entry, now=now), ensure_ascii=False, sort_keys=True))
        return 0
    print(json.dumps(run_all(args.root, args.state_root, registry, now=now), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
