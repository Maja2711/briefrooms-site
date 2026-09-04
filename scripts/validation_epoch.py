#!/usr/bin/env python3
"""Hash-committed ValidationEpoch ledger for autonomous policy research.

The autonomous-policy workflow consumes the main Learning Ledger read-only.
Validation epochs therefore live in the durable autonomous-policy artifact and
have their own append-only hash chain. An epoch commits the candidate,
eligibility rule and inference plan before any observation can belong to that
validation stage.

Zero authority: this module cannot promote policies, alter engine configs or
execute trades.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

SCHEMA_VERSION = "briefrooms-validation-epoch-ledger-v1"
EVENT_TYPE = "validation_epoch_committed"
LEDGER_FILENAME = "validation_epoch_ledger.jsonl"
STAGES = {"PR35", "PR36"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("timestamp is required")
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
            raise ValueError(f"validation epoch row {line_no} is not an object")
        rows.append(row)
    return rows


def _append_line(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (_canonical(payload) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def candidate_definition(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Only immutable research-candidate semantics belong in this hash."""
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "engine_id": str(candidate.get("engine_id") or ""),
        "parameter": str(candidate.get("parameter") or ""),
        "gate": str(candidate.get("gate") or ""),
        "from_value": candidate.get("from_value"),
        "to_value": candidate.get("to_value"),
        "promotion_methodology_version": int(candidate.get("promotion_methodology_version") or 0),
        "validation_target_n": int(candidate.get("validation_target_n") or 0),
    }


def eligibility_rule(candidate: Mapping[str, Any], stage: str) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError(f"unsupported validation stage: {stage}")
    low = min(float(candidate["from_value"]), float(candidate["to_value"]))
    high = max(float(candidate["from_value"]), float(candidate["to_value"]))
    rule: dict[str, Any] = {
        "stage": stage,
        "engine_id": str(candidate["engine_id"]),
        "first_blocking_gate": str(candidate["gate"]),
        "candidate_score_interval": {"lower_inclusive": low, "upper_exclusive": high},
        "source_threshold_must_equal_from_value": float(candidate["from_value"]),
        "other_hard_gates_passed": True,
        "decision_time_rule": "strictly_after_epoch_commit",
        "prospective_only": True,
    }
    if stage == "PR36":
        rule["pr35_sample_reuse_allowed"] = False
        rule["comparison"] = "champion_FLAT_vs_challenger_LONG_on_same_marginal_cases"
    return rule


def shadow_set_digest(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    frozen = sorted(
        (
            str(row.get("shadow_outcome_id") or ""),
            str(row.get("row_sha256") or ""),
            str(row.get("decision_at") or ""),
        )
        for row in rows
    )
    return {
        "existing_shadow_count": len(frozen),
        "existing_shadow_digest": _sha(frozen),
    }


def verify_chain(path: Path) -> dict[str, Any]:
    rows = _read_jsonl(path)
    previous: Optional[str] = None
    seen_events: set[str] = set()
    seen_epochs: set[str] = set()
    for index, row in enumerate(rows):
        if row.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"validation epoch schema mismatch at {index}")
        if row.get("event_type") != EVENT_TYPE:
            raise ValueError(f"validation epoch event type mismatch at {index}")
        event_id = str(row.get("event_id") or "")
        epoch_id = str(row.get("epoch_id") or "")
        if not event_id or event_id in seen_events:
            raise ValueError(f"duplicate/empty validation epoch event id at {index}")
        if not epoch_id or epoch_id in seen_epochs:
            raise ValueError(f"duplicate/empty validation epoch id at {index}")
        if row.get("previous_hash") != previous:
            raise ValueError(f"validation epoch chain break at {index}")
        body = dict(row)
        stored_hash = str(body.pop("event_hash", "") or "")
        if not stored_hash or stored_hash != _sha(body):
            raise ValueError(f"validation epoch hash mismatch at {index}")
        definition = row.get("candidate_definition")
        rule = row.get("eligibility_rule")
        if not isinstance(definition, Mapping) or row.get("candidate_definition_hash") != _sha(definition):
            raise ValueError(f"candidate definition hash mismatch at {index}")
        if not isinstance(rule, Mapping) or row.get("eligibility_rule_hash") != _sha(rule):
            raise ValueError(f"eligibility rule hash mismatch at {index}")
        _parse_time(str(row.get("committed_at") or ""))
        if str(row.get("stage") or "") not in STAGES:
            raise ValueError(f"invalid validation epoch stage at {index}")
        previous = stored_hash
        seen_events.add(event_id)
        seen_epochs.add(epoch_id)
    return {"ok": True, "events": len(rows), "head_hash": previous}


def find_epoch(path: Path, epoch_id: str) -> dict[str, Any] | None:
    verify_chain(path)
    for row in _read_jsonl(path):
        if row.get("epoch_id") == epoch_id:
            return row
    return None


def commit_epoch(
    state_dir: Path,
    candidate: Mapping[str, Any],
    *,
    stage: str,
    committed_at: str | datetime,
    primary_inference_plan: Mapping[str, Any],
    shadow_anytime_plan: Mapping[str, Any],
    shadow_rows: Sequence[Mapping[str, Any]] = (),
    supersedes_epoch_id: str | None = None,
) -> dict[str, Any]:
    """Commit a prospective validation stage and return a registry-safe reference."""
    if stage not in STAGES:
        raise ValueError(f"unsupported validation stage: {stage}")
    definition = candidate_definition(candidate)
    if not all(definition.get(key) not in ("", None) for key in ("candidate_id", "engine_id", "parameter", "gate")):
        raise ValueError("candidate is missing immutable definition fields")
    path = state_dir / LEDGER_FILENAME
    chain = verify_chain(path)
    rule = eligibility_rule(candidate, stage)
    commit_iso = _iso(committed_at)
    boundary = {
        **shadow_set_digest(shadow_rows),
        "strict_decision_at_after": commit_iso,
        "older_decisions_formally_eligible": False,
    }
    identity = {
        "candidate_id": definition["candidate_id"],
        "stage": stage,
        "committed_at": commit_iso,
        "candidate_definition_hash": _sha(definition),
        "eligibility_rule_hash": _sha(rule),
        "supersedes_epoch_id": supersedes_epoch_id,
    }
    epoch_id = "vepoch-" + _sha(identity)[:24]
    existing = find_epoch(path, epoch_id)
    if existing is not None:
        return epoch_reference(existing)

    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_TYPE,
        "event_id": "vevt-" + _sha({"epoch_id": epoch_id, "previous_hash": chain["head_hash"]})[:24],
        "epoch_id": epoch_id,
        "candidate_id": definition["candidate_id"],
        "stage": stage,
        "committed_at": commit_iso,
        "candidate_definition": definition,
        "candidate_definition_hash": _sha(definition),
        "eligibility_rule": rule,
        "eligibility_rule_hash": _sha(rule),
        "primary_inference_plan": dict(primary_inference_plan),
        "shadow_anytime_plan": dict(shadow_anytime_plan),
        "evidence_boundary": boundary,
        "supersedes_epoch_id": supersedes_epoch_id,
        "previous_hash": chain["head_hash"],
    }
    event["event_hash"] = _sha(event)
    _append_line(path, event)
    verify_chain(path)
    return epoch_reference(event)


def epoch_reference(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "epoch_id": str(event["epoch_id"]),
        "event_id": str(event["event_id"]),
        "event_hash": str(event["event_hash"]),
        "stage": str(event["stage"]),
        "committed_at": str(event["committed_at"]),
        "candidate_definition_hash": str(event["candidate_definition_hash"]),
        "eligibility_rule_hash": str(event["eligibility_rule_hash"]),
        "evidence_boundary": dict(event.get("evidence_boundary") or {}),
    }


def verify_epoch_reference(
    state_dir: Path,
    candidate: Mapping[str, Any],
    *,
    stage: str,
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    path = state_dir / LEDGER_FILENAME
    verify_chain(path)
    epoch_id = str(reference.get("epoch_id") or "")
    event = find_epoch(path, epoch_id)
    if event is None:
        raise ValueError(f"missing validation epoch: {epoch_id}")
    if str(event.get("stage")) != stage:
        raise ValueError("validation epoch stage mismatch")
    if str(event.get("candidate_id")) != str(candidate.get("candidate_id")):
        raise ValueError("validation epoch candidate mismatch")
    if reference.get("event_hash") != event.get("event_hash"):
        raise ValueError("validation epoch reference hash mismatch")
    current_definition = candidate_definition(candidate)
    if event.get("candidate_definition_hash") != _sha(current_definition):
        raise ValueError("candidate definition changed after ValidationEpoch commit")
    committed_at = str(event.get("committed_at") or "")
    if (event.get("evidence_boundary") or {}).get("strict_decision_at_after") != committed_at:
        raise ValueError("validation epoch evidence boundary mismatch")
    return event


def eligible_after(event: Mapping[str, Any]) -> datetime:
    return _parse_time(str((event.get("evidence_boundary") or {}).get("strict_decision_at_after") or event["committed_at"]))


def safety_controls() -> dict[str, bool]:
    return {
        "production_promotion": False,
        "engine_policy_writeback": False,
        "ranking_writeback": False,
        "sizing_writeback": False,
        "trade_execution": False,
        "learning_ledger_writeback": False,
    }
