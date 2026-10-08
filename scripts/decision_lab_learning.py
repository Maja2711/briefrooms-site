#!/usr/bin/env python3
"""Decision LAB learning memory: calibration -> auditable research actions.

This module never changes production beliefs, weights, priors or trading policy.
It turns calibration evidence into machine-readable challenger/research tasks.
"""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path

MIN_N = 30

def build(report, hypothesis_utility=None):
    cal = report.get("belief_calibration") or {}
    hypotheses = cal.get("hypothesis_intelligence") or {}
    actions = []
    for belief_id, h in sorted(hypotheses.items()):
        n = int(h.get("count") or 0)
        if n < MIN_N:
            continue
        skill = h.get("brier_skill_score_vs_base_rate")
        bias = h.get("calibration_bias")
        if skill is not None and float(skill) < 0:
            actions.append({
                "belief_id": belief_id, "kind": "challenger_required",
                "reason": "negative_skill_vs_base_rate", "n": n,
                "skill_vs_base_rate": float(skill),
                "instruction": "Test a challenger out-of-sample; do not alter production weights."
            })
        if bias is not None and abs(float(bias)) >= .08:
            actions.append({
                "belief_id": belief_id, "kind": "recalibration_candidate",
                "reason": "material_calibration_bias", "n": n,
                "calibration_bias": float(bias),
                "instruction": "Evaluate recalibration out-of-sample; no automatic tuning."
            })
    # P1 HUE findings are research proposals, not execution instructions.
    # The downstream governed challenger / OOS gate remains authoritative.
    for belief_id, row in sorted(((hypothesis_utility or {}).get("hypotheses") or {}).items()):
        if not isinstance(row, dict):
            continue
        status = row.get("lifecycle_status")
        if status not in {"REVIEW", "CHALLENGER"}:
            continue
        predictive = row.get("predictive_utility") or {}
        if int(predictive.get("independent_events") or 0) < MIN_N:
            continue
        actions.append({
            "belief_id": belief_id,
            "hypothesis_version": row.get("hypothesis_version"),
            "kind": "hypothesis_utility_challenger" if status == "CHALLENGER" else "hypothesis_utility_review",
            "reason": list(row.get("reasons") or []),
            "independent_event_n": int(predictive.get("independent_events") or 0),
            "prequential_brier_gain": predictive.get("brier_gain_vs_prequential_base_rate"),
            "source": "HYPOTHESIS_UTILITY_REPORT.json",
            "instruction": "Evaluate an out-of-sample challenger in Shadow; no Evidence mutation, automatic retirement or production promotion.",
        })
    drift = cal.get("drift") or {}
    if drift.get("status") == "deteriorating":
        actions.append({
            "kind": "drift_review", "reason": "calibration_deteriorating",
            "instruction": "Audit recent-vs-prior evidence and regimes before any model change."
        })
    return {
        "schema_version": "decision-lab-learning-v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
        "source_report": "BELIEF_CALIBRATION_REPORT.json",
        "mode": "shadow_learning",
        "automatic_tuning": False,
        "production_write_authority": False,
        "actions": actions,
        "action_count": len(actions),
        "contract": "calibration may create research/challenger tasks; promotion requires separate governed validation"
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--report", required=True); ap.add_argument("--output", required=True)
    ap.add_argument("--hypothesis-utility")
    a=ap.parse_args()
    report=json.loads(Path(a.report).read_text(encoding="utf-8"))
    utility=json.loads(Path(a.hypothesis_utility).read_text(encoding="utf-8")) if a.hypothesis_utility else None
    out=build(report, utility)
    Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"action_count":out["action_count"],"automatic_tuning":False}))
if __name__=="__main__": main()
