#!/usr/bin/env python3
"""Canonical calibration foundation report for Belief Core + GSE.

The report is measurement-only. It consolidates data quality, source health,
frozen-forecast coverage, calibration, slices and drift without changing any
belief, engine score, exposure, transmission weight or trading policy.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Mapping, Optional, Sequence

from belief_calibration import build_calibration_report
from belief_core import BeliefCore
from belief_data_quality_adapter import (
    DataQualityAdapter,
    forecast_quality,
    iso_z,
    parse_time,
    read_json,
    read_jsonl_with_health,
    safe_rate,
)

SCHEMA_VERSION = "belief-calibration-foundation-v1"
REPORT_NAME = "BELIEF_CALIBRATION_REPORT.json"
MODE = "shadow"
GLOBAL_CALIBRATION_MIN_N = 30
GSE_CALIBRATION_MIN_N = 30


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _source_health_from_gse_evidence(rows: Sequence[Mapping[str, Any]], now: datetime) -> Dict[str, Any]:
    grouped: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("source") or "unknown")].append(row)
    out: Dict[str, Any] = {}
    for source, group in sorted(grouped.items()):
        times = [parse_time(x.get("published_at")) for x in group]
        times = [x for x in times if x is not None]
        latest = max(times) if times else None
        ages = [max(0.0, (now - x).total_seconds() / 3600.0) for x in times]
        reliabilities = []
        for row in group:
            try:
                reliabilities.append(float(row.get("reliability", 0.0)))
            except (TypeError, ValueError):
                pass
        out[source] = {
            "evidence_count": len(group),
            "latest_published_at": None if latest is None else iso_z(latest),
            "latest_age_hours": None if latest is None else round(max(0.0, (now - latest).total_seconds() / 3600.0), 6),
            "mean_age_hours": None if not ages else round(mean(ages), 6),
            "mean_assigned_reliability": None if not reliabilities else round(mean(reliabilities), 6),
            "source_type_counts": {
                key: sum(1 for x in group if str(x.get("source_type") or "unknown") == key)
                for key in ("primary", "secondary", "derived", "unknown")
            },
        }
    return out


def _gse_calibration_from_verifications(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    eligible = [x for x in rows if bool(x.get("calibration_eligible", True))]
    if not eligible:
        return {
            "count": 0,
            "status": "awaiting_outcomes",
            "mean_brier": None,
            "mean_log_loss": None,
            "mean_predicted": None,
            "observed_rate": None,
            "calibration_bias": None,
        }
    probs = [float(x["predicted_probability"]) for x in eligible]
    outcomes = [1.0 if bool(x["outcome"]) else 0.0 for x in eligible]
    return {
        "count": len(eligible),
        "status": "insufficient_sample" if len(eligible) < GSE_CALIBRATION_MIN_N else "measuring",
        "mean_brier": round(mean(float(x["brier_score"]) for x in eligible), 6),
        "mean_log_loss": round(mean(float(x["log_loss"]) for x in eligible), 6),
        "mean_predicted": round(mean(probs), 6),
        "observed_rate": round(mean(outcomes), 6),
        "calibration_bias": round(mean(probs) - mean(outcomes), 6),
    }


def summarize_gse(root: Optional[Path], now: datetime) -> Dict[str, Any]:
    if root is None or not root.exists():
        return {
            "available": False,
            "status": "source_unavailable",
            "decision_influence": False,
            "automatic_tuning_enabled": False,
            "note": "No GSE shadow-state artifact was available to this calibration run.",
        }

    state = read_json(root / "gse_state.json", {})
    calibration = read_json(root / "gse_calibration.json", {})
    evidence_file = read_jsonl_with_health(root / "gse_evidence.jsonl")
    forecasts_file = read_jsonl_with_health(root / "gse_forecasts.jsonl")
    verifications_file = read_jsonl_with_health(root / "gse_verifications.jsonl")
    evidence = evidence_file["rows"]
    forecasts = forecasts_file["rows"]
    verifications = verifications_file["rows"]

    coverage = forecast_quality(
        forecasts,
        verifications,
        now,
        evidence_time_field="published_at",
    )
    eligible_ids = {str(v.get("forecast_id")) for v in verifications if bool(v.get("calibration_eligible", True))}
    forecast_complete = sum(
        1
        for f in forecasts
        if f.get("scenario_snapshot") and f.get("evidence_snapshot") and f.get("baseline_value") is not None
    )
    coverage["gse_full_snapshot_coverage"] = safe_rate(forecast_complete, len(forecasts))
    coverage["eligible_forecast_ids"] = len(eligible_ids)

    generated = parse_time(state.get("last_run_at"))
    runtime_age = None if generated is None else max(0.0, (now - generated).total_seconds() / 3600.0)
    controls = dict(state.get("controls") or {})
    safety_ok = all(
        controls.get(key) is False
        for key in (
            "trade_execution_enabled",
            "policy_output_enabled",
            "automatic_tuning_enabled",
            "decision_engine_connected",
        )
    ) if controls else False

    local_calibration = _gse_calibration_from_verifications(verifications)
    artifact_calibration = calibration if isinstance(calibration, dict) else {}
    return {
        "available": True,
        "status": "shadow_source_ready" if safety_ok else "safety_controls_unverified",
        "mode": state.get("mode"),
        "schema_version": state.get("schema_version"),
        "last_run_at": state.get("last_run_at"),
        "runtime_age_hours": None if runtime_age is None else round(runtime_age, 6),
        "last_status": dict(state.get("last_status") or {}),
        "cadence": dict(state.get("cadence") or {}),
        "controls": controls,
        "safety_controls_verified": safety_ok,
        "decision_influence": False,
        "automatic_tuning_enabled": False,
        "telemetry": {
            "evidence": len(evidence),
            "forecasts": len(forecasts),
            "verifications": len(verifications),
            "evidence_jsonl_parse_errors": evidence_file["parse_errors"],
            "forecast_jsonl_parse_errors": forecasts_file["parse_errors"],
            "verification_jsonl_parse_errors": verifications_file["parse_errors"],
            "source_health": _source_health_from_gse_evidence(evidence, now),
            "forecast_coverage": coverage,
        },
        "calibration": {
            "canonical_recomputed": local_calibration,
            "gse_native_report": artifact_calibration,
            "sample_sufficient": int(local_calibration.get("count") or 0) >= GSE_CALIBRATION_MIN_N,
        },
        "future_adapter_role": "forecast_source_only_until_separate_GSE_to_Belief_Core_read_only_adapter_PR",
    }


def _promotion_gate(
    core_calibration: Mapping[str, Any],
    quality: Mapping[str, Any],
    gse: Mapping[str, Any],
) -> Dict[str, Any]:
    reasons: List[str] = []
    core_n = int(core_calibration.get("count_calibration_eligible") or 0)
    if core_n < GLOBAL_CALIBRATION_MIN_N:
        reasons.append(f"belief_core_calibration_sample_below_{GLOBAL_CALIBRATION_MIN_N}")
    if str(quality.get("health")) in {"critical", "degraded"}:
        reasons.append("belief_data_quality_not_green")
    if not bool(gse.get("available")):
        reasons.append("gse_calibration_source_unavailable")
    else:
        gse_n = int((((gse.get("calibration") or {}).get("canonical_recomputed") or {}).get("count")) or 0)
        if gse_n < GSE_CALIBRATION_MIN_N:
            reasons.append(f"gse_calibration_sample_below_{GSE_CALIBRATION_MIN_N}")
        if not bool(gse.get("safety_controls_verified")):
            reasons.append("gse_shadow_safety_controls_unverified")
    return {
        "decision_influence_allowed": False,
        "bounded_modifier_allowed": False,
        "status": "foundation_measurement_only",
        "blocking_reasons": reasons,
        "required_next_stage": "read_only_engine_bridge_after_calibration_review",
        "note": "This foundation PR can never authorize engine influence. Promotion requires a separate reviewed gate.",
    }


def build_report(
    belief_state_dir: Path,
    *,
    gse_state_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = read_json(belief_state_dir / "state.json", {})
    scheduler = read_json(belief_state_dir / "scheduler.json", {})
    observations_file = read_jsonl_with_health(belief_state_dir / "observations.jsonl")
    observations = observations_file["rows"]
    forecasts = list(state.get("forecasts") or [])
    verifications = list(state.get("verifications") or [])

    core = BeliefCore(belief_state_dir)
    ledger_integrity = core.verify_ledger_integrity()
    quality = DataQualityAdapter().build(
        observations=observations,
        forecasts=forecasts,
        verifications=verifications,
        scheduler=scheduler,
        now=now,
        observation_parse_errors=int(observations_file["parse_errors"]),
        ledger_integrity=ledger_integrity,
    )
    calibration = build_calibration_report(verifications)
    gse = summarize_gse(gse_state_dir, now)

    source_health = {
        "belief_observation_sources": dict((quality.get("observations") or {}).get("by_source") or {}),
        "belief_adapters": dict((quality.get("observations") or {}).get("by_adapter") or {}),
        "gse_sources": dict((((gse.get("telemetry") or {}).get("source_health")) or {})),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "report_name": "BELIEF_CALIBRATION_REPORT",
        "generated_at": iso_z(now),
        "mode": MODE,
        "controls": {
            "decision_engine_connected": False,
            "trade_execution_enabled": False,
            "policy_output_enabled": False,
            "automatic_tuning_enabled": False,
            "belief_weight_changes_enabled": False,
            "gse_transmission_weight_changes_enabled": False,
        },
        "data_quality": quality,
        "latency_freshness": {
            "observation_freshness_age_hours": (quality.get("observations") or {}).get("freshness_age_hours"),
            "collection_latency_hours": (quality.get("observations") or {}).get("collection_latency_hours"),
            "forecast_evidence_latency_hours": (quality.get("forecast_coverage") or {}).get("forecast_evidence_latency_hours"),
            "gse_forecast_evidence_latency_hours": (((gse.get("telemetry") or {}).get("forecast_coverage") or {}).get("forecast_evidence_latency_hours")),
        },
        "stale_invalid": {
            "belief_stale_invalid_rate": (quality.get("observations") or {}).get("stale_invalid_rate"),
            "belief_unavailable_rate": (quality.get("observations") or {}).get("unavailable_rate"),
            "observation_jsonl_parse_errors": (quality.get("pipeline") or {}).get("observation_jsonl_parse_errors"),
            "gse_jsonl_parse_errors": {
                "evidence": ((gse.get("telemetry") or {}).get("evidence_jsonl_parse_errors")),
                "forecasts": ((gse.get("telemetry") or {}).get("forecast_jsonl_parse_errors")),
                "verifications": ((gse.get("telemetry") or {}).get("verification_jsonl_parse_errors")),
            },
        },
        "source_health": source_health,
        "frozen_forecast_coverage": {
            "belief_core": quality.get("forecast_coverage"),
            "gse": (gse.get("telemetry") or {}).get("forecast_coverage"),
        },
        "belief_calibration": calibration,
        "proper_scoring": {
            "brier": (calibration.get("overall") or {}).get("mean_brier"),
            "log_loss": (calibration.get("overall") or {}).get("mean_log_loss"),
            "ece": (calibration.get("overall") or {}).get("ece"),
            "mce": (calibration.get("overall") or {}).get("mce"),
            "calibration_bias": (calibration.get("overall") or {}).get("calibration_bias"),
            "reliability_curve": calibration.get("reliability_curve"),
        },
        "source_evidence_diagnostics": {
            "source_performance": calibration.get("source_performance"),
            "evidence_type_performance": calibration.get("evidence_type_performance"),
            "confidence_diagnostics": calibration.get("confidence_diagnostics"),
        },
        "regime_horizon_slices": {
            "by_regime": calibration.get("by_regime"),
            "by_horizon": calibration.get("by_horizon"),
            "by_domain": calibration.get("by_domain"),
            "by_entity": calibration.get("by_entity"),
        },
        "drift": calibration.get("drift"),
        "gse_forecast_source": gse,
        "promotion_gate": _promotion_gate(calibration, quality, gse),
        "interpretation": {
            "belief_probability": "Event probability from frozen Belief Core forecasts.",
            "evidence_confidence": "Evidence quality/freshness/diversity diagnostic; not an event probability.",
            "gse": "GSE is measured as a separate forecast source. It is not yet Belief Core evidence and cannot influence engines.",
            "with_without": "Engine WITH-vs-WITHOUT Belief testing belongs to later read-only bridge PRs, not this foundation.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build canonical Belief Core calibration foundation report")
    parser.add_argument("--belief-state-dir", required=True)
    parser.add_argument("--gse-state-dir")
    parser.add_argument("--output")
    parser.add_argument("--now", help="ISO timestamp override for deterministic validation")
    args = parser.parse_args()

    belief_dir = Path(args.belief_state_dir)
    gse_dir = Path(args.gse_state_dir) if args.gse_state_dir else None
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise ValueError("invalid --now timestamp")
    report = build_report(belief_dir, gse_state_dir=gse_dir, now=now)
    output = Path(args.output) if args.output else belief_dir / REPORT_NAME
    _write_json(output, report)
    print(json.dumps({
        "report": str(output),
        "mode": report["mode"],
        "decision_influence_allowed": report["promotion_gate"]["decision_influence_allowed"],
        "belief_calibration_n": report["belief_calibration"]["count_calibration_eligible"],
        "gse_available": report["gse_forecast_source"]["available"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
