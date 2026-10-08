#!/usr/bin/env python3
"""BriefRooms L3 P1: independent-event Hypothesis Utility Engine (read only).

Predictive utility: first immutable frozen forecast per objectively identical
outcome contract, with prequential (point-in-time) base-rate benchmark.
Research utility: ONLY settled L3-A intent->Evidence->Verification lineage,
with a distinct-event denominator and explicit missing-cost telemetry.
No Evidence, belief, forecast, policy or production mutation.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Mapping

from belief_calibration import metrics as calibration_metrics
from forecast_event_identity import canonical_event_rows, identity

SCHEMA = "briefrooms-hypothesis-utility-v1"
MIN_PREDICTIVE_EVENTS = 30
MIN_DISTINCT_TARGET_DATES = 10
MIN_CHALLENGER_EVENTS = 50
MIN_CHALLENGER_TARGET_DATES = 15
MIN_RESEARCH_EVENTS = 10
DEFAULT_VERSION = "1"


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _number(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _version(f: Mapping[str, Any]) -> str:
    return identity(f)["hypothesis_version"]


def _key(belief_id: str, version: str) -> str:
    return belief_id + "@" + version


def _predictive(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = sorted(rows, key=lambda r: (r["forecast_dt"], r["event_id"]))
    n = len(rows)
    if not n:
        return {
            "independent_events": 0, "distinct_target_dates": 0,
            "status": "COLLECTING", "recommendation": "COLLECT_MORE",
            "reasons": ["no_independent_verified_events"],
            "brier": None, "ece": None, "brier_skill_vs_50_50": None,
            "brier_gain_vs_prequential_base_rate": None,
            "brier_gain_95pct_approx_interval": None,
            "horizons": {},
        }

    # IMPORTANT: a base rate is updated only after a past event was VERIFIABLY
    # known at the moment this next forecast was frozen. This blocks hindsight.
    observable = sorted(rows, key=lambda x: (x["verified_dt"], x["event_id"]))
    yes = 0
    observed = 0
    pointer = 0
    paired_gains = []
    by_horizon: dict[str, list[dict[str, Any]]] = defaultdict(list)
    target_dates: set[str] = set()
    for row in rows:
        while pointer < n and observable[pointer]["verified_dt"] <= row["forecast_dt"]:
            if observable[pointer]["event_id"] != row["event_id"]:
                yes += int(observable[pointer]["y"])
                observed += 1
            pointer += 1
        prior_p = (yes + 0.5) / (observed + 1.0)  # Jeffreys prior, no future labels
        y = row["y"]
        paired_gains.append((prior_p - y) ** 2 - row["brier"])
        target_dates.add(row["target_dt"].date().isoformat())
        by_horizon[row["horizon_bucket"]].append(row)

    means = calibration_metrics([
        {"predicted_probability": r["p"], "outcome": bool(r["y"]),
         "brier_score": r["brier"], "forecast_confidence": r["confidence"],
         "calibration_eligible": True}
        for r in rows
    ])
    average_brier = mean(r["brier"] for r in rows)
    gain = mean(paired_gains)
    interval = None
    if n >= 2:
        half = 1.96 * stdev(paired_gains) / math.sqrt(n)
        interval = [round(max(-1.0, gain - half), 6), round(min(1.0, gain + half), 6)]

    horizon_stats = {
        h: {"n": len(group), "brier": round(mean(r["brier"] for r in group), 6),
            "target_dates": len({r["target_dt"].date().isoformat() for r in group})}
        for h, group in sorted(by_horizon.items())
    }
    dates = len(target_dates)
    reasons = []
    if n < MIN_PREDICTIVE_EVENTS or dates < MIN_DISTINCT_TARGET_DATES:
        status = "COLLECTING"
        recommendation = "COLLECT_MORE"
        reasons.append("independent_event_or_target_date_sample_below_gate")
    else:
        # Diagnostics, never an automatic decommission or calibration change.
        ece = means.get("ece")
        if gain < -0.03 and n >= MIN_CHALLENGER_EVENTS and dates >= MIN_CHALLENGER_TARGET_DATES:
            status, recommendation = "CHALLENGER", "CHALLENGER_RECOMMENDED"
            reasons.append("negative_prequential_skill_prospective_challenger_only")
        elif gain < 0 or (ece is not None and ece > 0.10):
            status, recommendation = "REVIEW", "REVIEW"
            reasons.append("negative_prequential_gain" if gain < 0 else "ece_above_10pp")
        else:
            status, recommendation = "ACTIVE", "MAINTAIN"
            reasons.append("no_material_adverse_signal")
    if len(horizon_stats) > 1:
        reasons.append("mixed_horizon_aggregate_diagnostic_only")
    return {
        "independent_events": n,
        "distinct_target_dates": dates,
        "raw_data_scope": "real_verifications_after_first_frozen_forecast_per_event",
        "status": status, "recommendation": recommendation, "reasons": reasons,
        "brier": round(average_brier, 6),
        "ece": means.get("ece"),
        "calibration_bias": means.get("calibration_bias"),
        "brier_skill_vs_50_50": round(1 - average_brier / .25, 6),
        "brier_gain_vs_prequential_base_rate": round(gain, 6),
        "brier_gain_95pct_approx_interval": interval,
        "confidence_interval_caveat": "descriptive_normal_approximation_correlated_market_outcomes_not_accounted_for",
        "benchmark": "Jeffreys_beta_0.5_0.5_prequential_only_prior_verified_events",
        "horizons": horizon_stats,
    }


def _research(belief_id: str, version: str, rows: list[Mapping[str, Any]],
              verifications: Mapping[str, Mapping[str, Any]],
              forecasts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    relevant = [x for x in rows if str(x.get("belief_id") or "") == belief_id
                and str(x.get("hypothesis_version") or DEFAULT_VERSION) == version]
    attempts = {str(r.get("attempt_id") or ""): r for r in relevant if r.get("attempt_id")}
    unique = list(attempts.values())
    evidence_positive = sum(bool(r.get("evidence_ids")) for r in unique)
    by_event: dict[str, list[tuple[str, float, float | None]]] = defaultdict(list)
    unvalidated = 0
    observed_costs = []
    for r in unique:
        fm = r.get("future_metrics") or {}
        if not isinstance(fm, Mapping):
            continue
        vid = str(fm.get("verification_id") or "")
        fid = str(r.get("forecast_id") or "")
        if not vid or not fid:
            continue
        v = verifications.get(vid)
        f = forecasts.get(fid)
        if not v or not f or str(v.get("forecast_id") or "") != fid:
            unvalidated += 1
            continue
        if not bool(v.get("calibration_eligible", True)) or not isinstance(v.get("outcome"), bool):
            unvalidated += 1
            continue
        gain = _number(fm.get("brier_gain_vs_pre_research"))
        pre = _number(r.get("p_before"))
        loss = _number(v.get("brier_score"))
        if gain is None or pre is None or loss is None:
            unvalidated += 1
            continue
        correct_gain = (pre - float(v["outcome"])) ** 2 - loss
        if abs(correct_gain - gain) > 2e-5:
            unvalidated += 1
            continue
        # Research may touch multiple revisions of one event. Count that event
        # only once in the aggregate (earliest valid registered attempt).
        eid = identity(f)["event_id"]
        cost = _number(r.get("measured_cost_usd"))
        if cost is not None and cost < 0:
            cost = None
        by_event[eid].append((str(r.get("attempted_at") or ""),
                              correct_gain, cost))
    independent = [sorted(group, key=lambda x: x[0])[0] for group in by_event.values()]
    independent.sort(key=lambda x: x[0])
    gains = [r[1] for r in independent]
    costs = [r[2] for r in independent]
    complete_cost = bool(independent) and all(c is not None for c in costs)
    total_cost = sum(c for c in costs if c is not None) if complete_cost else None
    avg_gain = mean(gains) if gains else None
    if len(gains) < MIN_RESEARCH_EVENTS:
        status = "INSUFFICIENT_SETTLEMENTS"
    elif avg_gain is not None and avg_gain < 0:
        status = "REVIEW"
    else:
        status = "POSITIVE_ASSOCIATION"
    return {
        "status": status,
        "research_attempts_observed": len(unique),
        "attempts_with_evidence": evidence_positive,
        "evidence_gain_rate": round(evidence_positive / len(unique), 6) if unique else None,
        "settled_independent_events": len(independent),
        "unvalidated_settlement_records": unvalidated,
        "mean_brier_gain_vs_pre_research": None if avg_gain is None else round(avg_gain, 6),
        "measured_cost_usd": None if total_cost is None else round(total_cost, 6),
        "cost_data_status": "COMPLETE_MEASURED" if complete_cost else "NOT_INSTRUMENTED_OR_INCOMPLETE",
        "gain_per_usd": None if total_cost is None or total_cost <= 0 or avg_gain is None
                        else round(sum(gains) / total_cost, 6),
        "attribution": "ASSOCIATION_ONLY_not_causal_contribution",
        "source_scope": "bounded_L3A_EXPERIENCE_STATE_no_historical_backfill",
    }


def build(state: Mapping[str, Any], experience: Mapping[str, Any] | None = None,
          *, now: str | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if experience is not None and experience.get("schema_version") != "briefrooms-l3a-experience-state-v1":
        raise ValueError("invalid L3-A experience schema")
    raw_forecasts = state.get("forecasts") or []
    raw_verifications = state.get("verifications") or []
    definitions = state.get("definitions") or []
    if isinstance(raw_forecasts, dict):
        raw_forecasts = list(raw_forecasts.values())
    if isinstance(raw_verifications, dict):
        raw_verifications = list(raw_verifications.values())
    if isinstance(definitions, dict):
        definitions = list(definitions.values())
    forecasts = {str(x["forecast_id"]): x for x in raw_forecasts
                 if isinstance(x, Mapping) and x.get("forecast_id")}
    verification_by_id = {str(x["verification_id"]): x for x in raw_verifications
                          if isinstance(x, Mapping) and x.get("verification_id")}
    by_forecast: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for v in raw_verifications:
        if isinstance(v, Mapping) and v.get("forecast_id"):
            by_forecast[str(v["forecast_id"])].append(v)

    candidates = []
    rejected = defaultdict(int)
    for fid, f in forecasts.items():
        attached = by_forecast.get(fid, [])
        if len(attached) > 1:
            rejected["multiple_verifications_for_forecast"] += 1
            continue
        if not attached:
            continue
        v = attached[0]
        if not bool(v.get("calibration_eligible", False)):
            rejected["not_calibration_eligible"] += 1
            continue
        if not isinstance(v.get("outcome"), bool):
            rejected["invalid_binary_outcome"] += 1
            continue
        p = _number(f.get("predicted_probability"))
        timestamp = _timestamp(f.get("forecast_at"))
        target = _timestamp(f.get("target_at"))
        verified = _timestamp(v.get("verified_at"))
        if p is None or not 0 <= p <= 1 or not timestamp or not target or not verified:
            rejected["invalid_probability_or_time"] += 1
            continue
        if not timestamp < target or verified < target:
            rejected["nonprospective_or_early_settlement"] += 1
            continue
        ids = identity(f)
        if str(v.get("belief_id") or "") != ids["hypothesis_id"]:
            rejected["verification_belief_mismatch"] += 1
            continue
        y = float(v["outcome"])
        brier = (p - y) ** 2
        candidates.append({
            "f": f, "p": p, "y": y, "brier": brier,
            "event_id": ids["event_id"], "forecast_dt": timestamp,
            "target_dt": target, "verified_dt": verified,
            "horizon_bucket": str(v.get("horizon_bucket") or "unknown"),
            "confidence": _number(f.get("forecast_confidence")) or 0.0,
            "hypothesis_id": ids["hypothesis_id"],
            "hypothesis_version": ids["hypothesis_version"],
        })

    chosen, dedupe = canonical_event_rows(candidates)
    definitions_by_id = {str(d["belief_id"]): d for d in definitions
                         if isinstance(d, Mapping) and d.get("belief_id")}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in chosen:
        grouped[_key(row["hypothesis_id"], row["hypothesis_version"])].append(row)
    for bid, d in definitions_by_id.items():
        grouped.setdefault(_key(bid, str(d.get("hypothesis_version") or DEFAULT_VERSION)), [])

    experience_rows = (experience or {}).get("records") or []
    if not isinstance(experience_rows, list):
        raise ValueError("L3-A experience records must be list")
    versions: dict[str, dict[str, Any]] = {}
    for key, rows in sorted(grouped.items()):
        if rows:
            bid, version = rows[0]["hypothesis_id"], rows[0]["hypothesis_version"]
        else:
            bid, version = key.rsplit("@", 1)
        predictive = _predictive(rows)
        research = _research(bid, version, experience_rows, verification_by_id, forecasts)
        reasons = list(predictive["reasons"])
        # Observational research can trigger review, NEVER automatic retirement.
        if research["status"] == "REVIEW" and predictive["status"] == "ACTIVE":
            recommendation, status = "REVIEW", "REVIEW"
            reasons.append("negative_research_gain_diagnostic")
        else:
            recommendation, status = predictive["recommendation"], predictive["status"]
        versions[key] = {
            "hypothesis_id": bid, "hypothesis_version": version,
            "claim": definitions_by_id.get(bid, {}).get("claim"),
            "lifecycle_status": status, "recommendation": recommendation,
            "reasons": reasons, "predictive_utility": predictive,
            "research_utility": research,
            "decision_authority": False, "automatic_retirement": False,
        }

    # Display one latest version without mixing its measurements with the old.
    latest = {}
    for v in versions.values():
        bid = v["hypothesis_id"]
        if bid not in latest or v["hypothesis_version"] > latest[bid]["hypothesis_version"]:
            latest[bid] = v
    status_count: dict[str, int] = defaultdict(int)
    for v in latest.values():
        status_count[v["lifecycle_status"]] += 1
    return {
        "schema_version": SCHEMA, "generated_at": now, "mode": "shadow_read_only",
        "authority": {
            "belief_probability_override": False,
            "source_evidence_mutation": False,
            "frozen_forecast_mutation": False,
            "automatic_retirement": False,
            "automatic_promotion": False,
            "trade_execution": False,
            "production_writeback": False,
        },
        "policy": {
            "min_predictive_events": MIN_PREDICTIVE_EVENTS,
            "min_distinct_target_dates": MIN_DISTINCT_TARGET_DATES,
            "min_challenger_events": MIN_CHALLENGER_EVENTS,
            "min_challenger_target_dates": MIN_CHALLENGER_TARGET_DATES,
            "min_research_settlements": MIN_RESEARCH_EVENTS,
            "revision_selection": "first_frozen_forecast_per_immutable_event",
            "cost_rule": "measured_cost_usd_only_no_proxy_imputation",
        },
        "source": {
            "forecast_count": len(forecasts),
            "verification_count": len(raw_verifications),
            "matched_eligible_forecasts": len(candidates),
            "independent_resolved_events": len(chosen),
            "revision_diagnostics": dedupe,
            "rejected": dict(sorted(rejected.items())),
            "l3a_experience_available": experience is not None,
            "l3a_experience_records": len(experience_rows),
        },
        "summary": {
            "hypotheses": len(latest), "hypothesis_versions": len(versions),
            "lifecycle_status_counts": dict(sorted(status_count.items())),
            "retired": 0, "auto_promoted": 0,
        },
        "hypotheses": dict(sorted(latest.items())),
        "hypothesis_versions": versions,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    with (args.state_dir / "state.json").open(encoding="utf-8") as fh:
        state = json.load(fh)
    ep = args.state_dir / "L3A_EXPERIENCE_STATE.json"
    experience = json.loads(ep.read_text(encoding="utf-8")) if ep.exists() else None
    report = build(state, experience)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "source": report["source"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
