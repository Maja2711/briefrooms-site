#!/usr/bin/env python3
"""PR #19.1 semantic hardening wrapper for PR19 epistemic/causal graph.

New PR19 graph snapshots exclude Entity Beliefs that PR15 marks as currently
semantically ineligible or append-only deprecated. Old graph snapshots and old
forecast↔epistemic bindings are never rewritten or deleted.

Resolved historical mismatches receive an additional append-only PR19 migration
record that points back to the PR15 semantic deprecation and lists historical
graph snapshots in which the Belief existed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence

try:
    import belief_epistemic_causal_graph as base
    from entity_semantic_eligibility import CONTRACT_VERSION as SEMANTIC_CONTRACT_VERSION
except ModuleNotFoundError:
    from scripts import belief_epistemic_causal_graph as base
    from scripts.entity_semantic_eligibility import CONTRACT_VERSION as SEMANTIC_CONTRACT_VERSION

MIGRATION_CONTRACT_VERSION = "entity-semantic-pr19-graph-migration-v1"
_ORIGINAL_ENTITY_IDS = base._entity_belief_ids
_EXCLUDED: set[str] = set()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _excluded(report: Mapping[str, Any]) -> set[str]:
    return {
        *[str(row.get("belief_id") or "") for row in report.get("semantic_deprecations") or [] if isinstance(row, Mapping) and row.get("belief_id")],
        *[str(x) for x in ((report.get("semantic_eligibility") or {}).get("current_semantic_ineligible_belief_ids") or [])],
    }


def _entity_ids_filtered(report: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(x for x in _ORIGINAL_ENTITY_IDS(report) if x not in _EXCLUDED)


def _historical_snapshot_ids(state: Mapping[str, Any], belief_id: str) -> list[str]:
    rows = []
    for snapshot_id, snapshot in (state.get("graph_snapshots") or {}).items():
        if not isinstance(snapshot, Mapping):
            continue
        if belief_id in (snapshot.get("contract_index") or {}):
            rows.append(str(snapshot_id))
    return sorted(rows)


def _append_graph_migrations(
    state: MutableMapping[str, Any],
    pr15_report: Mapping[str, Any],
    *,
    observed_at: str,
) -> None:
    previous = deepcopy(dict(state.get("semantic_contract_deprecations") or {}))
    target: MutableMapping[str, Any] = state.setdefault("semantic_contract_deprecations", {})
    for source in pr15_report.get("semantic_deprecations") or []:
        if not isinstance(source, Mapping):
            continue
        belief_id = str(source.get("belief_id") or "")
        if not belief_id or belief_id in target:
            continue
        record = {
            "migration_contract_version": MIGRATION_CONTRACT_VERSION,
            "semantic_eligibility_contract_version": SEMANTIC_CONTRACT_VERSION,
            "belief_id": belief_id,
            "status": "DEPRECATED_SEMANTIC_MISMATCH",
            "observed_at": observed_at,
            "source_pr15_deprecation_sha256": source.get("immutable_sha256"),
            "source_pr15_effective_at": source.get("effective_at"),
            "entity_id": source.get("entity_id"),
            "dimension": source.get("dimension"),
            "entity_archetype": source.get("entity_archetype"),
            "exposure_key": source.get("exposure_key"),
            "historical_graph_snapshot_ids": _historical_snapshot_ids(state, belief_id),
            "historical_graph_snapshots_preserved": True,
            "historical_forecast_bindings_preserved": True,
            "active_membership_in_new_graph": False,
            "retroactive_graph_rewrite": False,
            "historical_deletion": False,
        }
        record["immutable_sha256"] = _sha(record)
        target[belief_id] = record

    for belief_id, record in previous.items():
        if belief_id not in target or target[belief_id] != record:
            raise RuntimeError(f"PR19.1 PR19 append-only semantic migration mutation: {belief_id}")


def run(
    state_dir: Path,
    *,
    pr15_report_path: Path,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_z = base.iso_z(now)
    pr15 = _read_json(pr15_report_path, {})
    excluded = _excluded(pr15)
    global _EXCLUDED
    _EXCLUDED = excluded
    original = base._entity_belief_ids
    base._entity_belief_ids = _entity_ids_filtered
    try:
        report = base.run(state_dir, pr15_report_path=pr15_report_path, as_of=now)
    finally:
        base._entity_belief_ids = original
        _EXCLUDED = set()

    state_dir = Path(state_dir)
    state_path = state_dir / base.STATE_FILENAME
    state = _read_json(state_path, {})
    _append_graph_migrations(state, pr15, observed_at=now_z)
    state["semantic_eligibility_contract_version"] = SEMANTIC_CONTRACT_VERSION
    state["semantic_graph_migration_contract_version"] = MIGRATION_CONTRACT_VERSION
    state["current_semantic_ineligible_belief_ids"] = sorted(excluded)
    _write_json(state_path, state)

    out = deepcopy(dict(report))
    out["entity_semantic_eligibility"] = {
        "contract_version": SEMANTIC_CONTRACT_VERSION,
        "migration_contract_version": MIGRATION_CONTRACT_VERSION,
        "excluded_from_new_graph_belief_ids": sorted(excluded),
        "historical_graph_snapshot_deleted": False,
        "historical_graph_snapshot_rewritten": False,
        "historical_forecast_epistemic_binding_rewritten": False,
        "new_graph_membership_requires_current_semantic_eligibility": True,
    }
    out["semantic_contract_deprecations"] = [
        state["semantic_contract_deprecations"][key]
        for key in sorted(state.get("semantic_contract_deprecations") or {})
    ]
    out.setdefault("sample", {})["semantic_contract_deprecations_total"] = len(state.get("semantic_contract_deprecations") or {})
    out.setdefault("prospective_binding", {})["semantic_ineligible_belief_new_binding"] = False
    out.setdefault("research_boundary", {})["semantic_migration_is_not_causal_validation"] = True
    _write_json(state_dir / base.REPORT_FILENAME, out)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--pr15-report", required=True, type=Path)
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    now = base.parse_time(args.now) if args.now else datetime.now(timezone.utc)
    report = run(args.state_dir, pr15_report_path=args.pr15_report, as_of=now)
    print(json.dumps({
        "status": report["promotion"]["status"],
        "sample": report["sample"],
        "graph": report["graph_runtime"],
        "entity_semantic_eligibility": report["entity_semantic_eligibility"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
