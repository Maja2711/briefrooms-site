#!/usr/bin/env python3
"""Prospective runtime adapter for PR32B canonical epistemic verification.

The builder creates immutable verification targets from the current PR32A
CanonicalEpistemicState and optionally resolves explicit outcome records. It
never fabricates historical targets and never writes back into Belief Core.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from canonical_epistemic_verification import (
        CanonicalEpistemicVerificationError,
        EpistemicVerificationTarget,
        build_targets,
        resolve_target,
        target_from_dict,
        verification_from_dict,
    )
except ModuleNotFoundError:
    from scripts.canonical_epistemic_verification import (
        CanonicalEpistemicVerificationError,
        EpistemicVerificationTarget,
        build_targets,
        resolve_target,
        target_from_dict,
        verification_from_dict,
    )

CANONICAL_STATE_FILENAME = "canonical_epistemic_state.json"
TARGETS_FILENAME = "epistemic_verification_targets.jsonl"
OUTCOMES_FILENAME = "epistemic_outcomes.jsonl"
VERIFICATIONS_FILENAME = "canonical_epistemic_verifications.jsonl"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CanonicalEpistemicVerificationError(f"required file missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CanonicalEpistemicVerificationError(f"expected JSON object: {path}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CanonicalEpistemicVerificationError(f"invalid JSONL at {path}:{line_no}") from exc
        if not isinstance(row, dict):
            raise CanonicalEpistemicVerificationError(f"expected JSON object at {path}:{line_no}")
        rows.append(row)
    return rows


def _atomic_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _merge_immutable(existing: list[dict[str, Any]], new_rows: Iterable[Mapping[str, Any]], *, id_field: str,
                     validator) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for raw in existing:
        obj = validator(raw)
        row = obj.to_dict()
        row_id = str(row[id_field])
        if row_id in by_id and by_id[row_id] != row:
            raise CanonicalEpistemicVerificationError(f"conflicting immutable {id_field}: {row_id}")
        if row_id not in by_id:
            by_id[row_id] = row
            ordered.append(row)
    pending: list[dict[str, Any]] = []
    for raw in new_rows:
        row = dict(raw)
        obj = validator(row)
        row = obj.to_dict()
        row_id = str(row[id_field])
        if row_id in by_id:
            if by_id[row_id] != row:
                raise CanonicalEpistemicVerificationError(f"conflicting immutable {id_field}: {row_id}")
            continue
        by_id[row_id] = row
        pending.append(row)
    pending.sort(key=lambda row: str(row[id_field]))
    return ordered + pending


def _outcome_by_target(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        target_id = str(row.get("target_id") or "").strip()
        if not target_id:
            raise CanonicalEpistemicVerificationError("outcome target_id is required")
        required = ("outcome", "verified_at", "outcome_source")
        missing = [key for key in required if key not in row or row.get(key) is None]
        if missing:
            raise CanonicalEpistemicVerificationError(f"outcome {target_id} missing fields: {','.join(missing)}")
        normalized = {
            "target_id": target_id,
            "outcome": bool(row["outcome"]),
            "verified_at": str(row["verified_at"]),
            "outcome_source": str(row["outcome_source"]),
            "outcome_ref": row.get("outcome_ref"),
            "note": str(row.get("note") or ""),
        }
        if target_id in out and out[target_id] != normalized:
            raise CanonicalEpistemicVerificationError(f"conflicting outcome for target: {target_id}")
        out[target_id] = normalized
    return out


def build_runtime(state_dir: Path) -> dict[str, Any]:
    canonical_state = _read_json(state_dir / CANONICAL_STATE_FILENAME)

    target_path = state_dir / TARGETS_FILENAME
    existing_targets = _read_jsonl(target_path)
    generated_targets = [target.to_dict() for target in build_targets(canonical_state)]
    target_rows = _merge_immutable(existing_targets, generated_targets, id_field="target_id", validator=target_from_dict)
    _atomic_jsonl(target_path, target_rows)

    targets: dict[str, EpistemicVerificationTarget] = {}
    for row in target_rows:
        target = target_from_dict(row)
        targets[target.target_id] = target

    outcome_rows = _read_jsonl(state_dir / OUTCOMES_FILENAME)
    outcomes = _outcome_by_target(outcome_rows)
    resolved = []
    for target_id, outcome in sorted(outcomes.items()):
        target = targets.get(target_id)
        if target is None:
            # Prospective-only: never fabricate a target from an outcome.
            raise CanonicalEpistemicVerificationError(f"outcome references unknown prospective target: {target_id}")
        resolved.append(resolve_target(
            target,
            outcome=outcome["outcome"],
            verified_at=outcome["verified_at"],
            outcome_source=outcome["outcome_source"],
            outcome_ref=outcome.get("outcome_ref"),
            note=outcome.get("note") or "",
        ).to_dict())

    verification_path = state_dir / VERIFICATIONS_FILENAME
    existing_verifications = _read_jsonl(verification_path)
    verification_rows = _merge_immutable(
        existing_verifications,
        resolved,
        id_field="verification_id",
        validator=verification_from_dict,
    )
    _atomic_jsonl(verification_path, verification_rows)

    return {
        "targets_total": len(target_rows),
        "targets_generated_from_current_state": len(generated_targets),
        "explicit_outcomes_seen": len(outcomes),
        "verifications_total": len(verification_rows),
        "mode": "measurement_only",
        "prospective_only": True,
        "belief_core_writeback_enabled": False,
        "automatic_tuning_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build PR32B canonical verification targets/outcomes")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    summary = build_runtime(Path(args.state_dir))
    if args.print_summary:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
