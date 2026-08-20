#!/usr/bin/env python3
"""PR #19.1 semantic hardening wrapper for the PR17 BRACE bridge.

Old append-only WITH/WITHOUT pair sets remain historical evidence. For new pair
sets, forecasts belonging to semantic-ineligible/deprecated Entity Beliefs are
not eligible inputs. This prevents a corrected taxonomy from silently feeding a
legacy bank-specific Belief into new BRACE counterfactuals.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

try:
    import brace_entity_belief_shadow_bridge as base
    from entity_semantic_eligibility import CONTRACT_VERSION as SEMANTIC_CONTRACT_VERSION
except ModuleNotFoundError:
    from scripts import brace_entity_belief_shadow_bridge as base
    from scripts.entity_semantic_eligibility import CONTRACT_VERSION as SEMANTIC_CONTRACT_VERSION

WRAPPER_CONTRACT_VERSION = "entity-semantic-pr17-filter-v1"
_ORIGINAL_ELIGIBLE = base._eligible_entity_forecasts
_EXCLUDED: set[str] = set()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _excluded(runtime: Mapping[str, Any]) -> set[str]:
    return {
        *[str(x) for x in (runtime.get("semantic_deprecations") or {}).keys()],
        *[str(x) for x in runtime.get("current_semantic_ineligible_belief_ids") or []],
    }


def _eligible_filtered(
    core: Mapping[str, Any],
    world_state: Mapping[str, Any],
    *,
    entity: str,
    decision_at: datetime,
) -> list[Dict[str, Any]]:
    rows = _ORIGINAL_ELIGIBLE(core, world_state, entity=entity, decision_at=decision_at)
    return [row for row in rows if str(row.get("belief_id") or "") not in _EXCLUDED]


def run(
    state_dir: Path,
    *,
    pr15_core_state_path: Path,
    pr15_runtime_state_path: Path,
    world_state_path: Path,
    world_report_path: Path,
    pending_decisions_path: Path,
    analysis_path: Path,
    portfolio_path: Path,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    runtime = _read_json(pr15_runtime_state_path, {})
    excluded = _excluded(runtime)
    global _EXCLUDED
    _EXCLUDED = excluded
    original = base._eligible_entity_forecasts
    base._eligible_entity_forecasts = _eligible_filtered
    try:
        report = base.run(
            state_dir,
            pr15_core_state_path=pr15_core_state_path,
            pr15_runtime_state_path=pr15_runtime_state_path,
            world_state_path=world_state_path,
            world_report_path=world_report_path,
            pending_decisions_path=pending_decisions_path,
            analysis_path=analysis_path,
            portfolio_path=portfolio_path,
            as_of=as_of,
        )
    finally:
        base._eligible_entity_forecasts = original
        _EXCLUDED = set()

    out = deepcopy(dict(report))
    out["entity_semantic_eligibility"] = {
        "contract_version": SEMANTIC_CONTRACT_VERSION,
        "filter_contract_version": WRAPPER_CONTRACT_VERSION,
        "excluded_belief_ids": sorted(excluded),
        "semantic_ineligible_forecast_in_new_pair": False,
        "historical_pair_set_deleted": False,
        "historical_pair_set_rewritten": False,
    }
    out.setdefault("source_contract", {})["entity_semantic_eligibility_required"] = True
    out.setdefault("anti_hindsight", {})["semantic_filter_rewrites_existing_pair_sets"] = False
    base._write_json(Path(state_dir) / base.REPORT_FILENAME, out)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--pr15-core-state", required=True, type=Path)
    parser.add_argument("--pr15-runtime-state", required=True, type=Path)
    parser.add_argument("--world-state", required=True, type=Path)
    parser.add_argument("--world-report", required=True, type=Path)
    parser.add_argument("--pending-decisions", required=True, type=Path)
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--portfolio", required=True, type=Path)
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    now = base.parse_time(args.now) if args.now else datetime.now(timezone.utc)
    report = run(
        args.state_dir,
        pr15_core_state_path=args.pr15_core_state,
        pr15_runtime_state_path=args.pr15_runtime_state,
        world_state_path=args.world_state,
        world_report_path=args.world_report,
        pending_decisions_path=args.pending_decisions,
        analysis_path=args.analysis,
        portfolio_path=args.portfolio,
        as_of=now,
    )
    print(json.dumps({
        "mode": report["mode"],
        "phase": report["bridge_phase"],
        "sample": report["sample"],
        "entity_semantic_eligibility": report["entity_semantic_eligibility"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
