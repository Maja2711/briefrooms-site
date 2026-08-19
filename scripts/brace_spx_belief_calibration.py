#!/usr/bin/env python3
"""Prospective BRACE-SPX Engine-Belief calibration and WITH/WITHOUT evaluator.

PR #7 is analytical only. It consumes immutable Engine-Belief observations from
PR #6, freezes a separate counterfactual contract before outcomes are known,
and later settles the original BRACE-SPX G6 parallel-candidate consensus versus
a small predeclared hypothetical Belief overlay on the same next-session SPX
return.

Nothing in this module can modify BRACE-SPX, Belief Core, candidate ranking,
engine scores, sizing, target exposure, orders, vetoes or production policy.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, time, timezone
from pathlib import Path
from statistics import fmean, median, stdev
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import brace_spx_belief_bridge as bridge

SCHEMA_VERSION = "brace-spx-engine-belief-calibration-v1"
REPORT_VERSION = "brace-spx-engine-belief-calibration-report-v1"
MODE = "shadow_research"
OVERLAY_POLICY_VERSION = "belief-exposure-sensitivity-v1"
MAX_HYPOTHETICAL_EXPOSURE_TILT = 0.10
MAX_CONTRACT_CAPTURE_DELAY_HOURS = 3.0
MAX_CAPTURE_CLOCK_SKEW_MINUTES = 5.0
COST_PER_UNIT_TURNOVER = 0.0005
SHORT_BORROW_ANNUAL = 0.01
SHORT_BORROW_DAILY = (1.0 + SHORT_BORROW_ANNUAL) ** (1.0 / 252.0) - 1.0
MIN_DESCRIPTIVE_N = 12
MIN_INCREMENTAL_MODEL_N = 40
TEMPORAL_TRAIN_MIN = 25
FAMILY_KEYS: Tuple[str, ...] = ("price_trend", "rates", "liquidity", "options_vix")
NY = ZoneInfo("America/New_York")
MARKET_SETTLEMENT_TIME = time(16, 20)


def safety_controls() -> Dict[str, bool]:
    return {
        "active_decision_influence": False,
        "exposure_change": False,
        "score_change": False,
        "veto": False,
        "sizing_change": False,
        "candidate_ranking_change": False,
        "trade_execution": False,
        "policy_output": False,
        "automatic_tuning": False,
        "bounded_influence_enabled": False,
        "bounded_modifier_applied": False,
        "historical_backfill_allowed": False,
    }


def evaluation_policy() -> Dict[str, Any]:
    return {
        "with_without_evaluation_enabled": True,
        "hypothetical_overlay_only": True,
        "overlay_policy_version": OVERLAY_POLICY_VERSION,
        "max_hypothetical_exposure_tilt": MAX_HYPOTHETICAL_EXPOSURE_TILT,
        "overlay_scaled_by_frozen_belief_confidence": True,
        "overlay_clipped_to_g6_exposure_mandate": [-1.0, 1.0],
        "primary_outcome_horizon": "next_trading_session_close_to_close",
        "g6_cost_per_unit_turnover": COST_PER_UNIT_TURNOVER,
        "g6_short_borrow_annual": SHORT_BORROW_ANNUAL,
        "production_modifier_proposed": False,
    }


def _assert_safety() -> None:
    enabled = [key for key, value in safety_controls().items() if value is not False]
    if enabled:
        raise RuntimeError("PR #7 safety invariant violated: " + ",".join(enabled))


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


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _mean(values: Iterable[float]) -> Optional[float]:
    rows = list(values)
    return fmean(rows) if rows else None


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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"{path.name} line {line_no} is not an object")
        rows.append(payload)
    return rows


def _append_jsonl_unique(path: Path, rows: Iterable[Mapping[str, Any]], key: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {str(row.get(key)) for row in _read_jsonl(path) if row.get(key)}
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


def _initial_state(now: datetime) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "activated_at": _iso_z(now),
        "controls": safety_controls(),
        "evaluation_policy": evaluation_policy(),
        "last_run_at": None,
        "last_status": "not_run",
    }


def _relationship_signed(row: Mapping[str, Any]) -> float:
    rel = str((row.get("relationship") or {}).get("class") or "UNAVAILABLE")
    strength = float((row.get("relationship") or {}).get("strength") or 0.0)
    if rel.endswith("AGREEMENT"):
        return strength
    if rel.endswith("CONFLICT"):
        return -strength
    return 0.0


def _belief_overlay(record: Mapping[str, Any], baseline_target: float) -> Dict[str, Any]:
    belief = record.get("belief_state") or {}
    stance = str(belief.get("stance") or "unavailable")
    confidence = _finite(belief.get("confidence"))
    if confidence is None:
        confidence = 0.0
    direction = 1.0 if stance == "risk_on" else -1.0 if stance == "defensive" else 0.0
    requested_tilt = direction * MAX_HYPOTHETICAL_EXPOSURE_TILT * max(0.0, min(1.0, confidence))
    adjusted = _clip(baseline_target + requested_tilt)
    applied_tilt = adjusted - baseline_target
    return {
        "policy_version": OVERLAY_POLICY_VERSION,
        "belief_stance": stance,
        "frozen_belief_confidence": round(confidence, 8),
        "max_absolute_tilt": MAX_HYPOTHETICAL_EXPOSURE_TILT,
        "requested_tilt": round(requested_tilt, 8),
        "applied_tilt_after_clipping": round(applied_tilt, 8),
        "hypothetical_target_exposure": round(adjusted, 8),
        "production_proposal": False,
    }


def _capture_window(brace_at: datetime, now: datetime) -> Dict[str, Any]:
    delay = (now - brace_at).total_seconds() / 3600.0
    if delay < -(MAX_CAPTURE_CLOCK_SKEW_MINUTES / 60.0):
        return {"eligible": False, "reason": "contract_capture_before_brace_state", "delay_hours": round(delay, 6)}
    if delay > MAX_CONTRACT_CAPTURE_DELAY_HOURS:
        return {"eligible": False, "reason": "counterfactual_contract_window_missed", "delay_hours": round(delay, 6)}
    return {"eligible": True, "reason": "prospective_contract_window_valid", "delay_hours": round(delay, 6)}


def _raw_shadow_matches(record: Mapping[str, Any], raw_shadow: Mapping[str, Any]) -> Tuple[bool, str]:
    expected_hash = str((record.get("provenance") or {}).get("brace_shadow_sha256") or "")
    actual_hash = bridge.canonical_sha256(raw_shadow) if raw_shadow else ""
    record_at = str(((record.get("brace_spx") or {}).get("source") or {}).get("updated_at") or "")
    raw_at = str(raw_shadow.get("updated_at") or "")
    if not raw_shadow:
        return False, "g6_raw_shadow_unavailable"
    if not expected_hash or actual_hash != expected_hash:
        return False, "g6_raw_shadow_hash_mismatch"
    if not record_at or raw_at != record_at:
        return False, "g6_raw_shadow_timestamp_mismatch"
    return True, "g6_raw_shadow_exact_match"


def build_counterfactual_contract(record: Mapping[str, Any], raw_shadow: Mapping[str, Any], now: datetime) -> Tuple[Optional[Dict[str, Any]], str]:
    """Freeze next-session WITH/WITHOUT inputs before outcomes are available."""
    if not bool(record.get("engine_belief_calibration_eligible")):
        return None, "bridge_record_not_calibration_eligible"
    brace_at = _dt(((record.get("brace_spx") or {}).get("source") or {}).get("updated_at"))
    if brace_at is None:
        return None, "brace_timestamp_missing"
    window = _capture_window(brace_at, now)
    if not window["eligible"]:
        return None, str(window["reason"])
    matches, reason = _raw_shadow_matches(record, raw_shadow)
    if not matches:
        return None, reason
    snapshots = [row for row in (raw_shadow.get("candidate_snapshots") or []) if isinstance(row, dict)]
    if len(snapshots) != 8:
        return None, "g6_candidate_snapshot_count_not_eight"
    targets = [_finite(row.get("target_exposure_next_session")) for row in snapshots]
    applied = [_finite(row.get("applied_exposure_latest_session")) for row in snapshots]
    if any(value is None for value in targets) or any(value is None for value in applied):
        return None, "g6_candidate_exposure_prerequisite_missing"
    frozen_hash = str(((record.get("brace_spx") or {}).get("candidate_consensus") or {}).get("candidate_snapshots_sha256") or "")
    if frozen_hash and bridge.canonical_sha256(snapshots) != frozen_hash:
        return None, "g6_candidate_snapshot_hash_mismatch"
    baseline_target = float(_mean(float(value) for value in targets if value is not None) or 0.0)
    previous_applied = float(_mean(float(value) for value in applied if value is not None) or 0.0)
    overlay = _belief_overlay(record, baseline_target)
    source = (record.get("brace_spx") or {}).get("source") or {}
    market_date = str(source.get("latest_market_date") or raw_shadow.get("latest_market_date") or "")
    if not market_date:
        return None, "g6_latest_market_date_missing"
    family_scores = record.get("brace_spx", {}).get("family_scores") or {}
    frozen_features = {key: _finite(family_scores.get(key)) for key in FAMILY_KEYS}
    contract_id = "brace-spx-belief-contract-" + bridge.canonical_sha256({
        "record_id": record.get("record_id"),
        "market_date": market_date,
        "overlay_policy_version": OVERLAY_POLICY_VERSION,
    })[:20]
    contract = {
        "contract_id": contract_id,
        "record_id": record.get("record_id"),
        "frozen_at": _iso_z(now),
        "mode": MODE,
        "prospective": True,
        "historical_backfill": False,
        "capture_window": window,
        "decision_market_date": market_date,
        "brace_state_at": source.get("updated_at"),
        "generation_id": source.get("generation_id"),
        "candidate_signature": source.get("candidate_signature"),
        "relationship": deepcopy(record.get("relationship") or {}),
        "brace_regime": (record.get("brace_spx") or {}).get("latest_regime"),
        "family_scores": frozen_features,
        "belief": {
            "forecast_set_id": (record.get("belief_state") or {}).get("forecast_set_id"),
            "forecast_at": (record.get("belief_state") or {}).get("forecast_at"),
            "stance": (record.get("belief_state") or {}).get("stance"),
            "confidence": (record.get("belief_state") or {}).get("confidence"),
            "risk_on_probability_mean": (record.get("belief_state") or {}).get("risk_on_probability_mean"),
            "snapshot_sha256": (record.get("belief_state") or {}).get("snapshot_sha256"),
        },
        "without_belief": {
            "decision_type": "g6_parallel_candidate_consensus",
            "target_exposure_next_session": round(baseline_target, 8),
            "previous_applied_exposure": round(previous_applied, 8),
            "candidate_count": 8,
        },
        "with_belief_hypothetical": overlay,
        "accounting_contract": {
            "asset": "SPY",
            "risk_free_symbol": "^IRX",
            "return_horizon": "next_trading_session_close_to_close",
            "turnover_cost_per_unit": COST_PER_UNIT_TURNOVER,
            "short_borrow_annual": SHORT_BORROW_ANNUAL,
            "cash_weight_rule": "1-abs(exposure)",
            "same_previous_applied_exposure_for_both_variants": True,
        },
        "source_provenance": {
            "bridge_record_sha256": bridge.canonical_sha256(record),
            "g6_raw_shadow_sha256": bridge.canonical_sha256(raw_shadow),
            "candidate_snapshots_sha256": bridge.canonical_sha256(snapshots),
        },
        "controls": safety_controls(),
        "decision_influence": False,
        "bounded_modifier_applied": False,
    }
    return contract, "contract_frozen"


def load_market_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            day = str(raw.get("date") or "")
            try:
                parsed = date.fromisoformat(day)
            except ValueError:
                continue
            spy = _finite(raw.get("spy_close"))
            irx = _finite(raw.get("irx_annual_yield"))
            if spy is None:
                continue
            rows.append({"date": parsed, "spy_close": spy, "irx_annual_yield": irx})
    rows.sort(key=lambda row: row["date"])
    return rows


def _session_closed(session_date: date, now: datetime) -> bool:
    local = now.astimezone(NY)
    if session_date < local.date():
        return True
    if session_date > local.date():
        return False
    return local.time().replace(tzinfo=None) >= MARKET_SETTLEMENT_TIME


def _risk_free_yield(market_rows: Sequence[Mapping[str, Any]], index: int) -> Optional[float]:
    for pos in range(index, -1, -1):
        value = _finite(market_rows[pos].get("irx_annual_yield"))
        if value is not None:
            return max(0.0, value)
    return None


def one_session_portfolio_return(exposure: float, previous_exposure: float, asset_return: float, annual_rf_yield: float) -> Dict[str, float]:
    exposure = _clip(exposure)
    previous = _clip(previous_exposure)
    turnover = abs(exposure - previous)
    cash_weight = max(0.0, 1.0 - abs(exposure))
    risk_free_return = (1.0 + max(0.0, annual_rf_yield) / 100.0) ** (1.0 / 252.0) - 1.0
    short_borrow = abs(min(exposure, 0.0)) * SHORT_BORROW_DAILY
    turnover_cost = turnover * COST_PER_UNIT_TURNOVER
    asset_component = exposure * asset_return
    cash_component = cash_weight * risk_free_return
    total = asset_component + cash_component - short_borrow - turnover_cost
    return {
        "net_return": total,
        "asset_component": asset_component,
        "cash_component": cash_component,
        "short_borrow_cost": short_borrow,
        "turnover": turnover,
        "turnover_cost": turnover_cost,
        "risk_free_daily_return": risk_free_return,
    }


def settle_contract(contract: Mapping[str, Any], market_rows: Sequence[Mapping[str, Any]], now: datetime) -> Tuple[Optional[Dict[str, Any]], str]:
    try:
        base_date = date.fromisoformat(str(contract.get("decision_market_date") or ""))
    except ValueError:
        return None, "decision_market_date_invalid"
    base_index = next((i for i, row in enumerate(market_rows) if row.get("date") == base_date), None)
    if base_index is None:
        return None, "base_market_date_not_available"
    next_index = next((i for i in range(base_index + 1, len(market_rows)) if market_rows[i].get("date") > base_date), None)
    if next_index is None:
        return None, "next_trading_session_not_available"
    next_row = market_rows[next_index]
    if not _session_closed(next_row["date"], now):
        return None, "next_trading_session_not_closed"
    base_close = _finite(market_rows[base_index].get("spy_close"))
    next_close = _finite(next_row.get("spy_close"))
    annual_rf = _risk_free_yield(market_rows, next_index)
    if base_close is None or next_close is None or base_close <= 0 or annual_rf is None:
        return None, "market_settlement_prerequisite_missing"
    asset_return = next_close / base_close - 1.0
    without_target = float((contract.get("without_belief") or {}).get("target_exposure_next_session") or 0.0)
    previous = float((contract.get("without_belief") or {}).get("previous_applied_exposure") or 0.0)
    with_target = float((contract.get("with_belief_hypothetical") or {}).get("hypothetical_target_exposure") or without_target)
    without = one_session_portfolio_return(without_target, previous, asset_return, annual_rf)
    with_belief = one_session_portfolio_return(with_target, previous, asset_return, annual_rf)
    delta = with_belief["net_return"] - without["net_return"]
    relationship_class = str((contract.get("relationship") or {}).get("class") or "UNAVAILABLE")
    belief_stance = str((contract.get("belief") or {}).get("stance") or "unavailable")
    belief_direction = 1.0 if belief_stance == "risk_on" else -1.0 if belief_stance == "defensive" else 0.0
    without_hit = None if abs(without_target) < 1e-12 else bool(without_target * asset_return > 0.0)
    belief_hit = None if belief_direction == 0.0 else bool(belief_direction * asset_return > 0.0)
    conflict_warning_hit = bool(without["net_return"] < 0.0) if relationship_class.endswith("CONFLICT") else None
    agreement_confirmation_hit = bool(without["net_return"] > 0.0) if relationship_class.endswith("AGREEMENT") else None
    settlement = {
        "settlement_id": "brace-spx-belief-settlement-" + bridge.canonical_sha256({
            "contract_id": contract.get("contract_id"),
            "next_market_date": next_row["date"].isoformat(),
        })[:20],
        "contract_id": contract.get("contract_id"),
        "record_id": contract.get("record_id"),
        "settled_at": _iso_z(now),
        "decision_market_date": base_date.isoformat(),
        "next_market_date": next_row["date"].isoformat(),
        "forward_spx_return": asset_return,
        "annual_rf_yield_used": annual_rf,
        "relationship": deepcopy(contract.get("relationship") or {}),
        "without_belief": {
            "target_exposure": without_target,
            **without,
            "directional_hit": without_hit,
        },
        "with_belief_hypothetical": {
            "target_exposure": with_target,
            **with_belief,
            "directional_hit": None if abs(with_target) < 1e-12 else bool(with_target * asset_return > 0.0),
        },
        "belief_directional_hit": belief_hit,
        "conflict_warning_hit": conflict_warning_hit,
        "agreement_confirmation_hit": agreement_confirmation_hit,
        "delta_pnl": delta,
        "delta_pnl_percentage_points": delta * 100.0,
        "market_provenance": {
            "base_spy_close": base_close,
            "next_spy_close": next_close,
            "market_rows_sha256": bridge.canonical_sha256([
                {"date": market_rows[base_index]["date"].isoformat(), "spy_close": base_close},
                {"date": next_row["date"].isoformat(), "spy_close": next_close, "irx_annual_yield": annual_rf},
            ]),
        },
        "controls": safety_controls(),
        "decision_influence": False,
    }
    return settlement, "settled"


def _rate(values: Iterable[Optional[bool]]) -> Optional[float]:
    rows = [bool(value) for value in values if value is not None]
    return sum(rows) / len(rows) if rows else None


def _mean_field(rows: Sequence[Mapping[str, Any]], getter) -> Optional[float]:
    values = [value for value in (getter(row) for row in rows) if value is not None and math.isfinite(float(value))]
    return _mean(float(value) for value in values)


def _cumulative_return(returns: Sequence[float]) -> float:
    equity = 1.0
    for value in returns:
        equity *= 1.0 + float(value)
    return equity - 1.0


def _max_drawdown(returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + float(value)
        peak = max(peak, equity)
        drawdown = equity / peak - 1.0
        worst = min(worst, drawdown)
    return worst


def _sharpe(returns: Sequence[float]) -> Optional[float]:
    if len(returns) < 2:
        return None
    sigma = stdev(returns)
    if sigma <= 1e-15:
        return None
    return math.sqrt(252.0) * fmean(returns) / sigma


def _relationship_slice(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    without_returns = [float((row.get("without_belief") or {}).get("net_return") or 0.0) for row in rows]
    with_returns = [float((row.get("with_belief_hypothetical") or {}).get("net_return") or 0.0) for row in rows]
    deltas = [float(row.get("delta_pnl") or 0.0) for row in rows]
    return {
        "n": len(rows),
        "mean_forward_spx_return": _mean_field(rows, lambda r: _finite(r.get("forward_spx_return"))),
        "mean_without_belief_return": _mean(without_returns),
        "mean_with_belief_return": _mean(with_returns),
        "mean_delta_pnl": _mean(deltas),
        "without_directional_hit_rate": _rate((row.get("without_belief") or {}).get("directional_hit") for row in rows),
        "belief_directional_hit_rate": _rate(row.get("belief_directional_hit") for row in rows),
        "conflict_warning_hit_rate": _rate(row.get("conflict_warning_hit") for row in rows),
        "agreement_confirmation_hit_rate": _rate(row.get("agreement_confirmation_hit") for row in rows),
    }


def _relationship_group(name: str) -> str:
    if name.endswith("AGREEMENT"):
        return "AGREEMENT"
    if name.endswith("CONFLICT"):
        return "CONFLICT"
    if name == "NEUTRAL":
        return "NEUTRAL"
    return "UNAVAILABLE"


def _incremental_information(settlements: Sequence[Mapping[str, Any]], contracts: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    rows = []
    for settlement in sorted(settlements, key=lambda row: str(row.get("decision_market_date") or "")):
        contract = contracts.get(str(settlement.get("contract_id") or ""))
        if not contract:
            continue
        family = contract.get("family_scores") or {}
        baseline_features = [
            _finite((contract.get("without_belief") or {}).get("target_exposure_next_session")),
            *[_finite(family.get(key)) for key in FAMILY_KEYS],
        ]
        belief = contract.get("belief") or {}
        augmented_extra = [
            _finite(belief.get("risk_on_probability_mean")),
            _finite(belief.get("confidence")),
            _relationship_signed(contract),
        ]
        y = _finite(settlement.get("forward_spx_return"))
        if y is None or any(value is None for value in baseline_features + augmented_extra):
            continue
        rows.append((str(settlement.get("decision_market_date")), [float(x) for x in baseline_features], [float(x) for x in baseline_features + augmented_extra], y))
    if len(rows) < MIN_INCREMENTAL_MODEL_N:
        return {
            "status": "insufficient_sample",
            "n": len(rows),
            "minimum_n": MIN_INCREMENTAL_MODEL_N,
            "method": "expanding_window_point_in_time_linear_prediction",
            "baseline_features": ["g6_consensus_exposure", *FAMILY_KEYS],
            "augmented_features": ["g6_consensus_exposure", *FAMILY_KEYS, "belief_probability", "belief_confidence", "relationship_signed"],
            "mse_without_belief_features": None,
            "mse_with_belief_features": None,
            "mse_improvement": None,
            "directional_accuracy_without": None,
            "directional_accuracy_with": None,
        }
    try:
        import numpy as np
    except ImportError:
        return {"status": "numpy_unavailable", "n": len(rows)}

    def predict(train_x: Sequence[Sequence[float]], train_y: Sequence[float], test_x: Sequence[float]) -> float:
        x = np.asarray(train_x, dtype=float)
        y = np.asarray(train_y, dtype=float)
        test = np.asarray(test_x, dtype=float)
        mean = x.mean(axis=0)
        std = x.std(axis=0)
        std[std < 1e-12] = 1.0
        xz = (x - mean) / std
        tz = (test - mean) / std
        design = np.column_stack([np.ones(len(xz)), xz])
        beta = np.linalg.lstsq(design, y, rcond=None)[0]
        return float(np.dot(np.r_[1.0, tz], beta))

    actual: list[float] = []
    pred_base: list[float] = []
    pred_aug: list[float] = []
    for i in range(TEMPORAL_TRAIN_MIN, len(rows)):
        train = rows[:i]
        actual.append(rows[i][3])
        pred_base.append(predict([row[1] for row in train], [row[3] for row in train], rows[i][1]))
        pred_aug.append(predict([row[2] for row in train], [row[3] for row in train], rows[i][2]))
    mse_base = fmean((p - y) ** 2 for p, y in zip(pred_base, actual))
    mse_aug = fmean((p - y) ** 2 for p, y in zip(pred_aug, actual))
    acc_base = fmean(float((p >= 0) == (y >= 0)) for p, y in zip(pred_base, actual))
    acc_aug = fmean(float((p >= 0) == (y >= 0)) for p, y in zip(pred_aug, actual))
    return {
        "status": "measured_no_promotion_implication",
        "n": len(rows),
        "out_of_sample_predictions": len(actual),
        "method": "expanding_window_point_in_time_linear_prediction",
        "minimum_training_rows": TEMPORAL_TRAIN_MIN,
        "baseline_features": ["g6_consensus_exposure", *FAMILY_KEYS],
        "augmented_features": ["g6_consensus_exposure", *FAMILY_KEYS, "belief_probability", "belief_confidence", "relationship_signed"],
        "mse_without_belief_features": mse_base,
        "mse_with_belief_features": mse_aug,
        "mse_improvement": mse_base - mse_aug,
        "directional_accuracy_without": acc_base,
        "directional_accuracy_with": acc_aug,
        "directional_accuracy_uplift": acc_aug - acc_base,
        "promotion_decision": False,
    }


def build_report(
    bridge_records: Sequence[Mapping[str, Any]],
    contracts: Sequence[Mapping[str, Any]],
    settlements: Sequence[Mapping[str, Any]],
    misses: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    now: datetime,
) -> Dict[str, Any]:
    sorted_settlements = sorted(settlements, key=lambda row: str(row.get("decision_market_date") or ""))
    contract_map = {str(row.get("contract_id")): row for row in contracts}
    without_returns = [float((row.get("without_belief") or {}).get("net_return") or 0.0) for row in sorted_settlements]
    with_returns = [float((row.get("with_belief_hypothetical") or {}).get("net_return") or 0.0) for row in sorted_settlements]
    deltas = [float(row.get("delta_pnl") or 0.0) for row in sorted_settlements]
    by_class: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_group: Dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in sorted_settlements:
        cls = str((row.get("relationship") or {}).get("class") or "UNAVAILABLE")
        by_class[cls].append(row)
        by_group[_relationship_group(cls)].append(row)
    settled_n = len(sorted_settlements)
    without_dd = _max_drawdown(without_returns)
    with_dd = _max_drawdown(with_returns)
    if settled_n < MIN_DESCRIPTIVE_N:
        status = "collecting_prospective_outcomes"
    elif settled_n < MIN_INCREMENTAL_MODEL_N:
        status = "descriptive_calibration_available_collecting_incremental_model_sample"
    else:
        status = "engine_belief_calibration_measured_research_only"
    report = {
        "schema_version": REPORT_VERSION,
        "report_name": "BRACE_SPX_ENGINE_BELIEF_CALIBRATION_REPORT",
        "generated_at": _iso_z(now),
        "mode": MODE,
        "controls": safety_controls(),
        "evaluation_policy": evaluation_policy(),
        "activation": {
            "activated_at": state.get("activated_at"),
            "prospective_only": True,
            "historical_backfill_allowed": False,
            "counterfactual_contract_must_be_frozen_before_outcome": True,
        },
        "sample": {
            "bridge_records_total": len(bridge_records),
            "bridge_records_calibration_eligible": sum(bool(row.get("engine_belief_calibration_eligible")) for row in bridge_records),
            "counterfactual_contracts_frozen": len(contracts),
            "resolved_with_without_pairs": settled_n,
            "missed_contracts_not_reconstructed": len(misses),
            "effective_n": settled_n,
            "one_record_per_market_date_cap": True,
            "minimum_descriptive_n": MIN_DESCRIPTIVE_N,
            "minimum_incremental_model_n": MIN_INCREMENTAL_MODEL_N,
        },
        "engine_belief_relationship_calibration": {
            "by_relationship_class": {key: _relationship_slice(rows) for key, rows in sorted(by_class.items())},
            "by_relationship_group": {key: _relationship_slice(rows) for key, rows in sorted(by_group.items())},
            "conflict_warning_question": "Does BRACE-SPX/Belief conflict precede negative original G6 consensus return?",
            "agreement_confirmation_question": "Does agreement coincide with positive original G6 consensus return?",
        },
        "with_vs_without_belief": {
            "resolved_pairs": settled_n,
            "without_belief_cumulative_return": _cumulative_return(without_returns),
            "with_belief_hypothetical_cumulative_return": _cumulative_return(with_returns),
            "delta_cumulative_return": _cumulative_return(with_returns) - _cumulative_return(without_returns),
            "without_belief_max_drawdown": without_dd,
            "with_belief_hypothetical_max_drawdown": with_dd,
            "delta_drawdown": with_dd - without_dd,
            "without_belief_annualized_sharpe": _sharpe(without_returns),
            "with_belief_hypothetical_annualized_sharpe": _sharpe(with_returns),
            "delta_sharpe": None if _sharpe(without_returns) is None or _sharpe(with_returns) is None else _sharpe(with_returns) - _sharpe(without_returns),
            "mean_delta_pnl": _mean(deltas),
            "median_delta_pnl": median(deltas) if deltas else None,
            "worst_delta_pnl": min(deltas) if deltas else None,
            "best_delta_pnl": max(deltas) if deltas else None,
            "with_belief_outperforms_rate": _rate(value > 0.0 for value in deltas) if deltas else None,
        },
        "incremental_information_over_existing_g6_features": _incremental_information(sorted_settlements, contract_map),
        "promotion_gate": {
            "evaluated": False,
            "eligible": False,
            "bounded_influence_allowed": False,
            "reason": "PR7_is_research_calibration_only; promotion_requires_separate_review_after_sufficient_stable_evidence",
        },
        "decision_influence": False,
        "bounded_modifier": False,
        "status": status,
    }
    return report


def run_calibration(
    calibration_dir: Path,
    bridge_dir: Path,
    raw_shadow_path: Path,
    market_csv_path: Path,
    now: datetime,
) -> Dict[str, Any]:
    _assert_safety()
    calibration_dir.mkdir(parents=True, exist_ok=True)
    state_path = calibration_dir / "calibration_state.json"
    contracts_path = calibration_dir / "counterfactual_contracts.jsonl"
    settlements_path = calibration_dir / "settlements.jsonl"
    misses_path = calibration_dir / "missed_contracts.jsonl"
    report_path = calibration_dir / "BRACE_SPX_ENGINE_BELIEF_CALIBRATION_REPORT.json"
    bridge_records_path = bridge_dir / "engine_belief_observations.jsonl"

    existed = state_path.exists()
    state = _read_json(state_path, _initial_state(now))
    activated_at = _dt(state.get("activated_at")) or now
    bridge_records = _read_jsonl(bridge_records_path)
    raw_shadow = _read_json(raw_shadow_path, {})
    contracts = _read_jsonl(contracts_path)
    settlements = _read_jsonl(settlements_path)
    misses = _read_jsonl(misses_path)
    existing_contract_records = {str(row.get("record_id")) for row in contracts}
    missed_records = {str(row.get("record_id")) for row in misses}
    contracted_market_dates = {str(row.get("decision_market_date")) for row in contracts}

    new_contracts: list[Mapping[str, Any]] = []
    new_misses: list[Mapping[str, Any]] = []
    if existed:
        for record in bridge_records:
            record_id = str(record.get("record_id") or "")
            if not record_id or record_id in existing_contract_records or record_id in missed_records:
                continue
            if not bool(record.get("engine_belief_calibration_eligible")):
                continue
            brace_at = _dt(((record.get("brace_spx") or {}).get("source") or {}).get("updated_at"))
            if brace_at is None:
                continue
            if brace_at < activated_at:
                new_misses.append({
                    "miss_id": "miss-" + bridge.canonical_sha256({"record_id": record_id, "reason": "pre_activation"})[:20],
                    "record_id": record_id,
                    "recorded_at": _iso_z(now),
                    "reason": "pre_calibration_activation_record_not_reconstructed",
                    "historical_backfill": False,
                })
                continue
            market_date = str(((record.get("brace_spx") or {}).get("source") or {}).get("latest_market_date") or "")
            if market_date and market_date in contracted_market_dates:
                new_misses.append({
                    "miss_id": "miss-" + bridge.canonical_sha256({"record_id": record_id, "reason": "duplicate_market_date"})[:20],
                    "record_id": record_id,
                    "recorded_at": _iso_z(now),
                    "reason": "duplicate_market_date_non_independent",
                    "historical_backfill": False,
                })
                continue
            contract, reason = build_counterfactual_contract(record, raw_shadow, now)
            if contract is not None:
                new_contracts.append(contract)
                contracted_market_dates.add(str(contract.get("decision_market_date")))
            else:
                window = _capture_window(brace_at, now)
                if not window["eligible"] or reason not in {"g6_raw_shadow_unavailable"}:
                    new_misses.append({
                        "miss_id": "miss-" + bridge.canonical_sha256({"record_id": record_id, "reason": reason})[:20],
                        "record_id": record_id,
                        "recorded_at": _iso_z(now),
                        "reason": reason,
                        "historical_backfill": False,
                    })
    _append_jsonl_unique(contracts_path, new_contracts, "contract_id")
    _append_jsonl_unique(misses_path, new_misses, "miss_id")
    contracts = _read_jsonl(contracts_path)
    misses = _read_jsonl(misses_path)

    market_rows = load_market_csv(market_csv_path)
    settled_contract_ids = {str(row.get("contract_id")) for row in settlements}
    new_settlements: list[Mapping[str, Any]] = []
    pending_reasons: Counter[str] = Counter()
    for contract in contracts:
        contract_id = str(contract.get("contract_id") or "")
        if not contract_id or contract_id in settled_contract_ids:
            continue
        settlement, reason = settle_contract(contract, market_rows, now)
        if settlement is not None:
            new_settlements.append(settlement)
        else:
            pending_reasons[reason] += 1
    _append_jsonl_unique(settlements_path, new_settlements, "settlement_id")
    settlements = _read_jsonl(settlements_path)

    state.update({
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "controls": safety_controls(),
        "evaluation_policy": evaluation_policy(),
        "last_run_at": _iso_z(now),
        "last_status": "activated_waiting_for_next_prospective_bridge_record" if not existed else "collecting_and_settling_prospective_pairs",
        "contracts_total": len(contracts),
        "settlements_total": len(settlements),
        "missed_contracts_total": len(misses),
        "pending_outcome_reasons": dict(sorted(pending_reasons.items())),
    })
    _write_json(state_path, state)
    report = build_report(bridge_records, contracts, settlements, misses, state, now)
    _write_json(report_path, report)
    return {
        "status": state["last_status"],
        "new_contracts": len(new_contracts),
        "contracts_total": len(contracts),
        "new_settlements": len(new_settlements),
        "resolved_pairs": len(settlements),
        "missed_contracts": len(misses),
        "pending_outcome_reasons": dict(sorted(pending_reasons.items())),
        "decision_influence": False,
        "bounded_modifier": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BRACE-SPX Engine-Belief prospective calibration")
    parser.add_argument("--calibration-dir", required=True)
    parser.add_argument("--bridge-dir", required=True)
    parser.add_argument("--brace-shadow", required=True)
    parser.add_argument("--market-csv", required=True)
    parser.add_argument("--now", help="ISO timestamp override")
    args = parser.parse_args()
    now = _dt(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise ValueError("invalid --now")
    result = run_calibration(
        Path(args.calibration_dir),
        Path(args.bridge_dir),
        Path(args.brace_shadow),
        Path(args.market_csv),
        now,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
