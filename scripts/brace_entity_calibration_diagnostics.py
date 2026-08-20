#!/usr/bin/env python3
"""PR #16 — Entity Calibration & Diagnostics Foundation.

Read-only diagnostics over prospective PR15 Entity forecast/verifications.
No Belief updates, no BRACE influence, no WITH/WITHOUT bridge, no promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

MODE = "research_shadow"
SCHEMA_VERSION = "brace-entity-calibration-diagnostics-v1"
REPORT_VERSION = "brace-entity-calibration-diagnostics-report-v1"
CONTRACT_VERSION = "entity-calibration-diagnostics-contract-v1"
STATE_FILENAME = "ENTITY_CALIBRATION_DIAGNOSTICS_STATE.json"
REPORT_FILENAME = "BRACE_ENTITY_CALIBRATION_DIAGNOSTICS_REPORT.json"

PR15_CONTRACT_VERSION = "entity-belief-forecast-contract-v1"
EXPECTED_OUTCOME_SOURCE = "PR14 deterministic entity interpretation"
FIXED_PROBABILITY_BINS: Tuple[Tuple[float, float], ...] = tuple(
    (i / 10.0, (i + 1) / 10.0) for i in range(10)
)
MIN_SERIAL_N = 4
MIN_CLUSTER_GROUPS = 3
MIN_DRIFT_HALF = 5


def safety_controls() -> Dict[str, bool]:
    return {
        "active_decision_influence": False,
        "score_change": False,
        "candidate_ranking_change": False,
        "target_exposure_change": False,
        "sizing_change": False,
        "veto": False,
        "direction_reversal": False,
        "forced_exit": False,
        "trade_execution": False,
        "policy_output": False,
        "automatic_tuning": False,
        "bounded_influence": False,
        "historical_belief_backfill": False,
        "historical_forecast_backfill": False,
        "with_without_bridge": False,
        "promotion": False,
    }


def capabilities() -> Dict[str, bool]:
    return {
        "pr15_calibration_input_enabled": True,
        "fixed_bin_calibration_diagnostics_enabled": True,
        "brier_logloss_diagnostics_enabled": True,
        "serial_dependency_diagnostics_enabled": True,
        "entity_cluster_dependency_diagnostics_enabled": True,
        "sector_cluster_dependency_diagnostics_enabled": True,
        "reporting_season_dependency_diagnostics_enabled": True,
        "forecast_window_overlap_diagnostics_enabled": True,
        "concentration_diagnostics_enabled": True,
        "descriptive_drift_diagnostics_enabled": True,
        "provenance_quality_diagnostics_enabled": True,
        "broad_market_regime_context_frozen_at_forecast_enabled": False,
        "sector_factor_regime_context_frozen_at_forecast_enabled": False,
        "with_without_bridge_enabled": False,
        "promotion_gate_enabled": False,
    }


def promotion_evidence_standard() -> Dict[str, Any]:
    return {
        "with_without_required": True,
        "paired_prospective_counterfactual_required": True,
        "effective_n_required": True,
        "effective_n_threshold_defined_here": False,
        "stable_uplift_required": True,
        "multi_regime_robustness_required": True,
        "concentration_check_required": True,
        "drawdown_not_materially_worse_required": True,
        "tail_risk_not_materially_worse_required": True,
        "belief_calibration_required": True,
        "drift_check_required": True,
        "data_quality_and_provenance_required": True,
        "anti_hindsight_required": True,
        "shadow_runtime_stability_required": True,
        "automatic_promotion": False,
        "review_output_only": "ELIGIBLE_FOR_PROMOTION_REVIEW",
    }


def _assert_safety() -> None:
    bad = [k for k, v in safety_controls().items() if v is not False]
    if bad:
        raise RuntimeError("PR16 zero-influence invariant violated: " + ",".join(bad))


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("empty timestamp")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def empty_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "contract_version": CONTRACT_VERSION,
        "first_run_at": None,
        "last_run_at": None,
        "last_source_fingerprint": None,
        "seen_verification_ids": [],
        "diagnostic_snapshots": [],
    }


def _belief_parts(belief_id: str) -> Tuple[str, str]:
    text = str(belief_id or "")
    if not text.startswith("entity."):
        return "", ""
    body = text[len("entity."):]
    if "." not in body:
        return "", ""
    entity, dimension = body.rsplit(".", 1)
    return entity, dimension


def _quarter_label(value: str) -> str:
    dt = parse_time(value)
    return f"{dt.year}-Q{((dt.month - 1) // 3) + 1}"


def _safe_log_loss(probability: float, outcome: bool) -> float:
    p = min(1.0 - 1e-9, max(1e-9, float(probability)))
    y = 1.0 if outcome else 0.0
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def _wilson(successes: int, n: int, z: float = 1.959963984540054) -> Dict[str, Optional[float]]:
    if n <= 0:
        return {"low": None, "high": None}
    phat = successes / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2.0 * n)) / denom
    half = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * n)) / n) / denom
    return {"low": max(0.0, centre - half), "high": min(1.0, centre + half)}


def _fixed_bin_rows(rows: Sequence[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    bins: List[Dict[str, Any]] = []
    n_total = len(rows)
    ece = 0.0
    for idx, (low, high) in enumerate(FIXED_PROBABILITY_BINS):
        selected = []
        for row in rows:
            p = float(row["predicted_probability"])
            if (low <= p < high) or (idx == len(FIXED_PROBABILITY_BINS) - 1 and p == 1.0):
                selected.append(row)
        count = len(selected)
        if count:
            mean_p = sum(float(x["predicted_probability"]) for x in selected) / count
            successes = sum(1 for x in selected if bool(x["outcome"]))
            rate = successes / count
            gap = rate - mean_p
            ece += (count / n_total) * abs(gap)
            interval = _wilson(successes, count)
        else:
            mean_p = rate = gap = None
            interval = {"low": None, "high": None}
        bins.append({
            "low": low,
            "high": high,
            "count": count,
            "mean_probability": mean_p,
            "outcome_rate": rate,
            "calibration_gap_outcome_minus_probability": gap,
            "outcome_rate_wilson95": interval,
        })
    return bins, (ece if n_total else None)


def calibration_metrics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        bins, ece = _fixed_bin_rows(rows)
        return {
            "n": 0,
            "mean_probability": None,
            "outcome_rate": None,
            "outcome_rate_wilson95": {"low": None, "high": None},
            "calibration_gap_outcome_minus_probability": None,
            "brier_score": None,
            "log_loss": None,
            "hit_rate_at_0_5": None,
            "brier_climatology_baseline": None,
            "brier_skill_vs_in_sample_climatology": None,
            "expected_calibration_error_fixed_deciles": ece,
            "fixed_probability_bins": bins,
        }
    probs = [float(r["predicted_probability"]) for r in rows]
    outcomes = [1.0 if bool(r["outcome"]) else 0.0 for r in rows]
    mean_p = sum(probs) / n
    outcome_rate = sum(outcomes) / n
    brier = sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / n
    logloss = sum(_safe_log_loss(p, bool(y)) for p, y in zip(probs, outcomes)) / n
    hits = sum(1 for p, y in zip(probs, outcomes) if (p >= 0.5) == bool(y))
    baseline = outcome_rate * (1.0 - outcome_rate)
    skill = None if baseline <= 0 else 1.0 - brier / baseline
    bins, ece = _fixed_bin_rows(rows)
    successes = int(sum(outcomes))
    return {
        "n": n,
        "mean_probability": mean_p,
        "outcome_rate": outcome_rate,
        "outcome_rate_wilson95": _wilson(successes, n),
        "calibration_gap_outcome_minus_probability": outcome_rate - mean_p,
        "brier_score": brier,
        "log_loss": logloss,
        "hit_rate_at_0_5": hits / n,
        "brier_climatology_baseline": baseline,
        "brier_skill_vs_in_sample_climatology": skill,
        "expected_calibration_error_fixed_deciles": ece,
        "fixed_probability_bins": bins,
    }


def _correlation(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    if len(x) != len(y) or len(x) < 2:
        return None
    mx, my = sum(x) / len(x), sum(y) / len(y)
    dx = [v - mx for v in x]
    dy = [v - my for v in y]
    vx = sum(v * v for v in dx)
    vy = sum(v * v for v in dy)
    if vx <= 1e-18 or vy <= 1e-18:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / math.sqrt(vx * vy)


def serial_effective_n(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    ordered = sorted(rows, key=lambda r: (r["verified_at"], r["verification_id"]))
    n = len(ordered)
    if n < MIN_SERIAL_N:
        return {"status": f"not_estimable_n_lt_{MIN_SERIAL_N}", "raw_n": n, "lag1_residual_rho": None, "effective_n": None}
    residuals = [(1.0 if bool(r["outcome"]) else 0.0) - float(r["predicted_probability"]) for r in ordered]
    rho = _correlation(residuals[:-1], residuals[1:])
    if rho is None:
        return {"status": "not_estimable_zero_variance_or_short_series", "raw_n": n, "lag1_residual_rho": None, "effective_n": None}
    clipped = max(-0.80, min(0.80, rho))
    ess = n * (1.0 - clipped) / (1.0 + clipped)
    ess = max(1.0, min(float(n), ess))
    return {
        "status": "ok",
        "raw_n": n,
        "lag1_residual_rho": rho,
        "lag1_residual_rho_used": clipped,
        "effective_n": ess,
    }


def cluster_icc_effective_n(rows: Sequence[Mapping[str, Any]], cluster_key: str) -> Dict[str, Any]:
    groups: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        key = str(r.get(cluster_key) or "UNKNOWN")
        residual = (1.0 if bool(r["outcome"]) else 0.0) - float(r["predicted_probability"])
        groups[key].append(residual)
    n = sum(len(v) for v in groups.values())
    repeat_groups = sum(1 for v in groups.values() if len(v) >= 2)
    if len(groups) < MIN_CLUSTER_GROUPS or repeat_groups < 2 or n <= len(groups):
        return {
            "status": "not_estimable_insufficient_independent_groups_or_repeats",
            "cluster_key": cluster_key,
            "raw_n": n,
            "cluster_count": len(groups),
            "repeat_cluster_count": repeat_groups,
            "icc_raw": None,
            "icc_used": None,
            "effective_n": None,
        }
    grand = sum(sum(v) for v in groups.values()) / n
    ssb = 0.0
    ssw = 0.0
    sizes = []
    for values in groups.values():
        m = sum(values) / len(values)
        sizes.append(len(values))
        ssb += len(values) * (m - grand) ** 2
        ssw += sum((x - m) ** 2 for x in values)
    k = len(groups)
    dfb = k - 1
    dfw = n - k
    msb = ssb / dfb
    msw = ssw / dfw if dfw > 0 else 0.0
    n0 = (n - sum(s * s for s in sizes) / n) / dfb
    denom = msb + (n0 - 1.0) * msw
    if denom <= 1e-18:
        return {
            "status": "not_estimable_zero_variance",
            "cluster_key": cluster_key,
            "raw_n": n,
            "cluster_count": k,
            "repeat_cluster_count": repeat_groups,
            "icc_raw": None,
            "icc_used": None,
            "effective_n": None,
        }
    icc_raw = (msb - msw) / denom
    icc_used = max(0.0, min(0.95, icc_raw))
    design_effect = 1.0 + max(0.0, n0 - 1.0) * icc_used
    ess = max(1.0, min(float(n), n / design_effect))
    return {
        "status": "ok",
        "cluster_key": cluster_key,
        "raw_n": n,
        "cluster_count": k,
        "repeat_cluster_count": repeat_groups,
        "effective_average_cluster_size": n0,
        "max_cluster_size": max(sizes),
        "icc_raw": icc_raw,
        "icc_used": icc_used,
        "design_effect": design_effect,
        "effective_n": ess,
    }


def overlap_diagnostics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    intervals = sorted(
        [(parse_time(str(r["forecast_at"])), parse_time(str(r["target_at"])), str(r["forecast_id"])) for r in rows],
        key=lambda x: (x[0], x[1], x[2]),
    )
    n = len(intervals)
    if not n:
        return {
            "raw_n": 0,
            "overlapping_pair_fraction": None,
            "max_concurrent_forecast_windows": 0,
            "max_non_overlapping_window_count": 0,
            "effective_n_cap_from_non_overlapping_windows": None,
        }
    overlapping = 0
    pairs = n * (n - 1) // 2
    for i in range(n):
        a0, a1, _ = intervals[i]
        for j in range(i + 1, n):
            b0, b1, _ = intervals[j]
            if max(a0, b0) < min(a1, b1):
                overlapping += 1
    events = []
    for start, end, _ in intervals:
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda x: (x[0], x[1]))
    current = maximum = 0
    for _, delta in events:
        current += delta
        maximum = max(maximum, current)
    selected = 0
    last_end = None
    for start, end, _ in sorted(intervals, key=lambda x: (x[1], x[0], x[2])):
        if last_end is None or start >= last_end:
            selected += 1
            last_end = end
    return {
        "raw_n": n,
        "overlapping_pair_fraction": (overlapping / pairs) if pairs else 0.0,
        "overlapping_pairs": overlapping,
        "total_pairs": pairs,
        "max_concurrent_forecast_windows": maximum,
        "max_non_overlapping_window_count": selected,
        "effective_n_cap_from_non_overlapping_windows": float(selected),
    }


def concentration(rows: Sequence[Mapping[str, Any]], key: str) -> Dict[str, Any]:
    n = len(rows)
    counts = Counter(str(r.get(key) or "UNKNOWN") for r in rows)
    if not n:
        return {"key": key, "n": 0, "category_count": 0, "hhi": None, "effective_category_count": None, "max_share": None, "top": []}
    shares = {k: v / n for k, v in counts.items()}
    hhi = sum(s * s for s in shares.values())
    top = [
        {"value": k, "count": counts[k], "share": shares[k]}
        for k in sorted(counts, key=lambda k: (-counts[k], k))[:5]
    ]
    return {
        "key": key,
        "n": n,
        "category_count": len(counts),
        "hhi": hhi,
        "effective_category_count": (1.0 / hhi) if hhi > 0 else None,
        "max_share": max(shares.values()),
        "top": top,
        "status": "descriptive_no_frozen_promotion_threshold",
    }


def _slice_metrics(rows: Sequence[Mapping[str, Any]], key: str) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "UNKNOWN")].append(row)
    out = []
    for value in sorted(groups):
        metrics = calibration_metrics(groups[value])
        out.append({
            key: value,
            "n": metrics["n"],
            "brier_score": metrics["brier_score"],
            "log_loss": metrics["log_loss"],
            "mean_probability": metrics["mean_probability"],
            "outcome_rate": metrics["outcome_rate"],
            "calibration_gap_outcome_minus_probability": metrics["calibration_gap_outcome_minus_probability"],
            "hit_rate_at_0_5": metrics["hit_rate_at_0_5"],
        })
    return out


def drift_diagnostics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    ordered = sorted(rows, key=lambda r: (r["verified_at"], r["verification_id"]))
    if len(ordered) < 2 * MIN_DRIFT_HALF:
        return {
            "status": f"not_available_need_at_least_{2 * MIN_DRIFT_HALF}_verified_for_descriptive_split",
            "promotion_drift_ok": None,
            "threshold_defined_here": False,
        }
    split = len(ordered) // 2
    left = ordered[:split]
    right = ordered[split:]
    if len(left) < MIN_DRIFT_HALF or len(right) < MIN_DRIFT_HALF:
        return {"status": "not_available_unbalanced_split", "promotion_drift_ok": None, "threshold_defined_here": False}
    before = calibration_metrics(left)
    recent = calibration_metrics(right)
    return {
        "status": "available_descriptive_only",
        "threshold_defined_here": False,
        "promotion_drift_ok": None,
        "prior_n": len(left),
        "recent_n": len(right),
        "prior_brier": before["brier_score"],
        "recent_brier": recent["brier_score"],
        "brier_change_recent_minus_prior": recent["brier_score"] - before["brier_score"],
        "prior_log_loss": before["log_loss"],
        "recent_log_loss": recent["log_loss"],
        "log_loss_change_recent_minus_prior": recent["log_loss"] - before["log_loss"],
        "prior_calibration_gap": before["calibration_gap_outcome_minus_probability"],
        "recent_calibration_gap": recent["calibration_gap_outcome_minus_probability"],
    }


def _validate_source(core: Mapping[str, Any], runtime: Mapping[str, Any], pr15_report: Mapping[str, Any]) -> None:
    if str(core.get("mode") or "") != "shadow":
        raise ValueError("PR16 requires Belief Core shadow state")
    if int(core.get("schema_version") or 0) != 2:
        raise ValueError("PR16 requires Belief Core schema v2")
    if str(runtime.get("mode") or "") != MODE:
        raise ValueError("PR16 requires PR15 research_shadow runtime")
    if str(runtime.get("contract_version") or "") != PR15_CONTRACT_VERSION:
        raise ValueError("PR16 requires reviewed PR15 forecast contract")
    if str(pr15_report.get("mode") or "") != MODE:
        raise ValueError("PR16 requires PR15 research_shadow report")
    if str(pr15_report.get("contract_version") or "") != PR15_CONTRACT_VERSION:
        raise ValueError("PR16 requires reviewed PR15 report contract")
    boundary = pr15_report.get("state_boundary") or {}
    if boundary.get("entity_bridge_enabled") is not False or boundary.get("with_without_bridge_enabled") is not False:
        raise ValueError("PR16 refuses PR15 input with an enabled Entity/Engine bridge")


def _closure_map(runtime: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    raw = runtime.get("forecast_closures") or {}
    if isinstance(raw, Mapping):
        return {str(k): dict(v) for k, v in raw.items() if isinstance(v, Mapping)}
    return {}


def collect_calibration_rows(
    core: Mapping[str, Any],
    runtime: Mapping[str, Any],
    *,
    as_of: datetime,
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    forecasts = {
        str(f.get("forecast_id")): dict(f)
        for f in (core.get("forecasts") or [])
        if isinstance(f, Mapping) and f.get("forecast_id")
    }
    closures = _closure_map(runtime)
    issues: Dict[str, List[Dict[str, Any]]] = {"critical": [], "warning": []}
    rows: List[Dict[str, Any]] = []
    seen_verification_ids = set()
    for raw in core.get("verifications") or []:
        if not isinstance(raw, Mapping):
            continue
        verification_id = str(raw.get("verification_id") or "")
        if verification_id in seen_verification_ids:
            issues["critical"].append({"code": "duplicate_verification_id", "verification_id": verification_id})
            continue
        seen_verification_ids.add(verification_id)
        if raw.get("calibration_eligible") is not True:
            continue
        if raw.get("legacy") is True:
            issues["critical"].append({"code": "legacy_verification_marked_calibration_eligible", "verification_id": verification_id})
            continue
        if str(raw.get("outcome_source") or "") != EXPECTED_OUTCOME_SOURCE:
            issues["critical"].append({"code": "unexpected_outcome_source", "verification_id": verification_id, "outcome_source": raw.get("outcome_source")})
            continue
        forecast_id = str(raw.get("forecast_id") or "")
        forecast = forecasts.get(forecast_id)
        if not forecast:
            issues["critical"].append({"code": "forecast_join_missing", "verification_id": verification_id, "forecast_id": forecast_id})
            continue
        p = float(raw.get("predicted_probability"))
        if not 0.0 <= p <= 1.0:
            issues["critical"].append({"code": "probability_out_of_range", "verification_id": verification_id, "probability": p})
            continue
        try:
            forecast_at = parse_time(str(raw.get("forecast_at") or forecast.get("forecast_at")))
            target_at = parse_time(str(raw.get("target_at") or forecast.get("target_at")))
            verified_at = parse_time(str(raw.get("verified_at")))
        except Exception as exc:
            issues["critical"].append({"code": "invalid_timestamp", "verification_id": verification_id, "message": str(exc)[:160]})
            continue
        if forecast_at > as_of or verified_at > as_of:
            issues["critical"].append({"code": "future_dated_calibration_record", "verification_id": verification_id})
            continue
        if verified_at < forecast_at:
            issues["critical"].append({"code": "verification_before_forecast", "verification_id": verification_id})
            continue
        belief_id = str(raw.get("belief_id") or "")
        if belief_id != str(forecast.get("belief_id") or ""):
            issues["critical"].append({"code": "belief_id_mismatch", "verification_id": verification_id})
            continue
        entity, dimension_from_id = _belief_parts(belief_id)
        metadata = dict(forecast.get("metadata") or {})
        dimension = str(metadata.get("dimension") or dimension_from_id or "UNKNOWN")
        sector = str(metadata.get("sector") or "UNKNOWN")
        reporting_regime = str(metadata.get("reporting_regime") or "UNKNOWN")
        closure = closures.get(forecast_id)
        outcome_observed_at = None
        if closure:
            if closure.get("calibration_eligible") is not True:
                issues["critical"].append({"code": "closure_not_calibration_eligible", "verification_id": verification_id, "forecast_id": forecast_id})
            outcome_observed_at = closure.get("outcome_observed_at")
            if outcome_observed_at:
                try:
                    outcome_dt = parse_time(str(outcome_observed_at))
                    if not (forecast_at < outcome_dt <= target_at):
                        issues["critical"].append({"code": "outcome_outside_frozen_window", "verification_id": verification_id, "forecast_id": forecast_id})
                except Exception:
                    issues["critical"].append({"code": "invalid_outcome_observed_at", "verification_id": verification_id})
        else:
            issues["warning"].append({"code": "forecast_closure_missing", "verification_id": verification_id, "forecast_id": forecast_id})
        outcome = bool(raw.get("outcome"))
        rows.append({
            "verification_id": verification_id,
            "forecast_id": forecast_id,
            "belief_id": belief_id,
            "entity": str(raw.get("entity") or entity or "UNKNOWN"),
            "dimension": dimension,
            "sector": sector,
            "reporting_regime": reporting_regime,
            "regime": str(raw.get("regime") or forecast.get("regime") or "UNKNOWN"),
            "predicted_probability": p,
            "forecast_confidence": float(raw.get("forecast_confidence") or 0.0),
            "outcome": outcome,
            "forecast_at": iso_z(forecast_at),
            "target_at": iso_z(target_at),
            "verified_at": iso_z(verified_at),
            "outcome_observed_at": outcome_observed_at,
            "reporting_season": _quarter_label(str(outcome_observed_at or raw.get("verified_at"))),
            "brier_score": float(raw.get("brier_score", (p - (1.0 if outcome else 0.0)) ** 2)),
            "log_loss": float(raw.get("log_loss", _safe_log_loss(p, outcome))),
        })
    rows.sort(key=lambda r: (r["verified_at"], r["verification_id"]))
    return rows, issues


def dependency_diagnostics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    serial = serial_effective_n(rows)
    entity = cluster_icc_effective_n(rows, "entity")
    sector = cluster_icc_effective_n(rows, "sector")
    season = cluster_icc_effective_n(rows, "reporting_season")
    overlap = overlap_diagnostics(rows)
    components = {
        "serial_residual": serial,
        "entity_cluster": entity,
        "sector_cluster": sector,
        "reporting_season_cluster": season,
        "forecast_window_overlap": overlap,
    }
    numeric = []
    for comp in (serial, entity, sector, season):
        if comp.get("effective_n") is not None:
            numeric.append(float(comp["effective_n"]))
    if overlap.get("effective_n_cap_from_non_overlapping_windows") is not None:
        numeric.append(float(overlap["effective_n_cap_from_non_overlapping_windows"]))
    descriptive_floor = min(numeric) if numeric else None
    required_statuses = [serial.get("status"), entity.get("status"), sector.get("status"), season.get("status")]
    complete = all(x == "ok" for x in required_statuses)
    promotion_grade = descriptive_floor if complete else None
    return {
        "components": components,
        "dependency_diagnostics_complete": complete,
        "descriptive_effective_n_floor": descriptive_floor,
        "promotion_grade_effective_n": promotion_grade,
        "effective_n_threshold_defined_here": False,
        "effective_n_sufficient": None,
        "note": "Missing dependency diagnostics default to insufficient evidence; PR16 defines no global promotion N threshold.",
    }


def regime_diagnostics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    counts = Counter(str(r.get("regime") or "UNKNOWN") for r in rows)
    return {
        "runtime_regime_counts": dict(sorted(counts.items())),
        "broad_market_context_frozen_at_forecast": False,
        "sector_factor_context_frozen_at_forecast": False,
        "multi_regime_robustness_assessable": False,
        "promotion_regime_robust": None,
        "status": "not_assessable_until_prospective_broad_market_and_sector_factor_context_is_frozen_with_forecasts",
    }


def run(
    state_dir: Path,
    *,
    belief_core_state_path: Path,
    pr15_runtime_state_path: Path,
    pr15_report_path: Path,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    _assert_safety()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_z = iso_z(now)
    state_dir = Path(state_dir)
    state_path = state_dir / STATE_FILENAME
    report_path = state_dir / REPORT_FILENAME

    core = _read_json(Path(belief_core_state_path), {})
    runtime = _read_json(Path(pr15_runtime_state_path), {})
    pr15_report = _read_json(Path(pr15_report_path), {})
    _validate_source(core, runtime, pr15_report)

    rows, issues = collect_calibration_rows(core, runtime, as_of=now)
    overall = calibration_metrics(rows)
    deps = dependency_diagnostics(rows)
    entity_conc = concentration(rows, "entity")
    sector_conc = concentration(rows, "sector")
    dimension_conc = concentration(rows, "dimension")
    season_conc = concentration(rows, "reporting_season")
    drift = drift_diagnostics(rows)
    regime = regime_diagnostics(rows)

    closure_counts = Counter(str(v.get("status") or "UNKNOWN") for v in _closure_map(runtime).values())
    active_forecast_ids = {
        str(f.get("forecast_id"))
        for f in (core.get("forecasts") or [])
        if isinstance(f, Mapping) and f.get("forecast_id")
    } - set(_closure_map(runtime))

    source_fingerprint = _sha({
        "pr15_contract_version": runtime.get("contract_version"),
        "verification_ids": [str(v.get("verification_id")) for v in (core.get("verifications") or []) if isinstance(v, Mapping)],
        "forecast_ids": [str(v.get("forecast_id")) for v in (core.get("forecasts") or []) if isinstance(v, Mapping)],
        "forecast_closures": runtime.get("forecast_closures") or {},
    })

    state = _read_json(state_path, empty_state())
    if str(state.get("schema_version") or "") != SCHEMA_VERSION:
        raise ValueError("PR16 state schema mismatch")
    if str(state.get("contract_version") or "") != CONTRACT_VERSION:
        raise ValueError("PR16 state contract mismatch")
    if not state.get("first_run_at"):
        state["first_run_at"] = now_z
    previous_fingerprint = state.get("last_source_fingerprint")
    new_snapshot = source_fingerprint != previous_fingerprint
    if new_snapshot:
        state.setdefault("diagnostic_snapshots", []).append({
            "snapshot_at": now_z,
            "source_fingerprint": source_fingerprint,
            "verified_n": len(rows),
            "brier_score": overall.get("brier_score"),
            "log_loss": overall.get("log_loss"),
            "ece_fixed_deciles": overall.get("expected_calibration_error_fixed_deciles"),
            "descriptive_effective_n_floor": deps.get("descriptive_effective_n_floor"),
            "promotion_grade_effective_n": deps.get("promotion_grade_effective_n"),
            "critical_data_quality_issues": len(issues["critical"]),
        })
    state["seen_verification_ids"] = sorted({
        *[str(x) for x in state.get("seen_verification_ids") or []],
        *[str(r["verification_id"]) for r in rows],
    })
    state["last_source_fingerprint"] = source_fingerprint
    state["last_run_at"] = now_z
    _write_json(state_path, state)

    readiness_reasons = ["paired WITH/WITHOUT Entity Belief economic bridge is not enabled in PR16"]
    if deps["promotion_grade_effective_n"] is None:
        readiness_reasons.append("promotion-grade effective N is unavailable because dependency diagnostics are incomplete")
    if not regime["multi_regime_robustness_assessable"]:
        readiness_reasons.append("broad-market and sector/factor regime robustness is not yet prospectively assessable")
    if issues["critical"]:
        readiness_reasons.append("critical provenance/data-quality issues are present")

    report = {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "mode": MODE,
        "generated_at": now_z,
        "purpose": "Prospective Entity Belief calibration, dependency, concentration, drift and provenance diagnostics only.",
        "active_decision_influence": False,
        "source_contract": {
            "pr15_contract_version": PR15_CONTRACT_VERSION,
            "belief_core_schema_version": 2,
            "accepted_outcome_source": EXPECTED_OUTCOME_SOURCE,
            "diagnostics_bootstrap_may_read_existing_prospective_pr15_verifications": True,
            "historical_belief_or_forecast_backfill": False,
            "source_fingerprint": source_fingerprint,
        },
        "sample": {
            "raw_core_verifications": len(core.get("verifications") or []),
            "calibration_eligible_verified": len(rows),
            "active_forecasts": len(active_forecast_ids),
            "forecast_closure_status_counts": dict(sorted(closure_counts.items())),
            "new_diagnostic_snapshot_this_run": new_snapshot,
            "diagnostic_snapshots_total": len(state.get("diagnostic_snapshots") or []),
        },
        "calibration": overall,
        "slices": {
            "by_dimension": _slice_metrics(rows, "dimension"),
            "by_entity": _slice_metrics(rows, "entity"),
            "by_sector": _slice_metrics(rows, "sector"),
            "by_reporting_regime": _slice_metrics(rows, "reporting_regime"),
        },
        "effective_n": deps,
        "concentration": {
            "entity": entity_conc,
            "sector": sector_conc,
            "dimension": dimension_conc,
            "reporting_season": season_conc,
        },
        "drift": drift,
        "regime_robustness": regime,
        "data_quality": {
            "critical_issue_count": len(issues["critical"]),
            "warning_count": len(issues["warning"]),
            "critical_issues": issues["critical"],
            "warnings": issues["warning"],
            "provenance_ok": not bool(issues["critical"]),
            "promotion_data_quality_ok": None,
        },
        "promotion_readiness": {
            "eligible_for_promotion_review": False,
            "status": "NOT_ELIGIBLE_FOR_PROMOTION_REVIEW",
            "reasons": readiness_reasons,
            "effective_n_sufficient": None,
            "calibration_ok": None,
            "drift_ok": None,
            "regime_robust": None,
            "concentration_ok": None,
            "with_without_available": False,
            "automatic_promotion": False,
        },
        "anti_hindsight": {
            "historical_belief_backfill": False,
            "historical_forecast_backfill": False,
            "diagnostics_use_only_pr15_frozen_prospective_verifications": True,
            "first_pr16_run_may_bootstrap_diagnostics_from_existing_pr15_prospective_records": True,
        },
        "capabilities": capabilities(),
        "promotion_evidence_standard": promotion_evidence_standard(),
        "safety_controls": safety_controls(),
    }
    _write_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="PR16 Entity Calibration & Diagnostics Foundation")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--belief-core-state", required=True)
    parser.add_argument("--pr15-runtime-state", required=True)
    parser.add_argument("--pr15-report", required=True)
    args = parser.parse_args()
    report = run(
        Path(args.state_dir),
        belief_core_state_path=Path(args.belief_core_state),
        pr15_runtime_state_path=Path(args.pr15_runtime_state),
        pr15_report_path=Path(args.pr15_report),
    )
    print(json.dumps({
        "mode": report["mode"],
        "sample": report["sample"],
        "calibration_n": report["calibration"]["n"],
        "effective_n": report["effective_n"],
        "promotion_readiness": report["promotion_readiness"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
