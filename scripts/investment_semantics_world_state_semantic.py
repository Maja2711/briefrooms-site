#!/usr/bin/env python3
"""PR #19.1 semantic hardening wrapper for PR16.1 World State.

Existing append-only forecast-context bindings are preserved. New World State
bindings are created from a filtered in-memory PR15 core view that excludes
Beliefs currently marked semantically ineligible/deprecated by PR15. No frozen
forecast record is deleted or mutated.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

try:
    import investment_semantics_world_state as base
    from entity_semantic_eligibility import CONTRACT_VERSION as SEMANTIC_CONTRACT_VERSION
except ModuleNotFoundError:
    from scripts import investment_semantics_world_state as base
    from scripts.entity_semantic_eligibility import CONTRACT_VERSION as SEMANTIC_CONTRACT_VERSION

WRAPPER_CONTRACT_VERSION = "entity-semantic-world-state-filter-v1"


def _read_json(path: Optional[Path], default: Any) -> Any:
    if path is None:
        return deepcopy(default)
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _excluded(runtime: Mapping[str, Any]) -> set[str]:
    return {
        *[str(x) for x in (runtime.get("semantic_deprecations") or {}).keys()],
        *[str(x) for x in runtime.get("current_semantic_ineligible_belief_ids") or []],
    }


def _filtered_core(core: Mapping[str, Any], excluded: set[str]) -> Dict[str, Any]:
    payload = deepcopy(dict(core))
    payload["forecasts"] = [
        deepcopy(dict(row))
        for row in core.get("forecasts") or []
        if isinstance(row, Mapping) and str(row.get("belief_id") or "") not in excluded
    ]
    return payload


def run(
    state_dir: Path,
    *,
    broad_market_report_path: Path,
    sector_factor_report_path: Path,
    pr15_core_state_path: Optional[Path] = None,
    pr15_runtime_state_path: Optional[Path] = None,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    runtime = _read_json(pr15_runtime_state_path, {})
    excluded = _excluded(runtime)
    core = _read_json(pr15_core_state_path, {})
    filtered = _filtered_core(core, excluded)

    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as handle:
        json.dump(filtered, handle, ensure_ascii=False, sort_keys=True)
        filtered_path = Path(handle.name)
    try:
        report = base.run(
            state_dir,
            broad_market_report_path=broad_market_report_path,
            sector_factor_report_path=sector_factor_report_path,
            pr15_core_state_path=filtered_path,
            as_of=as_of,
        )
    finally:
        filtered_path.unlink(missing_ok=True)

    out = deepcopy(dict(report))
    excluded_forecasts = [
        str(row.get("forecast_id") or "")
        for row in core.get("forecasts") or []
        if isinstance(row, Mapping) and str(row.get("belief_id") or "") in excluded
    ]
    out["entity_semantic_eligibility"] = {
        "contract_version": SEMANTIC_CONTRACT_VERSION,
        "filter_contract_version": WRAPPER_CONTRACT_VERSION,
        "excluded_belief_ids": sorted(excluded),
        "excluded_forecast_ids": sorted(x for x in excluded_forecasts if x),
        "new_binding_for_semantic_ineligible_forecast": False,
        "historical_binding_deleted": False,
        "historical_forecast_mutated": False,
    }
    out.setdefault("forecast_context_contract", {})["semantic_eligibility_required_for_new_binding"] = True
    out.setdefault("anti_hindsight", {})["semantic_filter_rewrites_historical_bindings"] = False
    base._write_json(Path(state_dir) / base.REPORT_FILENAME, out)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--broad-market-report", required=True, type=Path)
    parser.add_argument("--sector-factor-report", required=True, type=Path)
    parser.add_argument("--pr15-core-state", type=Path)
    parser.add_argument("--pr15-runtime-state", type=Path)
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    now = base.parse_time(args.now) if args.now else datetime.now(timezone.utc)
    report = run(
        args.state_dir,
        broad_market_report_path=args.broad_market_report,
        sector_factor_report_path=args.sector_factor_report,
        pr15_core_state_path=args.pr15_core_state,
        pr15_runtime_state_path=args.pr15_runtime_state,
        as_of=now,
    )
    print(json.dumps({
        "mode": report["mode"],
        "forecast_context": report["forecast_context_contract"],
        "entity_semantic_eligibility": report["entity_semantic_eligibility"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
