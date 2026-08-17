#!/usr/bin/env python3
"""Calibration analytics for BriefRooms Belief Core v2.

This module is deliberately decision-independent. It measures how frozen
probabilistic beliefs behaved after outcomes became observable. It never
changes priors, evidence reliability, policy, sizing, or trading state.
"""
from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean
from typing import Any, Dict, Iterable, List, Mapping, Sequence

CALIBRATION_BINS = tuple((i / 10.0, (i + 1) / 10.0) for i in range(10))
DIMENSION_MIN_N = 8
RECOMMENDATION_MIN_N = 15
GLOBAL_MIN_N = 30
DRIFT_HALF_MIN_N = 15


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _safe_log_loss(p: float, y: float) -> float:
    p = clamp(p, 1e-9, 1.0 - 1e-9)
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def _bucket_label(low: float, high: float) -> str:
    if high >= 1.0:
        return f"{int(low*100):02d}-100%"
    return f"{int(low*100):02d}-{int(high*100):02d}%"


def _eligible(records: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    return [r for r in records if bool(r.get("calibration_eligible", True))]


def calibration_buckets(records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    rows = _eligible(records)
    out: List[Dict[str, Any]] = []
    for low, high in CALIBRATION_BINS:
        if high >= 1.0:
            bucket = [r for r in rows if low <= float(r["predicted_probability"]) <= high]
        else:
            bucket = [r for r in rows if low <= float(r["predicted_probability"]) < high]
        if not bucket:
            out.append({"bucket": _bucket_label(low, high), "count": 0, "mean_predicted": None,
                        "observed_rate": None, "gap": None, "mean_brier": None})
            continue
        ps = [float(r["predicted_probability"]) for r in bucket]
        ys = [1.0 if bool(r["outcome"]) else 0.0 for r in bucket]
        briers = [float(r["brier_score"]) for r in bucket]
        pbar, ybar = mean(ps), mean(ys)
        gap = pbar - ybar
        diagnosis = "insufficient_sample"
        if len(bucket) >= DIMENSION_MIN_N:
            diagnosis = "overconfident" if gap >= .08 else "underconfident" if gap <= -.08 else "aligned"
        out.append({"bucket": _bucket_label(low, high), "count": len(bucket),
                    "mean_predicted": round(pbar, 6), "observed_rate": round(ybar, 6),
                    "gap": round(gap, 6), "mean_brier": round(mean(briers), 6), "diagnosis": diagnosis})
    return out


def brier_decomposition(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = _eligible(records)
    if not rows:
        return {"reliability": None, "resolution": None, "uncertainty": None,
                "reconstructed_brier": None}
    y_all = [1.0 if bool(r["outcome"]) else 0.0 for r in rows]
    base_rate = mean(y_all)
    reliability = 0.0
    resolution = 0.0
    n = len(rows)
    for b in calibration_buckets(rows):
        if not b["count"]:
            continue
        w = b["count"] / n
        reliability += w * (float(b["mean_predicted"]) - float(b["observed_rate"])) ** 2
        resolution += w * (float(b["observed_rate"]) - base_rate) ** 2
    uncertainty = base_rate * (1.0 - base_rate)
    reconstructed = reliability - resolution + uncertainty
    return {"reliability": round(reliability, 6), "resolution": round(resolution, 6),
            "uncertainty": round(uncertainty, 6), "reconstructed_brier": round(reconstructed, 6)}


def metrics(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = _eligible(records)
    if not rows:
        return {"count": 0, "status": "awaiting_outcomes", "mean_brier": None,
                "mean_log_loss": None, "mean_predicted": None, "observed_rate": None,
                "calibration_bias": None, "ece": None, "mce": None,
                "accuracy_at_50": None, "brier_decomposition": brier_decomposition([])}
    ps = [float(r["predicted_probability"]) for r in rows]
    ys = [1.0 if bool(r["outcome"]) else 0.0 for r in rows]
    bs = [float(r["brier_score"]) for r in rows]
    buckets = calibration_buckets(rows)
    ece = sum((b["count"] / len(rows)) * abs(float(b["gap"])) for b in buckets if b["count"])
    mce = max((abs(float(b["gap"])) for b in buckets if b["count"]), default=0.0)
    status = "measuring"
    if len(rows) < GLOBAL_MIN_N:
        status = "insufficient_sample"
    elif ece <= 0.05:
        status = "well_calibrated"
    elif ece <= 0.10:
        status = "watch"
    else:
        status = "miscalibrated"
    return {
        "count": len(rows), "status": status,
        "mean_brier": round(mean(bs), 6),
        "mean_log_loss": round(mean(_safe_log_loss(p, y) for p, y in zip(ps, ys)), 6),
        "mean_predicted": round(mean(ps), 6), "observed_rate": round(mean(ys), 6),
        "calibration_bias": round(mean(ps) - mean(ys), 6),
        "ece": round(ece, 6), "mce": round(mce, 6),
        "accuracy_at_50": round(mean(1.0 if ((p >= .5) == bool(y)) else 0.0 for p, y in zip(ps, ys)), 6),
        "brier_decomposition": brier_decomposition(rows),
    }


def dimension_report(records: Sequence[Mapping[str, Any]], key: str) -> Dict[str, Any]:
    grouped: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for r in _eligible(records):
        value = r.get(key)
        if value is None or value == "":
            value = "unknown"
        grouped[str(value)].append(r)
    out: Dict[str, Any] = {}
    for value, rows in sorted(grouped.items()):
        m = metrics(rows)
        m["sample_sufficient"] = len(rows) >= DIMENSION_MIN_N
        out[value] = m
    return out


def source_attribution(records: Sequence[Mapping[str, Any]], field: str = "source") -> Dict[str, Any]:
    """Associational source/evidence-type diagnostics, never causal credit.

    Direction is judged against the later binary outcome. Counts distinguish
    independent evidence observations from frozen forecasts so one forecast
    containing several clusters cannot masquerade as a large sample of outcomes.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in _eligible(records):
        outcome = bool(r["outcome"])
        forecast_key = str(r.get("forecast_id") or r.get("verification_id"))
        for e in r.get("evidence_snapshot") or []:
            label = str(e.get(field) or "unknown")
            direction = int(e.get("direction", 0))
            correct = (direction > 0 and outcome) or (direction < 0 and not outcome)
            grouped[label].append({"correct": correct, "mass": float(e.get("effective_mass", 0.0)),
                                   "brier": float(r["brier_score"]), "forecast_key": forecast_key,
                                   "assigned_reliability": float(e.get("reliability", 0.0))})
    out: Dict[str, Any] = {}
    for label, rows in sorted(grouped.items()):
        forecast_count = len({x["forecast_key"] for x in rows})
        total_mass = sum(x["mass"] for x in rows)
        weighted_accuracy = (sum(x["mass"] * (1.0 if x["correct"] else 0.0) for x in rows) / total_mass
                             if total_mass > 1e-12 else mean(1.0 if x["correct"] else 0.0 for x in rows))
        avg_rel = mean(x["assigned_reliability"] for x in rows)
        suggested = None
        if forecast_count >= RECOMMENDATION_MIN_N:
            suggested = round(max(-0.10, min(0.05, (weighted_accuracy - avg_rel) * 0.25)), 6)
        out[label] = {
            "observation_count": len(rows), "forecast_count": forecast_count,
            "direction_accuracy": round(mean(1.0 if x["correct"] else 0.0 for x in rows), 6),
            "mass_weighted_direction_accuracy": round(weighted_accuracy, 6),
            "mean_assigned_reliability": round(avg_rel, 6),
            "suggested_reliability_delta": suggested,
            "mean_effective_mass": round(mean(x["mass"] for x in rows), 6),
            "mean_brier_when_present": round(mean(x["brier"] for x in rows), 6),
            "sample_sufficient": forecast_count >= RECOMMENDATION_MIN_N,
            "attribution_is_associational": True,
        }
    return out


def confidence_diagnostics(records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    rows = _eligible(records)
    out: List[Dict[str, Any]] = []
    for low, high in CALIBRATION_BINS:
        if high >= 1.0:
            b = [r for r in rows if low <= float(r.get("forecast_confidence", 0.0)) <= high]
        else:
            b = [r for r in rows if low <= float(r.get("forecast_confidence", 0.0)) < high]
        out.append({"bucket": _bucket_label(low, high), "count": len(b),
                    "mean_brier": None if not b else round(mean(float(r["brier_score"]) for r in b), 6)})
    return out


def drift_report(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = sorted(_eligible(records), key=lambda r: str(r.get("verified_at", "")))
    if len(rows) < 2 * DRIFT_HALF_MIN_N:
        return {"status": "insufficient_sample", "count": len(rows), "prior": None, "recent": None,
                "brier_delta": None, "bias_delta": None}
    half = min(len(rows) // 2, 50)
    prior_rows, recent_rows = rows[-2 * half:-half], rows[-half:]
    prior, recent = metrics(prior_rows), metrics(recent_rows)
    brier_delta = float(recent["mean_brier"]) - float(prior["mean_brier"])
    bias_delta = abs(float(recent["calibration_bias"])) - abs(float(prior["calibration_bias"]))
    status = "stable"
    if brier_delta >= 0.05 or bias_delta >= 0.05:
        status = "deteriorating"
    elif brier_delta <= -0.03 and bias_delta <= -0.03:
        status = "improving"
    return {"status": status, "count": len(rows), "window_size": half,
            "prior": prior, "recent": recent, "brier_delta": round(brier_delta, 6),
            "bias_delta": round(bias_delta, 6)}


def alternative_group_report(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    sets: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for r in _eligible(records):
        if r.get("alternative_group") and r.get("forecast_set_id"):
            sets[str(r["forecast_set_id"])].append(r)
    valid = []
    for set_id, rows in sets.items():
        if len(rows) < 2 or sum(1 for r in rows if bool(r["outcome"])) != 1:
            continue
        winner = next(r for r in rows if bool(r["outcome"]))
        valid.append({
            "forecast_set_id": set_id,
            "alternative_group": winner.get("alternative_group"),
            "class_count": len(rows),
            "multiclass_brier": sum((float(r["predicted_probability"]) - (1.0 if bool(r["outcome"]) else 0.0)) ** 2 for r in rows),
            "winner_log_loss": _safe_log_loss(float(winner["predicted_probability"]), 1.0),
            "top1_correct": max(rows, key=lambda r: float(r["predicted_probability"]))["belief_id"] == winner["belief_id"],
        })
    if not valid:
        return {"count": 0, "mean_multiclass_brier": None, "mean_winner_log_loss": None, "top1_accuracy": None}
    return {"count": len(valid),
            "mean_multiclass_brier": round(mean(x["multiclass_brier"] for x in valid), 6),
            "mean_winner_log_loss": round(mean(x["winner_log_loss"] for x in valid), 6),
            "top1_accuracy": round(mean(1.0 if x["top1_correct"] else 0.0 for x in valid), 6)}


def recommendations(records: Sequence[Mapping[str, Any]], source_report: Mapping[str, Any],
                    evidence_type_report: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = _eligible(records)
    recs: List[Dict[str, Any]] = []
    overall = metrics(rows)
    if len(rows) >= GLOBAL_MIN_N and overall["calibration_bias"] is not None:
        bias = float(overall["calibration_bias"])
        if bias >= 0.08:
            recs.append({"code": "global_overconfidence", "severity": "warning",
                         "action": "review_probability_mapping",
                         "message": "Prognozy są średnio zbyt wysokie względem częstości realizacji. Nie koryguj automatycznie; przeprowadź kontrolowaną rekalibrację out-of-sample."})
        elif bias <= -0.08:
            recs.append({"code": "global_underconfidence", "severity": "warning",
                         "action": "review_probability_mapping",
                         "message": "Prognozy są średnio zbyt niskie względem częstości realizacji. Nie koryguj automatycznie; przeprowadź kontrolowaną rekalibrację out-of-sample."})
    for source, s in source_report.items():
        if int(s["forecast_count"]) >= RECOMMENDATION_MIN_N and float(s["mass_weighted_direction_accuracy"]) < 0.45:
            recs.append({"code": "source_direction_failure", "severity": "warning", "source": source,
                         "action": "review_source_reliability",
                         "message": "Kierunek evidence z tego źródła jest historycznie słabszy niż losowy w wystarczającej próbie. To sygnał do audytu, nie automatycznej zmiany wagi."})
    for etype, s in evidence_type_report.items():
        if int(s["forecast_count"]) >= RECOMMENDATION_MIN_N and float(s["mass_weighted_direction_accuracy"]) < 0.45:
            recs.append({"code": "evidence_type_failure", "severity": "warning", "evidence_type": etype,
                         "action": "review_evidence_type",
                         "message": "Ten typ evidence ma słabą zgodność kierunkową z późniejszym outcome. Wymaga przeglądu definicji/ekstrakcji."})
    by_regime = dimension_report(rows, "regime")
    for regime, m in by_regime.items():
        if int(m["count"]) >= DIMENSION_MIN_N and m.get("mean_brier") is not None and float(m["mean_brier"]) >= 0.30:
            recs.append({"code": "regime_performance_break", "severity": "warning", "regime": regime,
                         "action": "review_regime_mapping",
                         "message": "Beliefs are materially less accurate in this regime; inspect evidence semantics and priors before changing weights."})
    return recs


def build_calibration_report(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = list(records)
    source = source_attribution(rows, "source")
    evidence_type = source_attribution(rows, "evidence_type")
    eligible_count = len(_eligible(rows))
    return {
        "count_all_verifications": len(rows),
        "count_calibration_eligible": eligible_count,
        "overall": metrics(rows),
        "reliability_curve": calibration_buckets(rows),
        "by_domain": dimension_report(rows, "domain"),
        "by_entity": dimension_report(rows, "entity"),
        "by_regime": dimension_report(rows, "regime"),
        "by_horizon": dimension_report(rows, "horizon_bucket"),
        "by_belief": dimension_report(rows, "belief_id"),
        "by_outcome_source": dimension_report(rows, "outcome_source"),
        "alternative_groups": alternative_group_report(rows),
        "source_performance": source,
        "evidence_type_performance": evidence_type,
        "confidence_diagnostics": confidence_diagnostics(rows),
        "drift": drift_report(rows),
        "recommendations": recommendations(rows, source, evidence_type),
        "automatic_tuning_enabled": False,
        "note": "Calibration Engine mierzy jakość i generuje audytowalne rekomendacje. Nie zmienia automatycznie priors, reliability ani policy.",
    }
