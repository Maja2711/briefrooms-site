#!/usr/bin/env python3
"""PR #19.1 semantic hardening wrapper for PR15 Entity Beliefs.

Existing PR15 Belief Core definitions, evidence, forecasts and verifications are
historical records and remain immutable. PR19.1 adds a separate append-only
semantic deprecation ledger and blocks future evidence/forecast activity for
business-model-ineligible dimensions.

Resolved non-bank mismatches are permanently deprecated. An unresolved
Financials archetype fails closed for new activity but is *not* irreversibly
deprecated until the archetype is actually resolved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence, Tuple

try:
    import brace_entity_belief_state_forecast as base
    from entity_semantic_eligibility import (
        CONTRACT_VERSION as SEMANTIC_CONTRACT_VERSION,
        dimension_eligibility,
        is_resolved_semantic_mismatch,
    )
except ModuleNotFoundError:
    from scripts import brace_entity_belief_state_forecast as base
    from scripts.entity_semantic_eligibility import (
        CONTRACT_VERSION as SEMANTIC_CONTRACT_VERSION,
        dimension_eligibility,
        is_resolved_semantic_mismatch,
    )

MIGRATION_CONTRACT_VERSION = "entity-semantic-pr15-migration-v1"


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


def _source_entities(source: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(key): dict(value)
        for key, value in (source.get("entities") or {}).items()
        if isinstance(value, Mapping)
    }


def _semantic_eligible_dimensions(entity_state: Mapping[str, Any]) -> Tuple[str, ...]:
    # Preserve all legacy PR15 eligibility first, then apply the stricter
    # business-model boundary. This can only remove eligibility, never invent it.
    legacy = _LEGACY_ELIGIBLE_DIMENSIONS(entity_state)
    return tuple(sorted(
        dimension for dimension in legacy
        if dimension_eligibility(entity_state, dimension)["eligible"]
    ))


def _belief_id(entity_id: str, dimension: str) -> str:
    return f"entity.{entity_id}.{dimension}"


def _core_counts(core: Mapping[str, Any], belief_id: str) -> Dict[str, int]:
    return {
        "definitions": sum(1 for row in core.get("definitions") or [] if isinstance(row, Mapping) and str(row.get("belief_id") or "") == belief_id),
        "belief_states": sum(1 for row in core.get("beliefs") or [] if isinstance(row, Mapping) and str(row.get("belief_id") or "") == belief_id),
        "evidence": sum(1 for row in core.get("evidence") or [] if isinstance(row, Mapping) and str(row.get("belief_id") or "") == belief_id),
        "forecasts": sum(1 for row in core.get("forecasts") or [] if isinstance(row, Mapping) and str(row.get("belief_id") or "") == belief_id),
        "verifications": sum(1 for row in core.get("verifications") or [] if isinstance(row, Mapping) and str(row.get("belief_id") or "") == belief_id),
    }


def _historical_source_counts(source: Mapping[str, Any], belief_id: str) -> Dict[str, int]:
    return {
        "pr14_interpretations": sum(1 for row in source.get("interpretations") or [] if isinstance(row, Mapping) and str(row.get("belief_id") or "") == belief_id),
        "pr14_evidence": sum(1 for row in source.get("evidence") or [] if isinstance(row, Mapping) and str(row.get("belief_id") or "") == belief_id),
    }


def _current_ineligible(entity_states: Mapping[str, Mapping[str, Any]]) -> list[str]:
    out: list[str] = []
    for entity_id, entity in sorted(entity_states.items()):
        for dimension in base.DIMENSION_CONFIG:
            # Respect legacy sector eligibility too. A dimension that was never
            # valid under PR15 does not need a semantic-current projection.
            if dimension not in _LEGACY_ELIGIBLE_DIMENSIONS(entity):
                continue
            if not dimension_eligibility(entity, dimension)["eligible"]:
                out.append(_belief_id(entity_id, dimension))
    return sorted(set(out))


def _apply_migration_before_run(
    state_dir: Path,
    source: Mapping[str, Any],
    *,
    now: datetime,
) -> Tuple[list[str], list[str]]:
    runtime_path = state_dir / base.STATE_FILENAME
    core_path = state_dir / base.BELIEF_CORE_DIRNAME / "state.json"
    runtime = _read_json(runtime_path, base.empty_state())
    core = _read_json(core_path, {})
    entities = _source_entities(source)
    now_z = base.iso_z(now)

    previous_deprecations = deepcopy(dict(runtime.get("semantic_deprecations") or {}))
    deprecations: MutableMapping[str, Any] = runtime.setdefault("semantic_deprecations", {})
    closures: MutableMapping[str, Any] = runtime.setdefault("forecast_closures", {})
    current_ineligible = _current_ineligible(entities)
    resolved_deprecated: list[str] = []

    forecasts = [row for row in core.get("forecasts") or [] if isinstance(row, Mapping)]
    for entity_id, entity in sorted(entities.items()):
        for dimension in base.DIMENSION_CONFIG:
            if dimension not in _LEGACY_ELIGIBLE_DIMENSIONS(entity):
                continue
            if not is_resolved_semantic_mismatch(entity, dimension):
                continue
            belief_id = _belief_id(entity_id, dimension)
            counts = _core_counts(core, belief_id)
            source_counts = _historical_source_counts(source, belief_id)
            if not any(counts.values()) and not any(source_counts.values()):
                continue
            resolved_deprecated.append(belief_id)
            if belief_id not in deprecations:
                eligibility = dimension_eligibility(entity, dimension)
                record = {
                    "migration_contract_version": MIGRATION_CONTRACT_VERSION,
                    "semantic_eligibility_contract_version": SEMANTIC_CONTRACT_VERSION,
                    "belief_id": belief_id,
                    "entity_id": entity_id,
                    "dimension": dimension,
                    "status": "DEPRECATED_SEMANTIC_MISMATCH",
                    "effective_at": now_z,
                    "entity_archetype": eligibility["entity_archetype"],
                    "entity_archetype_source": eligibility["entity_archetype_source"],
                    "exposure_key": eligibility.get("exposure_key"),
                    "reason": eligibility["reason"],
                    "historical_core_counts": counts,
                    "historical_pr14_counts": source_counts,
                    "historical_definitions_preserved": True,
                    "historical_evidence_preserved": True,
                    "historical_forecasts_preserved": True,
                    "historical_verifications_preserved": True,
                    "future_evidence_enabled": False,
                    "future_forecast_enabled": False,
                    "calibration_inclusion_enabled": False,
                    "engine_bridge_inclusion_enabled": False,
                    "causal_graph_active_membership_enabled": False,
                    "retroactive_reclassification": False,
                    "historical_deletion": False,
                }
                record["immutable_sha256"] = _sha(record)
                deprecations[belief_id] = record

            # A still-open forecast for a resolved mismatch becomes terminally
            # unusable prospectively. Already-closed historical outcomes are not
            # rewritten or reclassified.
            for forecast in forecasts:
                if str(forecast.get("belief_id") or "") != belief_id:
                    continue
                forecast_id = str(forecast.get("forecast_id") or "")
                if not forecast_id or forecast_id in closures:
                    continue
                closures[forecast_id] = {
                    "forecast_id": forecast_id,
                    "belief_id": belief_id,
                    "status": "semantic_deprecated",
                    "closed_at": now_z,
                    "calibration_eligible": False,
                    "terminal": True,
                    "reason": "resolved_nonbank_archetype_rejects_bank_specific_dimension",
                    "historical_forecast_preserved": True,
                    "forecast_rewritten": False,
                }

    for belief_id, record in previous_deprecations.items():
        if belief_id not in deprecations or deprecations[belief_id] != record:
            raise RuntimeError(f"PR19.1 PR15 append-only semantic deprecation mutation: {belief_id}")

    runtime["semantic_eligibility_contract_version"] = SEMANTIC_CONTRACT_VERSION
    runtime["semantic_migration_contract_version"] = MIGRATION_CONTRACT_VERSION
    runtime["current_semantic_ineligible_belief_ids"] = current_ineligible
    runtime["semantic_source_fingerprint"] = _sha({
        "entities": {
            key: {
                "sector": value.get("sector"),
                "exposure_key": value.get("exposure_key"),
                "entity_archetype": value.get("entity_archetype"),
                "semantic_eligibility_contract_version": value.get("semantic_eligibility_contract_version"),
            }
            for key, value in sorted(entities.items())
        }
    })
    _write_json(runtime_path, runtime)
    return current_ineligible, sorted(set(resolved_deprecated))


def _annotate_report(report: Mapping[str, Any], state_dir: Path) -> Dict[str, Any]:
    runtime = _read_json(state_dir / base.STATE_FILENAME, {})
    deprecated = set(str(x) for x in (runtime.get("semantic_deprecations") or {}).keys())
    current_ineligible = set(str(x) for x in runtime.get("current_semantic_ineligible_belief_ids") or [])
    out = deepcopy(dict(report))

    def status_for(belief_id: str) -> str:
        if belief_id in deprecated:
            return "DEPRECATED_SEMANTIC_MISMATCH"
        if belief_id in current_ineligible:
            return "SEMANTICALLY_INELIGIBLE_FAIL_CLOSED"
        return "ACTIVE_SEMANTICALLY_ELIGIBLE"

    for field in ("belief_states", "forecasts", "verifications"):
        for row in out.get(field) or []:
            if isinstance(row, MutableMapping):
                row["semantic_status"] = status_for(str(row.get("belief_id") or ""))

    active = [
        row for row in out.get("active_forecasts") or []
        if str(row.get("belief_id") or "") not in current_ineligible
    ]
    out["active_forecasts"] = active
    out.setdefault("sample", {})["active_forecasts"] = len(active)
    out["sample"]["semantic_deprecations_total"] = len(deprecated)
    out["sample"]["current_semantic_ineligible_beliefs"] = len(current_ineligible)
    out["semantic_eligibility"] = {
        "contract_version": SEMANTIC_CONTRACT_VERSION,
        "migration_contract_version": MIGRATION_CONTRACT_VERSION,
        "sector_is_not_business_model": True,
        "future_bank_specific_evidence_requires_bank_archetype": True,
        "future_bank_specific_forecast_requires_bank_archetype": True,
        "unresolved_archetype_fails_closed_without_irreversible_deprecation": True,
        "current_semantic_ineligible_belief_ids": sorted(current_ineligible),
    }
    out["semantic_deprecations"] = [
        runtime["semantic_deprecations"][key] for key in sorted(runtime.get("semantic_deprecations") or {})
    ]
    out.setdefault("anti_hindsight", {})["semantic_deprecation_rewrites_historical_forecast"] = False
    out["anti_hindsight"]["semantic_deprecation_rewrites_historical_verification"] = False
    out["anti_hindsight"]["semantic_deprecations_append_only"] = True
    out.setdefault("state_boundary", {})["deprecated_belief_new_evidence"] = False
    out["state_boundary"]["deprecated_belief_new_forecast"] = False
    return out


# Freeze the original before monkey-patching the module global used throughout
# the reviewed PR15 implementation.
_LEGACY_ELIGIBLE_DIMENSIONS = base._eligible_dimensions


def run(
    state_dir: Path,
    *,
    interpretation_state_path: Path,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    source = _read_json(interpretation_state_path, {})
    base._validate_source_state(source)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    _apply_migration_before_run(state_dir, source, now=now)
    original = base._eligible_dimensions
    base._eligible_dimensions = _semantic_eligible_dimensions
    try:
        report = base.run(
            state_dir,
            interpretation_state_path=interpretation_state_path,
            as_of=now,
        )
    finally:
        base._eligible_dimensions = original

    report = _annotate_report(report, state_dir)
    _write_json(state_dir / base.REPORT_FILENAME, report)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--interpretation-state", required=True, type=Path)
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    now = base.parse_time(args.now) if args.now else datetime.now(timezone.utc)
    report = run(
        args.state_dir,
        interpretation_state_path=args.interpretation_state,
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
