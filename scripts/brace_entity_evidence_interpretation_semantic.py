#!/usr/bin/env python3
"""PR #19.1 semantic hardening wrapper for PR14 Entity interpretation.

The reviewed PR14 v1 ledger is intentionally preserved. This wrapper changes
only *future eligibility* and records resolved historical semantic mismatches in
an append-only deprecation ledger. It never deletes or rewrites prior
interpretations/evidence.

PR14 v1 used ``sector == Financials`` as the NII eligibility boundary. PR19.1
replaces that operational boundary with the shared Entity Semantic Eligibility
contract while preserving the original PR14 contract/version for historical
lineage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence

try:
    import brace_entity_evidence_interpretation as base
    from brace_company_entity_framework import DEFAULT_UNIVERSE
    from entity_semantic_eligibility import (
        BANK_SPECIFIC_DIMENSIONS,
        CONTRACT_VERSION as SEMANTIC_CONTRACT_VERSION,
        dimension_eligibility,
        is_resolved_semantic_mismatch,
        semantic_profile,
    )
except ModuleNotFoundError:
    from scripts import brace_entity_evidence_interpretation as base
    from scripts.brace_company_entity_framework import DEFAULT_UNIVERSE
    from scripts.entity_semantic_eligibility import (
        BANK_SPECIFIC_DIMENSIONS,
        CONTRACT_VERSION as SEMANTIC_CONTRACT_VERSION,
        dimension_eligibility,
        is_resolved_semantic_mismatch,
        semantic_profile,
    )

MIGRATION_CONTRACT_VERSION = "entity-semantic-pr14-migration-v1"


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


def _universe_by_id(path: Path) -> Dict[str, Dict[str, Any]]:
    payload = _read_json(path, {})
    out: Dict[str, Dict[str, Any]] = {}
    for raw in payload.get("instruments") or []:
        if not isinstance(raw, Mapping):
            continue
        entity_id = str(raw.get("instrument_id") or raw.get("id") or "").strip().lower()
        if entity_id:
            out[entity_id] = dict(raw)
    return out


def _semantic_primary_state(primary: Mapping[str, Any], universe: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """Prepare an in-memory PR13 view for the legacy PR14 engine.

    The legacy PR14 loop gates NII on the sector string. For this compatibility
    wrapper only, non-bank/unresolved Financials receive an internal sentinel so
    the old NII branch cannot execute. The canonical sector is restored in the
    persisted PR14 state immediately after the legacy run.
    """
    payload = deepcopy(dict(primary))
    entities: MutableMapping[str, Any] = payload.setdefault("entities", {})
    for entity_id, raw in list(entities.items()):
        if not isinstance(raw, Mapping):
            continue
        canonical = {**dict(universe.get(str(entity_id).lower()) or {}), **dict(raw)}
        profile = semantic_profile(canonical)
        row = dict(raw)
        row.update(profile)
        row["canonical_sector"] = raw.get("sector")
        if str(raw.get("sector") or "") == "Financials" and not profile["bank_specific_dimensions_eligible"]:
            row["sector"] = "Financials__BANK_SPECIFIC_DISABLED"
        entities[entity_id] = row
    return payload


def _belief_id(entity_id: str, dimension: str) -> str:
    return f"entity.{entity_id}.{dimension}"


def _historical_ids(state: Mapping[str, Any], belief_id: str) -> Dict[str, list[str]]:
    interpretations = [
        str(row.get("interpretation_id"))
        for row in state.get("interpretations") or []
        if isinstance(row, Mapping) and str(row.get("belief_id") or "") == belief_id and row.get("interpretation_id")
    ]
    evidence = [
        str(row.get("evidence_id"))
        for row in state.get("evidence") or []
        if isinstance(row, Mapping) and str(row.get("belief_id") or "") == belief_id and row.get("evidence_id")
    ]
    return {"interpretation_ids": sorted(interpretations), "evidence_ids": sorted(evidence)}


def _restore_semantics_and_migrate(
    state: MutableMapping[str, Any],
    *,
    canonical_primary: Mapping[str, Any],
    universe: Mapping[str, Mapping[str, Any]],
    effective_at: str,
) -> None:
    primary_entities = canonical_primary.get("entities") or {}
    entity_states: MutableMapping[str, Any] = state.setdefault("entities", {})
    for entity_id, entity_state_raw in list(entity_states.items()):
        if not isinstance(entity_state_raw, Mapping):
            continue
        primary = dict(primary_entities.get(entity_id) or {})
        canonical = {**dict(universe.get(str(entity_id).lower()) or {}), **primary, **dict(entity_state_raw)}
        # Prefer the canonical PR13/universe sector over the temporary sentinel.
        canonical["sector"] = primary.get("sector") or (universe.get(str(entity_id).lower()) or {}).get("sector") or entity_state_raw.get("canonical_sector") or entity_state_raw.get("sector")
        profile = semantic_profile(canonical)
        row = dict(entity_state_raw)
        row["sector"] = canonical.get("sector")
        row.pop("canonical_sector", None)
        row.update(profile)
        entity_states[entity_id] = row

    previous = deepcopy(dict(state.get("semantic_deprecations") or {}))
    deprecations: MutableMapping[str, Any] = state.setdefault("semantic_deprecations", {})
    for entity_id, row in sorted(entity_states.items()):
        if not isinstance(row, Mapping):
            continue
        for dimension in BANK_SPECIFIC_DIMENSIONS:
            if not is_resolved_semantic_mismatch(row, dimension):
                continue
            belief_id = _belief_id(str(entity_id), dimension)
            historical = _historical_ids(state, belief_id)
            if not historical["interpretation_ids"] and not historical["evidence_ids"]:
                continue
            existing = deprecations.get(belief_id)
            if existing is not None:
                continue
            eligibility = dimension_eligibility(row, dimension)
            record = {
                "migration_contract_version": MIGRATION_CONTRACT_VERSION,
                "semantic_eligibility_contract_version": SEMANTIC_CONTRACT_VERSION,
                "belief_id": belief_id,
                "entity_id": str(entity_id),
                "dimension": dimension,
                "status": "DEPRECATED_SEMANTIC_MISMATCH",
                "effective_at": effective_at,
                "entity_archetype": eligibility["entity_archetype"],
                "entity_archetype_source": eligibility["entity_archetype_source"],
                "exposure_key": eligibility.get("exposure_key"),
                "reason": eligibility["reason"],
                **historical,
                "historical_records_preserved": True,
                "future_interpretation_enabled": False,
                "future_evidence_enabled": False,
                "retroactive_reclassification": False,
                "historical_deletion": False,
            }
            record["immutable_sha256"] = _sha(record)
            deprecations[belief_id] = record

    for belief_id, record in previous.items():
        if belief_id not in deprecations or deprecations[belief_id] != record:
            raise RuntimeError(f"PR19.1 PR14 append-only semantic deprecation mutation: {belief_id}")


def _current_ineligible(entity_states: Mapping[str, Any]) -> list[str]:
    out = []
    for entity_id, row in sorted(entity_states.items()):
        if not isinstance(row, Mapping):
            continue
        # Only dimensions active in PR14 are relevant here; other bank-specific
        # dimensions remain deferred in the legacy PR14 contract.
        dimension = "net_interest_income_durability"
        if not dimension_eligibility(row, dimension)["eligible"]:
            out.append(_belief_id(str(entity_id), dimension))
    return sorted(out)


def run(
    state_dir: Path,
    *,
    primary_state_path: Path,
    universe_path: Path = DEFAULT_UNIVERSE,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    effective_at = base.iso_z(now)
    canonical_primary = _read_json(primary_state_path, {})
    if not canonical_primary:
        raise ValueError("PR19.1 semantic PR14 wrapper requires non-empty PR13 state")
    universe = _universe_by_id(universe_path)
    semantic_primary = _semantic_primary_state(canonical_primary, universe)

    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as handle:
        json.dump(semantic_primary, handle, ensure_ascii=False, sort_keys=True)
        temp_path = Path(handle.name)
    try:
        report = base.run(state_dir, primary_state_path=temp_path, as_of=now)
    finally:
        temp_path.unlink(missing_ok=True)

    state_path = state_dir / base.STATE_FILENAME
    state = _read_json(state_path, {})
    _restore_semantics_and_migrate(
        state,
        canonical_primary=canonical_primary,
        universe=universe,
        effective_at=effective_at,
    )
    current_ineligible = _current_ineligible(state.get("entities") or {})
    state["current_semantic_ineligible_belief_ids"] = current_ineligible
    state["semantic_eligibility_contract_version"] = SEMANTIC_CONTRACT_VERSION
    state["semantic_migration_contract_version"] = MIGRATION_CONTRACT_VERSION
    _write_json(state_path, state)

    report = deepcopy(dict(report))
    report["semantic_eligibility"] = {
        "contract_version": SEMANTIC_CONTRACT_VERSION,
        "migration_contract_version": MIGRATION_CONTRACT_VERSION,
        "sector_is_not_business_model": True,
        "bank_specific_dimension": "net_interest_income_durability",
        "bank_archetype_required": True,
        "unresolved_financials_fail_closed_without_permanent_deprecation": True,
        "resolved_nonbank_historical_records_are_append_only_deprecated": True,
        "current_semantic_ineligible_belief_ids": current_ineligible,
    }
    report["semantic_deprecations"] = [
        state["semantic_deprecations"][key] for key in sorted(state.get("semantic_deprecations") or {})
    ]
    report.setdefault("sample", {})["semantic_deprecations_total"] = len(state.get("semantic_deprecations") or {})
    report["sample"]["current_semantic_ineligible_beliefs"] = len(current_ineligible)
    report.setdefault("anti_hindsight", {})["semantic_migration_deletes_historical_records"] = False
    report["anti_hindsight"]["semantic_deprecations_append_only"] = True
    report.setdefault("interpretation_boundary", {})["financials_sector_alone_enables_nii"] = False
    report["interpretation_boundary"]["bank_archetype_required_for_future_nii"] = True

    enabled = ((report.get("contracts") or {}).get("enabled") or [])
    for row in enabled:
        if isinstance(row, MutableMapping) and row.get("dimension") == "net_interest_income_durability":
            row["legacy_sector_gate"] = row.get("sector")
            row["sector"] = None
            row["entity_archetypes"] = ["bank"]
            row["semantic_eligibility_contract_version"] = SEMANTIC_CONTRACT_VERSION
    _write_json(state_dir / base.REPORT_FILENAME, report)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--primary-state", required=True, type=Path)
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE), type=Path)
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    now = base.parse_time(args.now) if args.now else datetime.now(timezone.utc)
    report = run(
        args.state_dir,
        primary_state_path=args.primary_state,
        universe_path=args.universe,
        as_of=now,
    )
    print(json.dumps({
        "mode": report["mode"],
        "sample": report["sample"],
        "semantic_eligibility": report["semantic_eligibility"],
        "active_decision_influence": report["active_decision_influence"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
