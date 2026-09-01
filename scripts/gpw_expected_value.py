#!/usr/bin/env python3
"""Empirical 1-2 session Expected Value model for GPW Daily Trading.

P0.2 replaces the mechanically fixed 1.8 R target with a walk-forward estimate
based only on completed historical sessions available before the live decision.
For each candidate the model finds similar historical setup states, simulates a
small grid of reward/risk targets and estimates:
- P(TP before SL),
- P(SL before TP),
- P(time exit),
- net expected R after the same paper cost assumption used by settlement.

Daily OHLC cannot reveal intraday ordering when both TP and SL are inside the
same candle, therefore the model resolves such ambiguity conservatively as
STOP first.  The model is deterministic and deliberately bounded; it is an
empirical decision aid, not a calibrated probability forecast.
"""
from __future__ import annotations

import copy
import math
import statistics
from typing import Any

try:
    from scripts import daily_stock_core as core
except ModuleNotFoundError:
    import daily_stock_core as core


ENGINE = "gpw-empirical-ev-v1"
DEFAULT_RR_GRID = (1.50, 1.75, 2.00, 2.25, 2.50)
DEFAULT_HORIZON_SESSIONS = 2
DEFAULT_MIN_ANALOGS = 16
DEFAULT_MAX_ANALOGS = 32
DEFAULT_COST_PERCENT = 0.38
DEFAULT_SCORE_WEIGHT = 0.20
MAX_SCORE_WEIGHT = 0.35
DEFAULT_UNCERTAINTY_PENALTY = 0.35
MIN_FEATURE_INDEX = 50


def _clamp_fraction(value: Any, default: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(maximum, parsed))


def settings_from(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return normalized EV settings from either the root or EV subsection."""
    source = config or {}
    raw = source.get("expected_value") if isinstance(source.get("expected_value"), dict) else source
    raw = raw or {}
    grid: list[float] = []
    for value in raw.get("rr_grid", DEFAULT_RR_GRID):
        try:
            parsed = round(float(value), 2)
        except (TypeError, ValueError):
            continue
        if 1.0 <= parsed <= 3.5:
            grid.append(parsed)
    grid = sorted(set(grid)) or list(DEFAULT_RR_GRID)
    minimum = max(8, int(raw.get("minimum_analogs", DEFAULT_MIN_ANALOGS)))
    maximum = max(minimum, int(raw.get("maximum_analogs", DEFAULT_MAX_ANALOGS)))
    return {
        "enabled": bool(raw.get("enabled", False)),
        "engine": str(raw.get("engine") or ENGINE),
        "horizon_sessions": max(1, min(3, int(raw.get("horizon_sessions", DEFAULT_HORIZON_SESSIONS)))),
        "rr_grid": grid,
        "minimum_analogs": minimum,
        "maximum_analogs": maximum,
        "cost_assumption_percent": max(0.0, float(raw.get("cost_assumption_percent", DEFAULT_COST_PERCENT))),
        "score_weight": _clamp_fraction(raw.get("score_weight", DEFAULT_SCORE_WEIGHT), DEFAULT_SCORE_WEIGHT, MAX_SCORE_WEIGHT),
        "uncertainty_penalty": max(0.0, min(1.5, float(raw.get("uncertainty_penalty", DEFAULT_UNCERTAINTY_PENALTY)))),
        "confidence_sample": max(minimum, int(raw.get("confidence_sample", 28))),
        "role": str(raw.get("role") or "empirical_target_and_bounded_reranking_overlay"),
    }


def _atr_at(bars: list[Any], index: int, window: int = 14) -> float:
    if index <= 0:
        return 0.0
    start = max(1, index - window + 1)
    values: list[float] = []
    for cursor in range(start, index + 1):
        previous = bars[cursor - 1]
        current = bars[cursor]
        values.append(
            max(
                float(current.high) - float(current.low),
                abs(float(current.high) - float(previous.close)),
                abs(float(current.low) - float(previous.close)),
            )
        )
    return statistics.fmean(values) if values else 0.0


def _return_at(bars: list[Any], index: int, sessions: int) -> float:
    if index < sessions:
        return 0.0
    base = float(bars[index - sessions].close or 0.0)
    if base <= 0.0:
        return 0.0
    return float(bars[index].close) / base - 1.0


def _feature_vector(bars: list[Any], index: int) -> dict[str, float] | None:
    if index < MIN_FEATURE_INDEX or index >= len(bars):
        return None
    close = float(bars[index].close or 0.0)
    if close <= 0.0:
        return None
    atr = _atr_at(bars, index)
    if atr <= 0.0:
        return None
    previous_volumes = [max(int(bar.volume or 0), 0) for bar in bars[index - 20 : index]]
    average_volume = statistics.fmean(previous_volumes) if previous_volumes else 1.0
    volume_ratio = max(int(bars[index].volume or 0), 0) / max(average_volume, 1.0)
    ma20 = statistics.fmean(float(bar.close) for bar in bars[index - 19 : index + 1])
    ma50 = statistics.fmean(float(bar.close) for bar in bars[index - 49 : index + 1])
    return {
        "ret5": _return_at(bars, index, 5),
        "ret20": _return_at(bars, index, 20),
        "atr_pct": atr / close,
        "log_volume_ratio": math.log(max(volume_ratio, 0.05)),
        "above_ma20": 1.0 if close > ma20 else 0.0,
        "ma20_above_ma50": 1.0 if ma20 > ma50 else 0.0,
    }


def _distance(current: dict[str, float], historical: dict[str, float]) -> float:
    """Dimensionless setup distance with robust, finance-oriented scales."""
    return (
        0.35 * abs(current["ret5"] - historical["ret5"]) / 0.04
        + 0.25 * abs(current["ret20"] - historical["ret20"]) / 0.08
        + 0.20 * abs(current["atr_pct"] - historical["atr_pct"]) / 0.015
        + 0.10 * abs(current["log_volume_ratio"] - historical["log_volume_ratio"]) / 0.75
        + 0.05 * abs(current["above_ma20"] - historical["above_ma20"])
        + 0.05 * abs(current["ma20_above_ma50"] - historical["ma20_above_ma50"])
    )


def _risk_fraction_at(bars: list[Any], index: int) -> float:
    close = float(bars[index].close or 0.0)
    if close <= 0.0:
        return 0.0
    atr_pct = _atr_at(bars, index) / close
    return max(atr_pct * core.GPW_PROFILE.risk_atr_multiple, core.GPW_PROFILE.risk_floor_percent)


def _simulate(
    bars: list[Any],
    anchor: int,
    *,
    rr: float,
    horizon: int,
    cost_fraction: float,
) -> dict[str, float | str] | None:
    if anchor + horizon >= len(bars):
        return None
    entry = float(bars[anchor + 1].open or 0.0)
    risk_fraction = _risk_fraction_at(bars, anchor)
    if entry <= 0.0 or risk_fraction <= 0.0 or risk_fraction > 0.07:
        return None
    risk_amount = entry * risk_fraction
    stop = entry - risk_amount
    target = entry + risk_amount * rr
    exit_price = float(bars[anchor + horizon].close)
    outcome = "time"

    for step in range(1, horizon + 1):
        bar = bars[anchor + step]
        hit_stop = float(bar.low) <= stop
        hit_target = float(bar.high) >= target
        # Conservative and consistent with the production settlement order.
        if hit_stop:
            exit_price = stop
            outcome = "stop"
            break
        if hit_target:
            exit_price = target
            outcome = "target"
            break

    gross_r = (exit_price - entry) / max(risk_amount, 1e-9)
    cost_r = cost_fraction / max(risk_fraction, 1e-9)
    return {
        "outcome": outcome,
        "gross_r": gross_r,
        "net_r": gross_r - cost_r,
        "risk_fraction": risk_fraction,
    }


def _weighted_stats(rows: list[tuple[float, dict[str, float | str]]], uncertainty_penalty: float) -> dict[str, float]:
    total_weight = sum(weight for weight, _ in rows)
    if total_weight <= 0.0:
        raise ValueError("non-positive analogue weights")
    gross = sum(weight * float(result["gross_r"]) for weight, result in rows) / total_weight
    net = sum(weight * float(result["net_r"]) for weight, result in rows) / total_weight
    variance = sum(
        weight * (float(result["net_r"]) - net) ** 2
        for weight, result in rows
    ) / total_weight
    squared_weights = sum(weight * weight for weight, _ in rows)
    effective_n = total_weight * total_weight / max(squared_weights, 1e-12)
    stderr = math.sqrt(max(variance, 0.0) / max(effective_n, 1.0))
    outcome_weight = {"target": 0.0, "stop": 0.0, "time": 0.0}
    for weight, result in rows:
        outcome_weight[str(result["outcome"])] += weight
    return {
        "expected_gross_r": gross,
        "expected_net_r": net,
        "conservative_ev_r": net - uncertainty_penalty * stderr,
        "standard_error_r": stderr,
        "effective_sample_size": effective_n,
        "tp_probability": outcome_weight["target"] / total_weight,
        "sl_probability": outcome_weight["stop"] / total_weight,
        "time_probability": outcome_weight["time"] / total_weight,
    }


def estimate(
    candidate: dict[str, Any],
    bars: list[Any],
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Estimate dynamic target and empirical EV for one completed-session setup."""
    settings = settings_from(config)
    result: dict[str, Any] = {
        "engine": ENGINE,
        "status": "disabled" if not settings["enabled"] else "insufficient_history",
        "horizon_sessions": settings["horizon_sessions"],
        "rr_grid": list(settings["rr_grid"]),
        "same_bar_policy": "stop_first_conservative",
        "entry_proxy": "next_session_open",
        "cost_assumption_percent": settings["cost_assumption_percent"],
        "role": settings["role"],
    }
    if not settings["enabled"]:
        return result

    completed = list(bars or [])
    if len(completed) < MIN_FEATURE_INDEX + settings["horizon_sessions"] + 2:
        result["available_bars"] = len(completed)
        return result

    current_index = len(completed) - 1
    current_features = _feature_vector(completed, current_index)
    if current_features is None:
        result["available_bars"] = len(completed)
        return result

    analogs: list[tuple[float, int]] = []
    last_anchor = current_index - settings["horizon_sessions"]
    for anchor in range(MIN_FEATURE_INDEX, last_anchor + 1):
        features = _feature_vector(completed, anchor)
        if features is None:
            continue
        risk_fraction = _risk_fraction_at(completed, anchor)
        if risk_fraction <= 0.0 or risk_fraction > 0.07:
            continue
        analogs.append((_distance(current_features, features), anchor))

    analogs.sort(key=lambda item: item[0])
    analogs = analogs[: settings["maximum_analogs"]]
    result["analogue_count"] = len(analogs)
    if len(analogs) < settings["minimum_analogs"]:
        return result

    # Similar states carry more weight, but no single analogue can dominate.
    weighted_anchors = [
        (1.0 / (1.0 + distance) ** 2, anchor, distance)
        for distance, anchor in analogs
    ]
    grid_results: list[dict[str, Any]] = []
    cost_fraction = settings["cost_assumption_percent"] / 100.0
    for rr in settings["rr_grid"]:
        simulated: list[tuple[float, dict[str, float | str]]] = []
        for weight, anchor, _distance_value in weighted_anchors:
            path = _simulate(
                completed,
                anchor,
                rr=float(rr),
                horizon=settings["horizon_sessions"],
                cost_fraction=cost_fraction,
            )
            if path is not None:
                simulated.append((weight, path))
        if len(simulated) < settings["minimum_analogs"]:
            continue
        stats = _weighted_stats(simulated, settings["uncertainty_penalty"])
        grid_results.append(
            {
                "reward_risk": float(rr),
                "expected_gross_r": round(stats["expected_gross_r"], 4),
                "expected_net_r": round(stats["expected_net_r"], 4),
                "conservative_ev_r": round(stats["conservative_ev_r"], 4),
                "standard_error_r": round(stats["standard_error_r"], 4),
                "effective_sample_size": round(stats["effective_sample_size"], 2),
                "tp_before_sl_probability": round(stats["tp_probability"], 4),
                "sl_before_tp_probability": round(stats["sl_probability"], 4),
                "time_exit_probability": round(stats["time_probability"], 4),
            }
        )

    if not grid_results:
        return result

    # Optimize the uncertainty-adjusted EV; on a tie prefer the less ambitious target.
    selected = max(
        grid_results,
        key=lambda row: (
            float(row["conservative_ev_r"]),
            float(row["expected_net_r"]),
            -float(row["reward_risk"]),
        ),
    )
    ev_score = core.clamp(50.0 + float(selected["conservative_ev_r"]) * 35.0)
    confidence = min(
        1.0,
        float(selected["effective_sample_size"]) / float(settings["confidence_sample"]),
    )
    result.update(
        {
            "status": "ready",
            "selected_reward_risk": float(selected["reward_risk"]),
            "expected_gross_r": float(selected["expected_gross_r"]),
            "expected_net_r": float(selected["expected_net_r"]),
            "conservative_ev_r": float(selected["conservative_ev_r"]),
            "standard_error_r": float(selected["standard_error_r"]),
            "effective_sample_size": float(selected["effective_sample_size"]),
            "tp_before_sl_probability": float(selected["tp_before_sl_probability"]),
            "sl_before_tp_probability": float(selected["sl_before_tp_probability"]),
            "time_exit_probability": float(selected["time_exit_probability"]),
            "score": round(ev_score, 2),
            "confidence": round(confidence, 4),
            "grid": grid_results,
            "analogue_distance_median": round(statistics.median(distance for distance, _ in analogs), 4),
        }
    )
    return result


def effective_score_weight(config: dict[str, Any] | None, model: dict[str, Any]) -> float:
    settings = settings_from(config)
    if not settings["enabled"] or model.get("status") != "ready":
        return 0.0
    return round(settings["score_weight"] * float(model.get("confidence") or 0.0), 6)


def blend_score(base_score: float, model: dict[str, Any], config: dict[str, Any] | None) -> tuple[float, float]:
    weight = effective_score_weight(config, model)
    if weight <= 0.0:
        return core.round2(base_score), 0.0
    blended = core.round2(
        float(base_score) * (1.0 - weight)
        + float(model["score"]) * weight
    )
    return blended, weight


def apply_dynamic_target(candidate: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with the empirically selected R/R and target applied."""
    if model.get("status") != "ready":
        raise ValueError("expected-value model is not ready")
    value = copy.deepcopy(candidate)
    reference = float(value.get("reference_price") or 0.0)
    stop = float(value.get("stop") or 0.0)
    risk = reference - stop
    if reference <= 0.0 or risk <= 0.0:
        raise ValueError("candidate has invalid reference/stop geometry")
    rr = float(model["selected_reward_risk"])
    value["reward_risk"] = rr
    value["target"] = core.round2(reference + risk * rr)
    value["expected_value_model"] = copy.deepcopy(model)
    value["expected_value_score"] = float(model["score"])
    value["target_method"] = ENGINE
    value.setdefault("scores", {})["expected_value"] = float(model["score"])
    return value
