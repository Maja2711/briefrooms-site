#!/usr/bin/env python3
"""Prospective read-only BRACE-SPX <-> Belief Core bridge.

The bridge freezes one point-in-time Engine-Belief Observation for each new
BRACE-SPX Generation 6 shadow state. It never changes BRACE-SPX, Belief Core,
engine scores, candidate ranking, target exposure, sizing, vetoes or trading.

PR #6 scope is observation collection only. WITH-vs-WITHOUT evaluation and any
hypothetical Belief-adjusted decision belong to a later reviewed PR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = "brace-spx-belief-readonly-v1"
REPORT_VERSION = "brace-spx-belief-report-v1"
MODE = "shadow"
GENERATION_ID = "spx-orthogonal-core-v6"
SPX_BELIEF_IDS: Tuple[str, ...] = (
    "spx.trend.bullish",
    "spx.breadth.healthy",
    "spx.volatility.benign",
    "spx.liquidity.supportive",
    "spx.financial_conditions.supportive",
)
MAX_BELIEF_AGE_HOURS = 18.0
STRONG_RELATIONSHIP_CONFIDENCE = 0.65


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _append_jsonl_unique(path: Path, rows: Iterable[Mapping[str, Any]], key: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if path.exists():
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            value = payload.get(key)
            if not value:
                raise ValueError(f"{path.name} line {line_no} missing {key}")
            existing.add(str(value))
    written = 0
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            value = str(row.get(key) or "")
            if not value or value in existing:
                continue
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            existing.add(value)
            written += 1
    return written


def canonical_sha256(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_mean(values: Iterable[float]) -> Optional[float]:
    rows = list(values)
    return fmean(rows) if rows else None


def controls() -> Dict[str, bool]:
    return {
        "belief_influence": False,
        "exposure_change": False,
        "score_change": False,
        "veto": False,
        "sizing_change": False,
        "candidate_ranking_change": False,
        "trade_execution": False,
        "policy_output": False,
        "automatic_tuning": False,
        "with_without_evaluation": False,
        "bounded_modifier": False,
    }


def _assert_controls_hard_off(payload: Mapping[str, Any]) -> None:
    enabled = [key for key, value in payload.items() if value is not False]
    if enabled:
        raise RuntimeError("BRACE-SPX/Belief bridge safety invariant violated: " + ",".join(enabled))


def brace_specialist_state(shadow: Mapping[str, Any]) -> Dict[str, Any]:
    """Summarize the immutable G6 parallel-candidate shadow state.

    G6 has no authorized single champion. The bridge therefore records a
    specialist consensus only when G6 itself emits all eight candidate
    snapshots. During warm-up the specialist opinion is explicitly unavailable.
    """
    source = {
        "generation_id": shadow.get("generation_id"),
        "candidate_signature": shadow.get("candidate_signature"),
        "updated_at": shadow.get("updated_at"),
        "latest_market_date": shadow.get("latest_market_date"),
        "status": shadow.get("status"),
        "observations_collected": shadow.get("observations_collected"),
        "warmup_required": shadow.get("warmup_required"),
        "holdout_accessed": bool(shadow.get("holdout_accessed", False)),
        "live_orders": bool(shadow.get("live_orders", False)),
        "autonomous_trading": bool(shadow.get("autonomous_trading", False)),
        "single_champion_selected": bool(shadow.get("single_champion_selected", False)),
    }
    governance_ok = (
        source["generation_id"] == GENERATION_ID
        and source["holdout_accessed"] is False
        and source["live_orders"] is False
        and source["autonomous_trading"] is False
        and source["single_champion_selected"] is False
    )
    if not governance_ok:
        return {
            "available": False,
            "state_type": "parallel_candidate_consensus",
            "stance": "unavailable",
            "confidence": 0.0,
            "reason": "brace_spx_governance_guard_failed",
            "source": source,
            "candidate_consensus": None,
            "family_scores": deepcopy(shadow.get("family_scores")),
            "latest_regime": shadow.get("latest_regime"),
        }
    if str(shadow.get("status")) != "shadow_active_no_orders":
        return {
            "available": False,
            "state_type": "parallel_candidate_consensus",
            "stance": "unavailable",
            "confidence": 0.0,
            "reason": "brace_spx_warmup_no_opinion",
            "source": source,
            "candidate_consensus": None,
            "family_scores": deepcopy(shadow.get("family_scores")),
            "latest_regime": shadow.get("latest_regime"),
        }

    snapshots = [row for row in (shadow.get("candidate_snapshots") or []) if isinstance(row, dict)]
    exposures = [value for value in (_finite(row.get("target_exposure_next_session")) for row in snapshots) if value is not None]
    if len(exposures) != 8:
        return {
            "available": False,
            "state_type": "parallel_candidate_consensus",
            "stance": "unavailable",
            "confidence": 0.0,
            "reason": "brace_spx_candidate_snapshot_incomplete",
            "source": source,
            "candidate_consensus": None,
            "family_scores": deepcopy(shadow.get("family_scores")),
            "latest_regime": shadow.get("latest_regime"),
        }

    mean_exposure = float(_safe_mean(exposures) or 0.0)
    risk_on_votes = sum(value >= 0.60 for value in exposures)
    defensive_votes = sum(value <= 0.40 for value in exposures)
    neutral_votes = len(exposures) - risk_on_votes - defensive_votes
    if mean_exposure >= 0.60:
        stance, directional_votes = "risk_on", risk_on_votes
    elif mean_exposure <= 0.40:
        stance, directional_votes = "defensive", defensive_votes
    else:
        stance, directional_votes = "neutral", neutral_votes
    agreement_ratio = directional_votes / len(exposures)
    distance = min(1.0, abs(mean_exposure - 0.50) / 0.50)
    confidence = agreement_ratio if stance == "neutral" else agreement_ratio * distance
    return {
        "available": True,
        "state_type": "parallel_candidate_consensus",
        "stance": stance,
        "confidence": round(confidence, 6),
        "reason": "g6_parallel_candidate_consensus_read_only",
        "source": source,
        "candidate_consensus": {
            "candidate_count": 8,
            "mean_target_exposure_next_session": round(mean_exposure, 6),
            "risk_on_votes": risk_on_votes,
            "neutral_votes": neutral_votes,
            "defensive_votes": defensive_votes,
            "agreement_ratio": round(agreement_ratio, 6),
            "candidate_snapshots_sha256": canonical_sha256(snapshots),
        },
        "family_scores": deepcopy(shadow.get("family_scores")),
        "latest_regime": shadow.get("latest_regime"),
    }


def _forecast_rows(state: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    value = state.get("forecasts") or []
    if isinstance(value, list):
        return [row for row in value if isinstance(row, Mapping)]
    if isinstance(value, Mapping):
        return [row for row in value.values() if isinstance(row, Mapping)]
    return []


def select_frozen_belief_state(state: Mapping[str, Any], as_of: datetime) -> Dict[str, Any]:
    """Choose the latest complete SPX Belief forecast set known at BRACE time."""
    groups: Dict[str, list[Mapping[str, Any]]] = {}
    for row in _forecast_rows(state):
        belief_id = str(row.get("belief_id") or "")
        if belief_id not in SPX_BELIEF_IDS:
            continue
        forecast_at = _dt(row.get("forecast_at"))
        target_at = _dt(row.get("target_at"))
        if forecast_at is None or target_at is None:
            continue
        if forecast_at > as_of or target_at <= as_of:
            continue
        set_id = str(row.get("forecast_set_id") or f"legacy:{row.get('forecast_at')}")
        groups.setdefault(set_id, []).append(row)
    complete = []
    required = set(SPX_BELIEF_IDS)
    for set_id, rows in groups.items():
        by_id = {str(row.get("belief_id")): row for row in rows}
        if set(by_id) != required:
            continue
        latest_at = max(_dt(row.get("forecast_at")) for row in by_id.values())
        if latest_at is None:
            continue
        complete.append((latest_at, set_id, by_id))
    if not complete:
        return {
            "available": False,
            "stance": "unavailable",
            "confidence": 0.0,
            "reason": "no_complete_point_in_time_spx_belief_set",
            "forecast_set_id": None,
            "forecast_at": None,
            "age_hours": None,
            "beliefs": [],
        }
    latest_at, set_id, by_id = max(complete, key=lambda item: item[0])
    age_hours = (as_of - latest_at).total_seconds() / 3600.0
    if age_hours < -1e-9:
        raise RuntimeError("future Belief snapshot selected")
    if age_hours > MAX_BELIEF_AGE_HOURS:
        return {
            "available": False,
            "stance": "unavailable",
            "confidence": 0.0,
            "reason": "belief_snapshot_too_old_at_brace_state",
            "forecast_set_id": set_id,
            "forecast_at": _iso_z(latest_at),
            "age_hours": round(age_hours, 6),
            "beliefs": [],
        }

    frozen = []
    probabilities = []
    confidences = []
    regimes = []
    for belief_id in SPX_BELIEF_IDS:
        row = by_id[belief_id]
        p = _finite(row.get("predicted_probability"))
        c = _finite(row.get("forecast_confidence"))
        if p is None or c is None:
            return {
                "available": False,
                "stance": "unavailable",
                "confidence": 0.0,
                "reason": "belief_snapshot_probability_or_confidence_missing",
                "forecast_set_id": set_id,
                "forecast_at": _iso_z(latest_at),
                "age_hours": round(age_hours, 6),
                "beliefs": [],
            }
        probabilities.append(p)
        confidences.append(c)
        regimes.append(str(row.get("regime") or "unknown"))
        frozen.append({
            "forecast_id": row.get("forecast_id"),
            "forecast_set_id": row.get("forecast_set_id"),
            "belief_id": belief_id,
            "predicted_probability": round(p, 6),
            "forecast_confidence": round(c, 6),
            "forecast_at": row.get("forecast_at"),
            "target_at": row.get("target_at"),
            "horizon_hours": row.get("horizon_hours"),
            "domain": row.get("domain"),
            "entity": row.get("entity"),
            "regime": row.get("regime"),
            "representative_evidence_ids": list(row.get("representative_evidence_ids") or []),
            "evidence_snapshot_sha256": canonical_sha256(row.get("evidence_snapshot") or []),
        })
    score = float(_safe_mean(probabilities) or 0.5)
    confidence = float(_safe_mean(confidences) or 0.0)
    if score >= 0.60:
        stance = "risk_on"
    elif score <= 0.40:
        stance = "defensive"
    else:
        stance = "neutral"
    regime = Counter(regimes).most_common(1)[0][0] if regimes else "unknown"
    return {
        "available": True,
        "stance": stance,
        "confidence": round(confidence, 6),
        "risk_on_probability_mean": round(score, 6),
        "aggregation": "equal_weight_mean_of_five_predeclared_supportive_spx_beliefs",
        "reason": "latest_complete_frozen_spx_belief_set",
        "forecast_set_id": set_id,
        "forecast_at": _iso_z(latest_at),
        "age_hours": round(age_hours, 6),
        "regime": regime,
        "beliefs": frozen,
        "snapshot_sha256": canonical_sha256(frozen),
    }


def relationship(brace: Mapping[str, Any], belief: Mapping[str, Any]) -> Dict[str, Any]:
    if not brace.get("available"):
        return {"class": "UNAVAILABLE", "strength": 0.0, "reason": brace.get("reason")}
    if not belief.get("available"):
        return {"class": "UNAVAILABLE", "strength": 0.0, "reason": belief.get("reason")}
    b = str(brace.get("stance") or "unavailable")
    k = str(belief.get("stance") or "unavailable")
    strength = min(float(brace.get("confidence") or 0.0), float(belief.get("confidence") or 0.0))
    if "neutral" in {b, k}:
        return {"class": "NEUTRAL", "strength": round(strength, 6), "reason": "at_least_one_state_neutral"}
    if b == k and b in {"risk_on", "defensive"}:
        cls = "STRONG_AGREEMENT" if strength >= STRONG_RELATIONSHIP_CONFIDENCE else "WEAK_AGREEMENT"
    elif {b, k} == {"risk_on", "defensive"}:
        cls = "STRONG_CONFLICT" if strength >= STRONG_RELATIONSHIP_CONFIDENCE else "WEAK_CONFLICT"
    else:
        cls = "UNAVAILABLE"
    return {"class": cls, "strength": round(strength, 6), "reason": "read_only_relationship_classification"}


def _initial_state(now: datetime) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "activated_at": _iso_z(now),
        "controls": controls(),
        "last_capture_at": None,
        "records_total": 0,
    }


def _report(observations: Sequence[Mapping[str, Any]], bridge_state: Mapping[str, Any], generated_at: datetime) -> Dict[str, Any]:
    classes = Counter(str((row.get("relationship") or {}).get("class") or "UNAVAILABLE") for row in observations)
    eligible = [row for row in observations if bool(row.get("engine_belief_calibration_eligible"))]
    return {
        "schema_version": REPORT_VERSION,
        "report_name": "BRACE_SPX_BELIEF_BRIDGE_REPORT",
        "generated_at": _iso_z(generated_at),
        "mode": MODE,
        "controls": controls(),
        "activation": {
            "activated_at": bridge_state.get("activated_at"),
            "historical_backfill_allowed": False,
            "prospective_only": True,
        },
        "sample": {
            "observations_total": len(observations),
            "engine_belief_calibration_eligible": len(eligible),
            "relationship_classes": dict(sorted(classes.items())),
        },
        "with_without_belief": {
            "enabled": False,
            "status": "deferred_to_future_reviewed_pr",
            "hypothetical_belief_adjusted_decision": None,
            "delta_pnl": None,
            "delta_drawdown": None,
        },
        "decision_influence": False,
        "bounded_modifier": False,
        "status": "collecting_prospective_engine_belief_observations",
    }


def run_bridge(bridge_dir: Path, belief_state_path: Path, brace_shadow_path: Path, now: datetime) -> Dict[str, Any]:
    _assert_controls_hard_off(controls())
    bridge_dir.mkdir(parents=True, exist_ok=True)
    state_path = bridge_dir / "bridge_state.json"
    observations_path = bridge_dir / "engine_belief_observations.jsonl"
    report_path = bridge_dir / "BRACE_SPX_BELIEF_BRIDGE_REPORT.json"

    existed = state_path.exists()
    bridge_state = _read_json(state_path, _initial_state(now))
    _assert_controls_hard_off(bridge_state.get("controls") or controls())
    activated_at = _dt(bridge_state.get("activated_at")) or now

    brace_raw = _read_json(brace_shadow_path, {})
    belief_raw = _read_json(belief_state_path, {})
    brace = brace_specialist_state(brace_raw)
    brace_at = _dt((brace.get("source") or {}).get("updated_at"))

    status = "no_capture"
    written = 0
    if not existed:
        status = "activated_waiting_for_prospective_brace_state"
    elif not brace_raw or brace_at is None:
        status = "brace_spx_source_unavailable"
    elif brace_at < activated_at:
        status = "pre_activation_brace_state_not_reconstructed"
    else:
        record_id = "brace-spx-belief-" + canonical_sha256({
            "generation_id": (brace.get("source") or {}).get("generation_id"),
            "candidate_signature": (brace.get("source") or {}).get("candidate_signature"),
            "brace_updated_at": (brace.get("source") or {}).get("updated_at"),
        })[:20]
        belief = select_frozen_belief_state(belief_raw, brace_at)
        rel = relationship(brace, belief)
        eligible = bool(brace.get("available")) and bool(belief.get("available")) and rel.get("class") != "UNAVAILABLE"
        record = {
            "record_id": record_id,
            "captured_at": _iso_z(now),
            "mode": MODE,
            "point_in_time": {
                "brace_state_at": _iso_z(brace_at),
                "belief_as_of": belief.get("forecast_at"),
                "prospective_after_activation": True,
                "historical_backfill": False,
            },
            "brace_spx": brace,
            "belief_state": belief,
            "relationship": rel,
            "engine_belief_calibration_eligible": eligible,
            "alpha_evaluation_enabled": False,
            "with_without_evaluation_enabled": False,
            "decision_influence": False,
            "bounded_modifier_applied": False,
            "controls": controls(),
            "provenance": {
                "brace_shadow_sha256": canonical_sha256(brace_raw),
                "belief_state_source_sha256": canonical_sha256(belief_raw),
            },
        }
        written = _append_jsonl_unique(observations_path, [record], "record_id")
        status = "captured" if written else "retry_idempotent_no_new_record"
        if written:
            bridge_state["last_capture_at"] = _iso_z(now)

    observations = []
    if observations_path.exists():
        for line in observations_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                observations.append(json.loads(line))
    bridge_state.update({
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "controls": controls(),
        "records_total": len(observations),
        "last_run_at": _iso_z(now),
        "last_status": status,
    })
    _write_json(state_path, bridge_state)
    report = _report(observations, bridge_state, now)
    _write_json(report_path, report)
    return {
        "status": status,
        "records_total": len(observations),
        "written": written,
        "engine_belief_calibration_eligible": report["sample"]["engine_belief_calibration_eligible"],
        "relationship_classes": report["sample"]["relationship_classes"],
        "decision_influence": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run prospective BRACE-SPX / Belief Core read-only bridge")
    parser.add_argument("--bridge-dir", required=True)
    parser.add_argument("--belief-state", required=True)
    parser.add_argument("--brace-shadow", required=True)
    parser.add_argument("--now", help="ISO timestamp override")
    args = parser.parse_args()
    now = _dt(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise ValueError("invalid --now")
    result = run_bridge(Path(args.bridge_dir), Path(args.belief_state), Path(args.brace_shadow), now)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
