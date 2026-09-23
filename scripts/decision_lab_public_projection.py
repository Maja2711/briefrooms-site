#!/usr/bin/env python3
"""Build sanitized BriefRooms Decision LAB public projection from Belief Core shadow state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from belief_aris_pattern import build_pattern_report


def load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def records(value):
    """Accept persisted Belief Core collections stored as either list or dict."""
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, list):
        return value
    return []


def build_payload(state, report):
    forecasts = records(state.get("forecasts"))
    definitions = records(state.get("definitions"))
    state = dict(state)
    state["definitions_by_id"] = {str(x.get("belief_id")): x for x in definitions if x.get("belief_id")}
    verifications = records(state.get("verifications"))
    verified = {
        str(v.get("forecast_id")): v
        for v in verifications
        if v.get("forecast_id")
    }
    def horizon_label(f):
        meta = f.get("metadata") or {}
        if meta.get("research_horizon_label"):
            return str(meta["research_horizon_label"])
        h = round(float(f.get("horizon_hours") or 0))
        return {3:"3H", 12:"12H", 24:"24H", 72:"3D", 120:"5D"}.get(h, f"{h}H")

    def row_for(f):
        fid = str(f.get("forecast_id", ""))
        v = verified.get(fid)
        return {
            "forecast_id": fid, "entity": f.get("entity"), "belief_id": f.get("belief_id"),
            "claim": (state.get("definitions_by_id") or {}).get(str(f.get("belief_id")), {}).get("claim"),
            "probability": f.get("predicted_probability"), "confidence": f.get("forecast_confidence"),
            "forecast_at": f.get("forecast_at"), "target_at": f.get("target_at"),
            "horizon_hours": f.get("horizon_hours"), "horizon_label": horizon_label(f),
            "regime": f.get("regime"), "status": "RESOLVED" if v else "OPEN",
            "outcome": None if not v else bool(v.get("outcome")),
            "brier_score": None if not v else v.get("brier_score"),
        }

    multi = [f for f in forecasts if (f.get("metadata") or {}).get("multihorizon_contract") == "decision-lab-multihorizon-v1"]
    primary = [f for f in multi if (f.get("metadata") or {}).get("primary_research_horizon")]
    legacy = [f for f in forecasts if f not in multi]
    display_source = primary + legacy
    rows = [row_for(f) for f in sorted(display_source, key=lambda x: str(x.get("forecast_at", "")), reverse=True)[:40]]

    grouped = {}
    for f in multi:
        key = (str(f.get("belief_id")), str(f.get("forecast_at")))
        grouped.setdefault(key, []).append(row_for(f))
    multihorizon_paths = []
    for (belief_id, forecast_at), path in sorted(grouped.items(), key=lambda kv: kv[0][1], reverse=True)[:40]:
        multihorizon_paths.append({
            "belief_id": belief_id, "forecast_at": forecast_at,
            "horizons": sorted(path, key=lambda x: float(x.get("horizon_hours") or 0)),
        })

    horizon_stats = []
    by_h = {}
    for f in multi:
        v = verified.get(str(f.get("forecast_id", "")))
        if not v:
            continue
        label = horizon_label(f)
        by_h.setdefault(label, []).append(float(v.get("brier_score")))
    for label in ("3H","12H","24H","3D","5D"):
        vals = by_h.get(label, [])
        horizon_stats.append({"horizon":label, "n":len(vals), "mean_brier":None if not vals else round(sum(vals)/len(vals),6)})

    cal = report.get("belief_calibration") or {}
    overall = cal.get("overall") or {}
    aris = build_pattern_report(state)

    return {
        "schema_version": "briefrooms_decision_lab_public_v2",
        "model": "BriefRooms Belief Core v2",
        "mode": "research_shadow",
        "execution_authority": False,
        "production_write_authority": False,
        "automatic_tuning": False,
        "generated_at": report.get("generated_at"),
        "forecasts": rows,
        "multihorizon_paths": multihorizon_paths,
        "horizon_aggregate": horizon_stats,
        "horizon_aggregate_min_sample": 30,
        "metrics": {
            "forecast_count": len(forecasts),
            "resolved_count": len(verifications),
            "calibration_eligible": cal.get("count_calibration_eligible", 0),
            "brier_score": overall.get("mean_brier"),
            "ece": overall.get("ece"),
            "log_loss": overall.get("mean_log_loss"),
            "calibration_status": overall.get("status", "awaiting_outcomes"),
        },
        "aris_patterns": aris.get("patterns", []),
        "aris_pattern_meta": {
            "schema_version": aris.get("schema_version"),
            "mode": aris.get("mode"),
            "causal_status": aris.get("causal_status"),
            "sample": aris.get("sample", {}),
            "methodology": aris.get("methodology", {}),
            "authority": aris.get("authority", {}),
        },
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--state-dir", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    root = Path(args.state_dir)
    state = load(root / "state.json", {})
    report = load(root / "BELIEF_CALIBRATION_REPORT.json", {})
    payload = build_payload(state, report)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
