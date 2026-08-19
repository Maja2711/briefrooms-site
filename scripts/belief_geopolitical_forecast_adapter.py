#!/usr/bin/env python3
"""Read-only/shadow GSE -> Belief Core geopolitical forecast adapter.

Only immutable GSE v1 frozen forecasts can become Belief Core evidence. GSE v2
historical-analogue candidates are attached as research telemetry but cannot be
used as evidence until a separate promotion decision.

The adapter never connects Belief Core to WES, BRACE or BRACE-SPX and never
changes an engine decision, position, size or execution policy.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from belief_adapter_contract import (
    AdapterResult,
    EvidenceAssessment,
    Observation,
    clamp,
    observation_to_evidence,
)
from belief_core import parse_time

MODE = "shadow"
ADAPTER_NAME = "geopolitical_forecast"
ADAPTER_VERSION = "1.0.0"
MIN_CALIBRATION_N = 30
MAX_BRIER_FOR_EVIDENCE = 0.25
MAX_ABS_BIAS_FOR_EVIDENCE = 0.15
MAX_EVIDENCE_STRENGTH = 0.30
EVIDENCE_VARIANT = "gse_v1_frozen"
V2_ROLE = "research_telemetry_only"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_controls(state: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if str(state.get("mode") or "") != MODE:
        reasons.append("gse_not_shadow_mode")
    controls = state.get("controls") if isinstance(state.get("controls"), Mapping) else {}
    for key in (
        "trade_execution_enabled",
        "policy_output_enabled",
        "automatic_tuning_enabled",
        "decision_engine_connected",
    ):
        if controls.get(key) is not False:
            reasons.append(f"gse_control_not_hard_off:{key}")
    return not reasons, reasons


def _metric(row: Mapping[str, Any], key: str) -> Optional[float]:
    value = row.get(key)
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _joint_metrics(
    verifications: Sequence[Mapping[str, Any]], asset: str, horizon_hours: int
) -> Dict[str, Any]:
    rows = [
        row
        for row in verifications
        if bool(row.get("calibration_eligible", True))
        and str(row.get("asset") or "") == asset
        and int(row.get("horizon_hours") or 0) == int(horizon_hours)
    ]
    if not rows:
        return {"count": 0, "mean_brier": None, "bias": None}
    briers: List[float] = []
    predicted: List[float] = []
    outcomes: List[float] = []
    for row in rows:
        p = _metric(row, "predicted_probability")
        brier = _metric(row, "brier_score")
        if p is None or brier is None or row.get("outcome") is None:
            continue
        predicted.append(p)
        outcomes.append(1.0 if bool(row.get("outcome")) else 0.0)
        briers.append(brier)
    if not briers:
        return {"count": 0, "mean_brier": None, "bias": None}
    return {
        "count": len(briers),
        "mean_brier": round(mean(briers), 6),
        "bias": round(mean(predicted) - mean(outcomes), 6),
    }


def _calibration_qualification(
    calibration: Mapping[str, Any],
    verifications: Sequence[Mapping[str, Any]],
    asset: str,
    horizon_hours: int,
) -> Dict[str, Any]:
    asset_slice = ((calibration.get("by_asset") or {}).get(asset) or {})
    horizon_slice = ((calibration.get("by_horizon_hours") or {}).get(str(horizon_hours)) or {})
    joint_slice = _joint_metrics(verifications, asset, horizon_hours)
    n_asset = int(asset_slice.get("count") or 0)
    n_horizon = int(horizon_slice.get("count") or 0)
    n_joint = int(joint_slice.get("count") or 0)
    briers = [
        x
        for x in (
            _metric(asset_slice, "mean_brier"),
            _metric(horizon_slice, "mean_brier"),
            _metric(joint_slice, "mean_brier"),
        )
        if x is not None
    ]
    biases = [
        abs(x)
        for x in (
            _metric(asset_slice, "bias"),
            _metric(horizon_slice, "bias"),
            _metric(joint_slice, "bias"),
        )
        if x is not None
    ]
    worst_brier = max(briers) if briers else None
    worst_abs_bias = max(biases) if biases else None
    reasons: List[str] = []
    if n_asset < MIN_CALIBRATION_N:
        reasons.append(f"asset_calibration_n_below_{MIN_CALIBRATION_N}")
    if n_horizon < MIN_CALIBRATION_N:
        reasons.append(f"horizon_calibration_n_below_{MIN_CALIBRATION_N}")
    if n_joint < MIN_CALIBRATION_N:
        reasons.append(f"asset_horizon_joint_n_below_{MIN_CALIBRATION_N}")
    if worst_brier is None:
        reasons.append("brier_unavailable")
    elif worst_brier > MAX_BRIER_FOR_EVIDENCE:
        reasons.append("brier_above_evidence_gate")
    if worst_abs_bias is None:
        reasons.append("bias_unavailable")
    elif worst_abs_bias > MAX_ABS_BIAS_FOR_EVIDENCE:
        reasons.append("absolute_bias_above_evidence_gate")
    eligible = not reasons
    reliability = 0.40
    if worst_brier is not None and worst_abs_bias is not None:
        reliability = clamp(0.67 - 0.70 * worst_brier - 0.35 * worst_abs_bias, 0.35, 0.65)
    return {
        "evidence_eligible": eligible,
        "reasons": reasons,
        "asset_count": n_asset,
        "horizon_count": n_horizon,
        "asset_horizon_joint_count": n_joint,
        "joint_mean_brier": joint_slice.get("mean_brier"),
        "joint_bias": joint_slice.get("bias"),
        "worst_mean_brier": None if worst_brier is None else round(worst_brier, 6),
        "worst_abs_bias": None if worst_abs_bias is None else round(worst_abs_bias, 6),
        "derived_source_reliability": round(reliability, 6),
        "thresholds": {
            "minimum_count_each_marginal_and_joint": MIN_CALIBRATION_N,
            "maximum_mean_brier": MAX_BRIER_FOR_EVIDENCE,
            "maximum_absolute_bias": MAX_ABS_BIAS_FOR_EVIDENCE,
        },
    }


def _active_latest(
    forecasts: Sequence[Mapping[str, Any]],
    as_of: datetime,
) -> List[Dict[str, Any]]:
    latest: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for raw in forecasts:
        try:
            forecast_at = parse_time(raw.get("forecast_at"))
            target_at = parse_time(raw.get("target_at"))
            horizon = int(raw.get("horizon_hours") or 0)
        except Exception:
            continue
        if forecast_at > as_of or target_at <= as_of:
            continue
        if str(raw.get("mode") or MODE) != MODE:
            continue
        asset = str(raw.get("asset") or "")
        if not asset or not raw.get("forecast_id") or horizon <= 0:
            continue
        key = (asset, horizon)
        row = dict(raw)
        previous = latest.get(key)
        if previous is None or parse_time(row["forecast_at"]) > parse_time(previous["forecast_at"]):
            latest[key] = row
    return sorted(latest.values(), key=lambda x: (str(x.get("asset")), int(x.get("horizon_hours") or 0)))


def _v2_index(root: Path) -> Dict[str, Dict[str, Any]]:
    return {
        str(row.get("baseline_forecast_id")): row
        for row in _read_jsonl(root / "gse_v2_forecasts.jsonl")
        if row.get("baseline_forecast_id")
    }


def _scenario_lineage(forecast: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    scenario_ids: List[str] = []
    evidence_ids: List[str] = []
    for scenario in forecast.get("scenario_snapshot") or []:
        if scenario.get("scenario_id"):
            scenario_ids.append(str(scenario["scenario_id"]))
        evidence_ids.extend(str(x) for x in (scenario.get("evidence_ids") or []))
    if not evidence_ids:
        for evidence in forecast.get("evidence_snapshot") or []:
            if evidence.get("evidence_id"):
                evidence_ids.append(str(evidence["evidence_id"]))
    return sorted(set(scenario_ids)), sorted(set(evidence_ids))


def _strength(probability: float) -> float:
    # Derived geopolitical evidence stays intentionally modest even after its
    # calibration gate passes. Probability and evidence strength remain distinct.
    return clamp(0.08 + 1.10 * abs(float(probability) - 0.50), 0.08, MAX_EVIDENCE_STRENGTH)


def _serial_cluster(asset: str, horizon_hours: int) -> str:
    """All serial forecasts from one GSE asset/horizon share one cluster.

    GSE freezes every six hours. Treating each batch as independent evidence
    would pseudo-replicate one forecasting model and inflate Belief mass. A
    stable cluster lets Belief Core select the freshest/strongest representative.
    """
    return f"gse:serial_forecast:{asset}:{int(horizon_hours)}"


class GeopoliticalForecastAdapter:
    name = ADAPTER_NAME
    version = ADAPTER_VERSION
    decision_influence = False
    trade_execution_enabled = False
    policy_output_enabled = False
    v2_evidence_enabled = False

    def run(self, gse_state_dir: Path, as_of: datetime) -> AdapterResult:
        as_of = as_of.astimezone(timezone.utc)
        state = _read_json(gse_state_dir / "gse_state.json", {})
        calibration = _read_json(gse_state_dir / "gse_calibration.json", {})
        forecasts = _read_jsonl(gse_state_dir / "gse_forecasts.jsonl")
        verifications = _read_jsonl(gse_state_dir / "gse_verifications.jsonl")
        v2_by_baseline = _v2_index(gse_state_dir)
        safe, safety_reasons = _safe_controls(state)

        if not state or not forecasts:
            obs = Observation.make(
                adapter=self.name,
                metric="gse.forecast_source_health",
                entity="GSE",
                observed_at=_iso_z(as_of),
                value="unavailable",
                unit="status",
                source="BriefRooms GSE",
                source_type="derived",
                source_ref="gse://state/unavailable",
                reliability=0.0,
                independence_cluster="gse:source_health",
                status="unavailable",
                tags=("geopolitical", "forecast", "shadow"),
                metadata={"reason": "gse_state_or_frozen_forecasts_missing", "decision_influence": False},
            )
            return AdapterResult(self.name, (obs,), ())

        if not safe:
            obs = Observation.make(
                adapter=self.name,
                metric="gse.forecast_source_health",
                entity="GSE",
                observed_at=_iso_z(as_of),
                value="invalid_safety_state",
                unit="status",
                source="BriefRooms GSE",
                source_type="derived",
                source_ref="gse://state/safety-invalid",
                reliability=0.0,
                independence_cluster="gse:source_health",
                status="invalid",
                tags=("geopolitical", "forecast", "shadow", "safety"),
                metadata={"reasons": safety_reasons, "decision_influence": False},
            )
            return AdapterResult(self.name, (obs,), ())

        observations: List[Observation] = []
        evidence = []
        for forecast in _active_latest(forecasts, as_of):
            asset = str(forecast["asset"])
            horizon = int(forecast["horizon_hours"])
            forecast_id = str(forecast["forecast_id"])
            probability = float(forecast["predicted_probability"])
            direction = int(forecast["direction"])
            qualification = _calibration_qualification(calibration, verifications, asset, horizon)
            scenario_ids, gse_evidence_ids = _scenario_lineage(forecast)
            v2_candidate = v2_by_baseline.get(forecast_id)
            serial_cluster = _serial_cluster(asset, horizon)
            metadata = {
                "gse_forecast_id": forecast_id,
                "gse_batch_id": forecast.get("batch_id"),
                "forecast_variant_used_for_evidence": EVIDENCE_VARIANT,
                "horizon_hours": horizon,
                "target_at": forecast.get("target_at"),
                "direction": direction,
                "predicted_probability": probability,
                "gse_confidence": forecast.get("confidence"),
                "impact_magnitude": forecast.get("impact_magnitude"),
                "gse_scenario_ids": scenario_ids,
                "gse_evidence_ids": gse_evidence_ids,
                "calibration_qualification": qualification,
                "serial_independence_cluster": serial_cluster,
                "serial_forecasts_are_not_independent": True,
                "v2_role": V2_ROLE,
                "v2_candidate": None if v2_candidate is None else {
                    "candidate_id": v2_candidate.get("candidate_id"),
                    "v2_candidate_probability": v2_candidate.get("v2_candidate_probability"),
                    "effective_analogue_n": v2_candidate.get("effective_analogue_n"),
                    "analogue_status": v2_candidate.get("analogue_status"),
                    "candidate_sha256": v2_candidate.get("candidate_sha256"),
                },
                "decision_influence": False,
            }
            observation = Observation.make(
                adapter=self.name,
                metric="gse.frozen_direction_probability",
                entity=asset,
                observed_at=str(forecast["forecast_at"]),
                value={
                    "direction": direction,
                    "probability": probability,
                    "confidence": forecast.get("confidence"),
                    "impact_magnitude": forecast.get("impact_magnitude"),
                },
                unit="probability",
                source="BriefRooms GSE",
                source_type="derived",
                source_ref=f"gse://forecast/{forecast_id}",
                reliability=float(qualification["derived_source_reliability"]),
                independence_cluster=serial_cluster,
                status="ok",
                tags=("geopolitical", "forecast", "frozen", "shadow", asset.lower()),
                metadata=metadata,
            )
            observations.append(observation)

            # v1: only the calibrated 24h SPX forecast can affect the SPX trend
            # belief inside the shadow Belief Core. Other assets remain
            # observations until atomic commodity/FX beliefs exist.
            if asset != "SPX" or horizon != 24 or not qualification["evidence_eligible"]:
                continue
            assessment = EvidenceAssessment(
                belief_id="spx.trend.bullish",
                direction=1 if direction > 0 else -1,
                strength=_strength(probability),
                evidence_type="geopolitical_forecast",
                note=(
                    f"Frozen GSE v1 geopolitical transmission forecast for SPX {horizon}h; "
                    f"p(direction)={probability:.3f}."
                ),
                independence_cluster=serial_cluster,
                metadata={
                    "gse_forecast_id": forecast_id,
                    "gse_variant": "v1",
                    "gse_v2_not_used_for_evidence": True,
                    "serial_forecasts_are_not_independent": True,
                    "calibration_qualification": qualification,
                    "scenario_ids": scenario_ids,
                    "gse_evidence_ids": gse_evidence_ids,
                    "decision_influence": False,
                },
            )
            evidence.append(observation_to_evidence(observation, assessment))

        return AdapterResult(self.name, tuple(observations), tuple(evidence))
