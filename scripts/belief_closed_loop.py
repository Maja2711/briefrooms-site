#!/usr/bin/env python3
"""Governed Belief Core closed loop.

Resolved prospective forecasts are not just archived. They can create a frozen
recalibration challenger. The challenger is evaluated only on forecasts created
after it was frozen. If strict prospective gates pass, a versioned production
probability overlay is activated. Raw/control probability remains preserved in
forecast metadata so the overlay can be monitored and rolled back.

This module never changes evidence, source reliability, belief definitions,
trading policy, sizing or execution.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

SCHEMA_VERSION = "belief-closed-loop-v1"
POLICY_SCHEMA = "belief-core-production-overrides-v1"
MIN_DISCOVERY_N = 50
MIN_PROSPECTIVE_N = 50
MIN_ROLLBACK_N = 30
PROMOTION_BRIER_REL_IMPROVEMENT = 0.05
MAX_ECE_DEGRADATION = 0.01
MAX_ACCURACY_DEGRADATION = 0.03
ROLLBACK_BRIER_REL_DEGRADATION = 0.05
ROLLBACK_ECE_DEGRADATION = 0.05
BLOCKS = 4


def now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def clamp(value: float, low: float = 1e-6, high: float = 1 - 1e-6) -> float:
    return max(low, min(high, float(value)))


def logit(p: float) -> float:
    p = clamp(p)
    return math.log(p / (1 - p))


def logistic(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1 / (1 + z)
    z = math.exp(x)
    return z / (1 + z)


def transform_probability(p: float, transform: Mapping[str, Any]) -> float:
    if str(transform.get("type")) != "logit_affine_v1":
        return float(p)
    intercept_raw = transform.get("intercept")
    slope_raw = transform.get("slope")
    intercept = 0.0 if intercept_raw is None else float(intercept_raw)
    slope = 1.0 if slope_raw is None else float(slope_raw)
    return round(clamp(logistic(intercept + slope * logit(float(p))), .01, .99), 6)


def safe_log_loss(p: float, y: int) -> float:
    p = clamp(p, 1e-9, 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def metrics(rows: Sequence[Tuple[float, int]]) -> Dict[str, Any]:
    if not rows:
        return {"n": 0, "brier": None, "log_loss": None, "ece": None, "accuracy": None}
    ps = [float(p) for p, _ in rows]
    ys = [int(y) for _, y in rows]
    brier = mean((p - y) ** 2 for p, y in rows)
    ll = mean(safe_log_loss(p, y) for p, y in rows)
    accuracy = mean(1.0 if ((p >= .5) == bool(y)) else 0.0 for p, y in rows)
    ece = 0.0
    for low_i in range(10):
        low = low_i / 10
        high = (low_i + 1) / 10
        bucket = [(p, y) for p, y in rows if low <= p < high or (high == 1 and p == 1)]
        if not bucket:
            continue
        pbar = mean(p for p, _ in bucket)
        ybar = mean(y for _, y in bucket)
        ece += len(bucket) / len(rows) * abs(pbar - ybar)
    return {
        "n": len(rows),
        "brier": round(brier, 6),
        "log_loss": round(ll, 6),
        "ece": round(ece, 6),
        "accuracy": round(accuracy, 6),
    }


def relative_improvement(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old <= 1e-12:
        return None
    return (old - new) / old


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return default


def eligible_rows(state: Mapping[str, Any], belief_id: str) -> List[Dict[str, Any]]:
    forecasts = {str(x.get("forecast_id") or ""): x for x in state.get("forecasts", []) if isinstance(x, Mapping)}
    rows = []
    for v in state.get("verifications", []) if isinstance(state.get("verifications"), list) else []:
        if not isinstance(v, Mapping) or str(v.get("belief_id")) != belief_id:
            continue
        if not bool(v.get("calibration_eligible", True)):
            continue
        p = v.get("predicted_probability")
        try:
            p = float(p)
        except (TypeError, ValueError):
            continue
        y = 1 if bool(v.get("outcome")) else 0
        f = forecasts.get(str(v.get("forecast_id") or "")) or {}
        meta = f.get("metadata") if isinstance(f.get("metadata"), Mapping) else {}
        raw = meta.get("raw_probability")
        try:
            raw = float(raw) if raw is not None else p
        except (TypeError, ValueError):
            raw = p
        rows.append({
            "forecast_id": v.get("forecast_id"),
            "forecast_at": str(v.get("forecast_at") or ""),
            "verified_at": str(v.get("verified_at") or ""),
            "raw_probability": raw,
            "production_probability": p,
            "outcome": y,
        })
    rows.sort(key=lambda x: x["forecast_at"])
    return rows


def triggered(report: Mapping[str, Any], belief_id: str) -> Tuple[bool, List[str]]:
    h = (((report.get("belief_calibration") or {}).get("hypothesis_intelligence") or {}).get(belief_id) or {})
    n = int(h.get("count") or 0)
    reasons = []
    if n < MIN_DISCOVERY_N:
        return False, reasons
    skill = h.get("brier_skill_score_vs_base_rate")
    bias = h.get("calibration_bias")
    if skill is not None and float(skill) < 0:
        reasons.append("negative_skill_vs_base_rate")
    if bias is not None and abs(float(bias)) >= .08:
        reasons.append("material_calibration_bias")
    return bool(reasons), reasons


def fit_transform(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any] | None:
    if len(rows) < MIN_DISCOVERY_N:
        return None
    split = max(30, int(len(rows) * .70))
    if len(rows) - split < 15:
        split = len(rows) - 15
    train, validation = rows[:split], rows[split:]
    if len(train) < 30 or len(validation) < 15:
        return None

    candidates = []
    for ai in range(-8, 9):
        intercept = ai * .05
        for bi in range(5, 16):
            slope = bi * .10
            pairs = [(transform_probability(r["raw_probability"], {"type":"logit_affine_v1","intercept":intercept,"slope":slope}), r["outcome"]) for r in train]
            score = float(metrics(pairs)["brier"])
            penalty = .0015 * ((intercept / .40) ** 2 + ((slope - 1.0) / .50) ** 2)
            candidates.append((score + penalty, intercept, slope))
    _, intercept, slope = min(candidates)
    transform = {"type":"logit_affine_v1","intercept":round(intercept,4),"slope":round(slope,4)}
    control = metrics([(r["raw_probability"], r["outcome"]) for r in validation])
    challenger = metrics([(transform_probability(r["raw_probability"], transform), r["outcome"]) for r in validation])
    improvement = relative_improvement(challenger["brier"], control["brier"])
    if improvement is None or improvement < .01 or challenger["log_loss"] > control["log_loss"]:
        return None
    return {
        "transform": transform,
        "discovery_n": len(train),
        "validation_n": len(validation),
        "validation_control": control,
        "validation_challenger": challenger,
        "validation_brier_relative_improvement": round(improvement, 6),
    }


def block_stability(rows: Sequence[Dict[str, Any]], transform: Mapping[str, Any]) -> Dict[str, Any]:
    if len(rows) < BLOCKS:
        return {"blocks": 0, "improved": 0, "worst_relative_degradation": None}
    size = len(rows) // BLOCKS
    blocks = []
    for i in range(BLOCKS):
        part = rows[i * size:(i + 1) * size] if i < BLOCKS - 1 else rows[i * size:]
        if not part:
            continue
        c = metrics([(r["raw_probability"], r["outcome"]) for r in part])
        n = metrics([(transform_probability(r["raw_probability"], transform), r["outcome"]) for r in part])
        imp = relative_improvement(n["brier"], c["brier"])
        blocks.append(imp)
    return {
        "blocks": len(blocks),
        "improved": sum(1 for x in blocks if x is not None and x > 0),
        "worst_relative_degradation": None if not blocks else round(max(0.0, max(-(x or 0.0) for x in blocks)), 6),
        "relative_improvements": [None if x is None else round(x, 6) for x in blocks],
    }


def prospective_evaluation(rows: Sequence[Dict[str, Any]], frozen_at: str, transform: Mapping[str, Any]) -> Dict[str, Any]:
    future = [r for r in rows if r["forecast_at"] and parse_time(r["forecast_at"]) > parse_time(frozen_at)]
    control = metrics([(r["raw_probability"], r["outcome"]) for r in future])
    challenger = metrics([(transform_probability(r["raw_probability"], transform), r["outcome"]) for r in future])
    brier_imp = relative_improvement(challenger["brier"], control["brier"])
    stability = block_stability(future, transform)
    pass_gate = (
        len(future) >= MIN_PROSPECTIVE_N
        and brier_imp is not None and brier_imp >= PROMOTION_BRIER_REL_IMPROVEMENT
        and challenger["log_loss"] < control["log_loss"]
        and challenger["ece"] <= control["ece"] + MAX_ECE_DEGRADATION
        and challenger["accuracy"] >= control["accuracy"] - MAX_ACCURACY_DEGRADATION
        and stability["improved"] >= 3
        and (stability["worst_relative_degradation"] or 0.0) <= .10
    )
    return {
        "n": len(future),
        "control": control,
        "challenger": challenger,
        "brier_relative_improvement": None if brier_imp is None else round(brier_imp, 6),
        "stability": stability,
        "pass": pass_gate,
    }


def default_policy() -> Dict[str, Any]:
    return {
        "schema_version": POLICY_SCHEMA,
        "updated_at": None,
        "authority": {
            "scope": "probability_calibration_overlay_only",
            "may_change_evidence": False,
            "may_change_sources": False,
            "may_execute_trades": False,
            "may_change_sizing": False,
            "automatic_promotion_after_prospective_gate": True,
            "automatic_rollback": True,
        },
        "overrides": {},
        "history": [],
    }


def run(state_dir: Path, report_path: Path, policy_path: Path, output_path: Path) -> Dict[str, Any]:
    state = load_json(state_dir / "state.json", {})
    report = load_json(report_path, {})
    previous = load_json(state_dir / "BELIEF_CLOSED_LOOP.json", {})
    policy = load_json(policy_path, default_policy())
    if not isinstance(policy, dict) or policy.get("schema_version") != POLICY_SCHEMA:
        policy = default_policy()
    challengers = dict(previous.get("challengers") or {})
    now = now_z()

    defs = {
        str(x.get("belief_id")): x
        for x in state.get("definitions", []) if isinstance(x, Mapping)
        and "v3_candidate" not in set(x.get("tags") or [])
    }

    events = []
    for belief_id in sorted(defs):
        rows = eligible_rows(state, belief_id)
        ch = challengers.get(belief_id)
        override = (policy.get("overrides") or {}).get(belief_id)

        if ch and ch.get("status") == "promoted" and override and override.get("active"):
            promoted_at = str(ch.get("promoted_at") or override.get("promoted_at") or "")
            post = [r for r in rows if promoted_at and r["forecast_at"] and parse_time(r["forecast_at"]) > parse_time(promoted_at)]
            if len(post) >= MIN_ROLLBACK_N:
                raw_m = metrics([(r["raw_probability"], r["outcome"]) for r in post])
                prod_m = metrics([(r["production_probability"], r["outcome"]) for r in post])
                brier_deg = None if raw_m["brier"] in (None, 0) else (prod_m["brier"] - raw_m["brier"]) / raw_m["brier"]
                rollback = (
                    brier_deg is not None and brier_deg >= ROLLBACK_BRIER_REL_DEGRADATION
                ) or (
                    prod_m["ece"] is not None and raw_m["ece"] is not None
                    and prod_m["ece"] >= raw_m["ece"] + ROLLBACK_ECE_DEGRADATION
                )
                ch["post_promotion_monitor"] = {"n":len(post),"raw":raw_m,"production":prod_m,"brier_relative_degradation":brier_deg}
                if rollback:
                    override["active"] = False
                    override["rolled_back_at"] = now
                    ch["status"] = "rolled_back"
                    ch["rolled_back_at"] = now
                    ch["rediscovery_after_n"] = len(rows) + 20
                    event = {"at":now,"belief_id":belief_id,"event":"AUTO_ROLLBACK","reason":"post_promotion_degradation"}
                    policy.setdefault("history", []).append(event); events.append(event)
            challengers[belief_id] = ch
            continue

        if ch and ch.get("status") in {"prospective_shadow","ready_for_promotion"}:
            ev = prospective_evaluation(rows, str(ch["frozen_at"]), ch["transform"])
            ch["prospective"] = ev
            if ev["pass"]:
                version = f"{belief_id}:cal-overlay:{now}"
                override = {
                    "active": True,
                    "belief_id": belief_id,
                    "version": version,
                    "transform": ch["transform"],
                    "promoted_at": now,
                    "prospective_gate": ev,
                    "raw_control_preserved": True,
                    "rollback": {
                        "minimum_post_promotion_n": MIN_ROLLBACK_N,
                        "brier_relative_degradation": ROLLBACK_BRIER_REL_DEGRADATION,
                        "ece_degradation": ROLLBACK_ECE_DEGRADATION,
                    },
                }
                policy.setdefault("overrides", {})[belief_id] = override
                ch["status"] = "promoted"
                ch["promoted_at"] = now
                ch["production_version"] = version
                event = {"at":now,"belief_id":belief_id,"event":"AUTO_PROMOTION","version":version,"prospective_n":ev["n"]}
                policy.setdefault("history", []).append(event); events.append(event)
            challengers[belief_id] = ch
            continue

        rediscovery_after = int((ch or {}).get("rediscovery_after_n") or 0)
        if len(rows) < rediscovery_after:
            continue
        is_triggered, reasons = triggered(report, belief_id)
        if not is_triggered:
            continue
        fitted = fit_transform(rows)
        if not fitted:
            challengers[belief_id] = {
                "belief_id": belief_id,
                "status": "no_valid_challenger",
                "last_discovery_at": now,
                "last_discovery_n": len(rows),
                "trigger_reasons": reasons,
                "rediscovery_after_n": len(rows) + 20,
            }
            continue
        challengers[belief_id] = {
            "belief_id": belief_id,
            "status": "prospective_shadow",
            "created_at": now,
            "frozen_at": now,
            "trigger_reasons": reasons,
            "transform": fitted["transform"],
            "discovery": {k:v for k,v in fitted.items() if k != "transform"},
            "prospective": prospective_evaluation(rows, now, fitted["transform"]),
            "production_write_authority": False,
        }
        events.append({"at":now,"belief_id":belief_id,"event":"CHALLENGER_FROZEN","transform":fitted["transform"]})

    policy["updated_at"] = now
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now,
        "mode": "governed_closed_loop",
        "authority": {
            "automatic_candidate_creation": True,
            "automatic_prospective_evaluation": True,
            "automatic_model_overlay_promotion": True,
            "automatic_rollback": True,
            "trade_execution_authority": False,
            "evidence_mutation_authority": False,
            "source_mutation_authority": False,
        },
        "gates": {
            "minimum_discovery_n": MIN_DISCOVERY_N,
            "minimum_prospective_n": MIN_PROSPECTIVE_N,
            "promotion_brier_relative_improvement": PROMOTION_BRIER_REL_IMPROVEMENT,
            "max_ece_degradation": MAX_ECE_DEGRADATION,
            "max_accuracy_degradation": MAX_ACCURACY_DEGRADATION,
            "stability_blocks_required": "3_of_4",
            "minimum_post_promotion_n_for_rollback": MIN_ROLLBACK_N,
        },
        "challengers": challengers,
        "active_production_overrides": sorted(
            bid for bid, row in (policy.get("overrides") or {}).items() if isinstance(row, Mapping) and row.get("active")
        ),
        "events_this_run": events,
    }
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--policy", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()
    payload = run(a.state_dir, a.report, a.policy, a.output)
    print(json.dumps({
        "challengers": len(payload["challengers"]),
        "active_production_overrides": len(payload["active_production_overrides"]),
        "events": len(payload["events_this_run"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
