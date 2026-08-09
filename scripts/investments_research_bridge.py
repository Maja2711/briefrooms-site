#!/usr/bin/env python3
"""Governed bridge from Research Lab registry into weekly candidate convictions.

Only explicitly promoted shadow/paper candidates can influence runtime, and influence is bounded.
Research results never create broker orders or directly replace a production strategy.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, Tuple

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "data/investments/research_lab_policy.json"
REGISTRY = ROOT / "data/investments/research_lab_promotion_registry.json"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def apply(instrument_id: str, candidates: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    policy = _read(POLICY, {})
    registry = _read(REGISTRY, {"candidates": []})
    out = {k: dict(v) for k, v in candidates.items()}
    gov = policy.get("governance") or {}
    allowed = set(gov.get("allowed_runtime_statuses") or [])
    cap = abs(float(gov.get("max_runtime_adjustment_points") or 0.0))
    applied = []
    if not policy.get("enabled") or instrument_id not in set(policy.get("scope") or []):
        return out, {"enabled": False, "applied": []}
    for row in registry.get("candidates") or []:
        if str(row.get("instrument_id")) != instrument_id or str(row.get("status")) not in allowed:
            continue
        strategy_id = str(row.get("strategy_id") or "")
        if strategy_id not in out:
            continue
        raw = float(row.get("runtime_adjustment_points") or 0.0)
        adj = max(-cap, min(cap, raw))
        out[strategy_id]["research_lab_base_conviction"] = out[strategy_id].get("conviction", 0.0)
        out[strategy_id]["research_lab_candidate_id"] = row.get("candidate_id")
        out[strategy_id]["research_lab_status"] = row.get("status")
        out[strategy_id]["research_lab_adjustment"] = round(adj, 4)
        out[strategy_id]["conviction"] = round(float(out[strategy_id].get("conviction") or 0.0) + adj, 4)
        applied.append({"candidate_id": row.get("candidate_id"), "strategy_id": strategy_id, "adjustment": round(adj, 4), "status": row.get("status")})
    return out, {"enabled": True, "cap_points": cap, "applied": applied}
