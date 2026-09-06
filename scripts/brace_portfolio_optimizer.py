#!/usr/bin/env python3
"""Autonomous, constrained and turnover-aware BRACE portfolio construction."""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping

from brace_portfolio_config import EngineConfig
from brace_portfolio_features import finite


def _cap(item: Mapping[str, Any], config: EngineConfig) -> float:
    return (
        config.max_broad_etf_weight
        if str(item.get("asset_type") or "").upper() == "BROAD_ETF"
        else config.max_single_stock_weight
        if str(item.get("asset_type") or "").upper() == "STOCK"
        else config.max_broad_etf_weight
    )


def _confidence(item: Mapping[str, Any]) -> float:
    raw = finite(item.get("confidence_score"))
    if raw is None:
        raw = finite(item.get("confidence"))
    if raw is None:
        raw = finite(item.get("probability_of_target"))
    value = 0.5 if raw is None else float(raw)
    if value > 1.0 and value <= 100.0:
        value /= 100.0
    return min(1.0, max(0.0, value))


def _normalize(weights: MutableMapping[str, float]) -> Dict[str, float]:
    total = sum(max(0.0, value) for value in weights.values())
    if total <= 0:
        return {"CASH": 1.0}
    return {key: max(0.0, value) / total for key, value in weights.items()}


def enforce_limits(
    raw_weights: Mapping[str, float],
    instruments: Mapping[str, Mapping[str, Any]],
    config: EngineConfig,
) -> Dict[str, float]:
    active_limit = min(
        config.max_positions,
        max(2, int(round(config.portfolio_max_active_positions))),
    )
    ordered = sorted(
        (
            (key, max(0.0, float(value)))
            for key, value in raw_weights.items()
            if key in instruments and value > 0
        ),
        key=lambda item: (-item[1], item[0]),
    )[:active_limit]
    weights = {key: value for key, value in ordered}
    if not weights:
        return {"CASH": 1.0}
    weights = _normalize(weights)

    for _ in range(16):
        changed = False
        for key, value in list(weights.items()):
            cap = _cap(instruments[key], config)
            if value > cap:
                weights[key] = cap
                changed = True
        for field, limit in (
            ("sector", config.max_sector_weight),
            ("currency", config.max_currency_weight),
            ("region", config.max_region_weight),
        ):
            groups: Dict[str, List[str]] = {}
            for key in weights:
                groups.setdefault(str(instruments[key].get(field) or "Unknown"), []).append(key)
            for keys in groups.values():
                total = sum(weights[key] for key in keys)
                if total > limit:
                    scale = limit / total
                    for key in keys:
                        weights[key] *= scale
                    changed = True
        active = {
            key: value
            for key, value in weights.items()
            if value >= config.minimum_position_weight
        }
        if len(active) != len(weights):
            weights = active
            changed = True
        invested = sum(weights.values())
        maximum_invested = 1.0 - config.portfolio_cash_floor
        if invested > maximum_invested and invested > 0:
            scale = maximum_invested / invested
            weights = {key: value * scale for key, value in weights.items()}
            changed = True
        if not changed:
            break

    cash = max(config.portfolio_cash_floor, 1.0 - sum(weights.values()))
    if cash > 1e-9:
        weights["CASH"] = cash
    total = sum(weights.values())
    if total > 1.0 + 1e-9:
        non_cash = sum(value for key, value in weights.items() if key != "CASH")
        target_non_cash = max(0.0, 1.0 - weights.get("CASH", 0.0))
        if non_cash > 0:
            scale = target_non_cash / non_cash
            for key in list(weights):
                if key != "CASH":
                    weights[key] *= scale
    correction = 1.0 - sum(weights.values())
    weights["CASH"] = weights.get("CASH", 0.0) + correction
    return {key: round(max(0.0, value), 8) for key, value in sorted(weights.items())}


def turnover(current: Mapping[str, float], proposed: Mapping[str, float]) -> float:
    keys = set(current) | set(proposed)
    return 0.5 * sum(
        abs(float(proposed.get(key, 0.0)) - float(current.get(key, 0.0)))
        for key in keys
    )


def _concentration_proxy(
    weights: Mapping[str, float], analyses: Mapping[str, Mapping[str, Any]]
) -> float:
    stock_hhi = sum(float(weight) ** 2 for key, weight in weights.items() if key != "CASH")
    sectors: Dict[str, float] = {}
    for key, weight in weights.items():
        if key == "CASH" or key not in analyses:
            continue
        sector = str(analyses[key].get("sector") or "Unknown")
        sectors[sector] = sectors.get(sector, 0.0) + float(weight)
    sector_hhi = sum(value**2 for value in sectors.values())
    return 0.5 * stock_hhi + 0.5 * sector_hhi


def portfolio_metrics(
    weights: Mapping[str, float],
    analyses: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, float],
    config: EngineConfig | None = None,
) -> Dict[str, float]:
    expected = 0.0
    confidence_adjusted = 0.0
    variance = 0.0
    drawdown = 0.0
    score = 0.0
    for key, weight in weights.items():
        if key == "CASH":
            continue
        item = analyses[key]
        item_expected = float(item.get("expected_return_base") or 0.0)
        expected += weight * item_expected
        confidence_adjusted += weight * item_expected * _confidence(item)
        volatility = finite((item.get("risk") or {}).get("volatility")) or 0.3
        variance += (weight * volatility) ** 2
        drawdown += weight * float(item.get("expected_drawdown") or 0.0)
        score += weight * float(item.get("final_score") or 0.0)
    portfolio_turnover = turnover(current, weights)
    cost_rate = float(config.transaction_cost_buffer) if config else 0.0
    net_expected = confidence_adjusted - portfolio_turnover * cost_rate
    return {
        "expected_return": round(expected, 6),
        "confidence_adjusted_expected_return": round(confidence_adjusted, 6),
        "expected_return_after_costs": round(net_expected, 6),
        "volatility_proxy": round(math.sqrt(variance), 6),
        "expected_drawdown": round(drawdown, 6),
        "weighted_score": round(score, 4),
        "turnover": round(portfolio_turnover, 6),
        "estimated_transaction_cost": round(portfolio_turnover * cost_rate, 6),
        "concentration_proxy": round(_concentration_proxy(weights, analyses), 6),
        "cash_weight": round(float(weights.get("CASH", 0.0)), 6),
        "active_positions": float(sum(1 for key, value in weights.items() if key != "CASH" and value > 0)),
    }


def _suppress_small_rebalances(
    proposed: Mapping[str, float],
    current: Mapping[str, float],
    instruments: Mapping[str, Mapping[str, Any]],
    config: EngineConfig,
) -> Dict[str, float]:
    threshold = config.portfolio_rebalance_threshold
    adjusted = dict(proposed)
    for key in set(current) | set(proposed):
        if key == "CASH":
            continue
        current_weight = float(current.get(key, 0.0))
        target_weight = float(proposed.get(key, 0.0))
        if abs(target_weight - current_weight) < threshold:
            if current_weight > 0:
                adjusted[key] = current_weight
            else:
                adjusted.pop(key, None)
    return enforce_limits(adjusted, instruments, config)


def build_rebalance_plan(
    current: Mapping[str, float],
    target: Mapping[str, float],
    config: EngineConfig,
) -> List[Dict[str, Any]]:
    threshold = config.portfolio_rebalance_threshold
    rows: List[Dict[str, Any]] = []
    for key in sorted((set(current) | set(target)) - {"CASH"}):
        current_weight = float(current.get(key, 0.0))
        target_weight = float(target.get(key, 0.0))
        delta = target_weight - current_weight
        if current_weight <= 1e-9 and target_weight > 1e-9:
            action = "OPEN"
        elif target_weight <= 1e-9 and current_weight > 1e-9:
            action = "EXIT"
        elif delta >= threshold:
            action = "ADD"
        elif delta <= -threshold:
            action = "TRIM"
        else:
            action = "KEEP"
        rows.append(
            {
                "instrument_id": key,
                "action": action,
                "current_weight": round(current_weight, 8),
                "target_weight": round(target_weight, 8),
                "delta_weight": round(delta, 8),
            }
        )
    rows.append(
        {
            "instrument_id": "CASH",
            "action": "CASH",
            "current_weight": round(float(current.get("CASH", 0.0)), 8),
            "target_weight": round(float(target.get("CASH", 0.0)), 8),
            "delta_weight": round(
                float(target.get("CASH", 0.0)) - float(current.get("CASH", 0.0)),
                8,
            ),
        }
    )
    return rows


def optimize(
    current_weights: Mapping[str, float],
    analyses: Iterable[Mapping[str, Any]],
    config: EngineConfig,
) -> Dict[str, Any]:
    by_id = {
        str(item.get("instrument_id")): dict(item)
        for item in analyses
        if item.get("instrument_id")
    }
    instruments = by_id
    current = {
        key: float(value)
        for key, value in current_weights.items()
        if key in instruments or key == "CASH"
    }
    candidates: Dict[str, Mapping[str, float]] = {"current": current}

    inverse_variance: Dict[str, float] = {}
    sharpe: Dict[str, float] = {}
    objective: Dict[str, float] = {}
    conviction: Dict[str, float] = {}
    for key, item in by_id.items():
        volatility = finite((item.get("risk") or {}).get("volatility")) or 0.5
        expected = float(item.get("expected_return_base") or 0.0)
        score = float(item.get("final_score") or 0.0) / 100.0
        confidence = _confidence(item)
        inverse_variance[key] = 1.0 / max(volatility * volatility, 0.01)
        sharpe[key] = max(0.0, expected - config.risk_free_rate) * confidence / max(volatility, 0.05)
        objective[key] = max(
            0.0,
            score
            * confidence
            * max(0.0, expected + 0.05)
            / max(volatility, 0.05)
            + 0.35 * float(current.get(key, 0.0)),
        )
        conviction[key] = max(
            0.0,
            score * confidence * max(0.0, expected) / max(volatility, 0.05),
        )
    candidates["minimum_variance"] = inverse_variance
    candidates["maximum_sharpe"] = sharpe
    candidates["return_risk_turnover"] = objective
    candidates["conviction_growth"] = conviction

    evaluated: List[Dict[str, Any]] = []
    for name, raw in candidates.items():
        weights = enforce_limits(raw, instruments, config)
        weights = _suppress_small_rebalances(weights, current, instruments, config)
        metrics = portfolio_metrics(weights, by_id, current, config)
        metrics["objective_value"] = round(
            metrics["expected_return_after_costs"]
            - config.portfolio_risk_aversion * metrics["volatility_proxy"]
            - config.portfolio_drawdown_penalty * metrics["expected_drawdown"]
            - config.portfolio_turnover_penalty * metrics["turnover"]
            - config.portfolio_diversification_penalty * metrics["concentration_proxy"],
            6,
        )
        evaluated.append({"name": name, "weights": weights, "metrics": metrics})

    eligible = [
        item
        for item in evaluated
        if item["metrics"]["expected_drawdown"] <= config.max_expected_drawdown
    ]
    selected = max(
        eligible or evaluated,
        key=lambda item: (
            item["metrics"]["objective_value"],
            -item["metrics"]["turnover"],
            item["name"],
        ),
    )
    target_weights = selected["weights"]
    return {
        "selected": selected["name"],
        "current_weights": dict(sorted(current.items())),
        "target_weights": target_weights,
        "rebalance_plan": build_rebalance_plan(current, target_weights, config),
        "comparisons": evaluated,
        "rules_passed": selected in eligible,
        "no_leverage": abs(sum(target_weights.values()) - 1.0) < 1e-6,
        "no_short_positions": all(value >= 0 for value in target_weights.values()),
        "portfolio_policy_snapshot": {
            "portfolio_risk_aversion": config.portfolio_risk_aversion,
            "portfolio_drawdown_penalty": config.portfolio_drawdown_penalty,
            "portfolio_turnover_penalty": config.portfolio_turnover_penalty,
            "portfolio_diversification_penalty": config.portfolio_diversification_penalty,
            "portfolio_cash_floor": config.portfolio_cash_floor,
            "portfolio_rebalance_threshold": config.portfolio_rebalance_threshold,
            "portfolio_max_active_positions": config.portfolio_max_active_positions,
        },
        "safety_kernel_snapshot": {
            "max_single_stock_weight": config.max_single_stock_weight,
            "max_broad_etf_weight": config.max_broad_etf_weight,
            "max_sector_weight": config.max_sector_weight,
            "max_currency_weight": config.max_currency_weight,
            "max_region_weight": config.max_region_weight,
            "max_positions": config.max_positions,
            "max_expected_drawdown": config.max_expected_drawdown,
            "max_annual_turnover": config.max_annual_turnover,
            "real_broker_integration_enabled": False,
        },
    }
