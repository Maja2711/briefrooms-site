#!/usr/bin/env python3
"""PR #19.1 semantic hardening wrapper for PR16 calibration diagnostics.

Historical PR15 verifications remain stored, but any Belief currently marked as
semantically ineligible or append-only deprecated is excluded from calibration
statistics. This prevents a taxonomy mistake from becoming calibration evidence
without rewriting the historical verification ledger.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

try:
    import brace_entity_calibration_diagnostics as base
    from entity_semantic_eligibility import CONTRACT_VERSION as SEMANTIC_CONTRACT_VERSION
except ModuleNotFoundError:
    from scripts import brace_entity_calibration_diagnostics as base
    from scripts.entity_semantic_eligibility import CONTRACT_VERSION as SEMANTIC_CONTRACT_VERSION

WRAPPER_CONTRACT_VERSION = "entity-semantic-pr16-filter-v1"
_ORIGINAL_COLLECT = base.collect_calibration_rows
_EXCLUDED_BELIEF_IDS: set[str] = set()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _collect_filtered(
    core: Mapping[str, Any],
    runtime: Mapping[str, Any],
    *,
    as_of: datetime,
) -> Tuple[list[Dict[str, Any]], Dict[str, list[Dict[str, Any]]]]:
    rows, issues = _ORIGINAL_COLLECT(core, runtime, as_of=as_of)
    excluded = {
        *[str(x) for x in (runtime.get("semantic_deprecations") or {}).keys()],
        *[str(x) for x in runtime.get("current_semantic_ineligible_belief_ids") or []],
        *_EXCLUDED_BELIEF_IDS,
    }
    kept = [row for row in rows if str(row.get("belief_id") or "") not in excluded]
    dropped = [row for row in rows if str(row.get("belief_id") or "") in excluded]
    if dropped:
        issues.setdefault("warning", []).append({
            "code": "semantic_ineligible_verifications_excluded",
            "belief_ids": sorted({str(row.get("belief_id") or "") for row in dropped}),
            "verification_ids": sorted(str(row.get("verification_id") or "") for row in dropped),
            "count": len(dropped),
            "historical_verifications_preserved": True,
        })
    return kept, issues


def run(
    state_dir: Path,
    *,
    belief_core_state_path: Path,
    pr15_runtime_state_path: Path,
    pr15_report_path: Path,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    runtime = _read_json(pr15_runtime_state_path, {})
    excluded = {
        *[str(x) for x in (runtime.get("semantic_deprecations") or {}).keys()],
        *[str(x) for x in runtime.get("current_semantic_ineligible_belief_ids") or []],
    }
    global _EXCLUDED_BELIEF_IDS
    _EXCLUDED_BELIEF_IDS = excluded
    original = base.collect_calibration_rows
    base.collect_calibration_rows = _collect_filtered
    try:
        report = base.run(
            state_dir,
            belief_core_state_path=belief_core_state_path,
            pr15_runtime_state_path=pr15_runtime_state_path,
            pr15_report_path=pr15_report_path,
            as_of=as_of,
        )
    finally:
        base.collect_calibration_rows = original
        _EXCLUDED_BELIEF_IDS = set()

    out = deepcopy(dict(report))
    raw_core = _read_json(belief_core_state_path, {})
    raw_eligible = [
        row for row in raw_core.get("verifications") or []
        if isinstance(row, Mapping) and row.get("calibration_eligible") is True
    ]
    excluded_verifications = [
        row for row in raw_eligible if str(row.get("belief_id") or "") in excluded
    ]
    out["semantic_eligibility"] = {
        "contract_version": SEMANTIC_CONTRACT_VERSION,
        "filter_contract_version": WRAPPER_CONTRACT_VERSION,
        "excluded_belief_ids": sorted(excluded),
        "historical_verifications_deleted": False,
        "historical_verifications_rewritten": False,
        "semantic_ineligible_included_in_calibration": False,
    }
    out.setdefault("sample", {})["semantic_excluded_calibration_verifications"] = len(excluded_verifications)
    out.setdefault("anti_hindsight", {})["semantic_filter_retroactively_relabels_outcomes"] = False
    out["anti_hindsight"]["historical_semantic_mismatch_retained_for_audit_only"] = True
    base._write_json(Path(state_dir) / base.REPORT_FILENAME, out)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--belief-core-state", required=True, type=Path)
    parser.add_argument("--pr15-runtime-state", required=True, type=Path)
    parser.add_argument("--pr15-report", required=True, type=Path)
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    now = base.parse_time(args.now) if args.now else datetime.now(timezone.utc)
    report = run(
        args.state_dir,
        belief_core_state_path=args.belief_core_state,
        pr15_runtime_state_path=args.pr15_runtime_state,
        pr15_report_path=args.pr15_report,
        as_of=now,
    )
    print(json.dumps({
        "mode": report["mode"],
        "sample": report["sample"],
        "semantic_eligibility": report["semantic_eligibility"],
        "promotion": report["promotion_readiness"]["status"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
