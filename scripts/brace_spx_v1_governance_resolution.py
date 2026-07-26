#!/usr/bin/env python3
"""Resolve the v1 frozen/raw champion versus governance-audited selection conflict.

This script never reads or evaluates holdout observations. It records both decisions,
explains their roles, and closes v1 for development-only evidence.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/research/brace_spx_generation_manifest.json"
AUDIT = ROOT / "data/research/brace_spx_selection_audit.json"
REPORT = ROOT / "data/research/brace_spx_generation_research.json"
OUTPUT = ROOT / "data/research/brace_spx_v1_governance_resolution.json"


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def canonical_hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def main() -> None:
    manifest, audit, report = load(MANIFEST), load(AUDIT), load(REPORT)
    if manifest.get("generation_id") != "spx-sealed-v1" or audit.get("generation_id") != "spx-sealed-v1":
        raise RuntimeError("Resolution is restricted to spx-sealed-v1")
    holdout = manifest.get("holdout", {})
    if holdout.get("accessed") or int(holdout.get("access_count", 0)) != 0:
        raise RuntimeError("Refusing governance resolution after holdout access")

    frozen = manifest.get("frozen_champion") or {}
    selected = (audit.get("selection") or {}).get("selected") or {}
    raw_best = (audit.get("selection") or {}).get("raw_best") or {}
    if frozen.get("candidate_id") != raw_best.get("candidate_id"):
        raise RuntimeError("Frozen v1 champion is not the independently reproduced raw metric leader")
    if not selected.get("candidate_id"):
        raise RuntimeError("Selection audit has no governance-selected candidate")

    resolution = {
        "schema_version": "1.0.0",
        "generation_id": "spx-sealed-v1",
        "resolved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "development_closed_holdout_sealed",
        "holdout": {
            "status": "sealed",
            "accessed": False,
            "access_count": 0,
            "ordinary_workflow_access": False,
        },
        "raw_metric_champion": {
            "candidate_id": frozen.get("candidate_id"),
            "candidate_hash": frozen.get("candidate_hash"),
            "role": "immutable_raw_sharpe_leader_recorded_by_generation_runner",
        },
        "governance_selected_candidate": {
            "candidate_id": selected.get("candidate_id"),
            "candidate_hash": canonical_hash(selected.get("candidate", {})),
            "role": "pre_holdout_selection_after_stability_and_simplicity_audit",
        },
        "decision": {
            "winner_for_future_research_baseline": selected.get("candidate_id"),
            "v1_holdout_open_authorized": False,
            "reason": "The runner froze the raw Sharpe leader before the independent audit applied its declared stability/simplicity rule. Both records are valid but represent different roles. V1 is closed without opening holdout; the audited candidate becomes a development baseline for v2, not a retroactive v1 holdout nominee.",
        },
        "evidence_hashes": {
            "manifest": canonical_hash(manifest),
            "selection_audit": canonical_hash(audit),
            "development_report": canonical_hash(report),
        },
    }
    OUTPUT.write_text(json.dumps(resolution, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(resolution["decision"], indent=2))


if __name__ == "__main__":
    main()
