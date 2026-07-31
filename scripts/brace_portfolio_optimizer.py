#!/usr/bin/env python3
"""Constrained, turnover-aware portfolio weight proposals."""
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
    ordered = sorted(
        (
            (key, max(0.0, float(value)))
            for key, value in raw_weights.items()
            if key in instruments and value > 0
        ),
        key=lambda item: (-item[1], item[0]),
    )[: config.max_positions]
    weights = {key: value for key, value in ordered}
    if not weights:
        return {"CASH": 1.0}
    weights = _normalize(weights)

    for _ in range(12):
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
                groups.setdefault(str(instruments[key].get(field) or "Unknown"), []).append(
                    key
                )
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
        if invested > 1.0:
            weights = {key: value / invested for key, value in weights.items()}
            changed = True
        if not changed:
            break

    cash = max(0.0, 1.0 - sum(weights.values()))
    if cash > 1e-9:
        weights["CASH"] = cash
    correction = 1.0 - sum(weights.values())
    weights["CASH"] = weights.get("CASH", 0.0) + correction
    return {key: round(value, 8) for key, value in sorted(weights.items())}


def turnover(
    current: Mapping[str, float],
    proposed: Mapping[str, float],
) -> float:
    keys = set(current) | set(proposed)
    return 0.5 * sum(
        abs(float(proposed.get(key, 0.0)) - float(current.get(key, 0.0)))
        for key in keys
    )


def portfolio_metrics(
    weights: Mapping[str, float],
    analyses: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, float],
) -> Dict[str, float]:
    expected = 0.0
    variance = 0.0
    drawdown = 0.0
    score = 0.0
    for key, weight in weights.items():
        if key == "CASH":
            continue
        item = analyses[key]
        expected += weight * float(item.get("expected_return_base") or 0.0)
        volatility = finite((item.get("risk") or {}).get("volatility")) or 0.3
        variance += (weight * volatility) ** 2
        drawdown += weight * float(item.get("expected_drawdown") or 0.0)
        score += weight * float(item.get("final_score") or 0.0)
    return {
        "expected_return": round(expected, 6),
        "volatility_proxy": round(math.sqrt(variance), 6),
        "expected_drawdown": round(drawdown, 6),
        "weighted_score": round(score, 4),
        "turnover": round(turnover(current, weights), 6),
    }


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
    for key, item in by_id.items():
        volatility = finite((item.get("risk") or {}).get("volatility")) or 0.5
        expected = float(item.get("expected_return_base") or 0.0)
        score = float(item.get("final_score") or 0.0) / 100.0
        inverse_variance[key] = 1.0 / max(volatility * volatility, 0.01)
        sharpe[key] = max(0.0, expected - config.risk_free_rate) / max(
            volatility,
            0.05,
        )
        objective[key] = max(
            0.0,
            score
            * max(0.0, expected + 0.05)
            / max(volatility, 0.05)
            + 0.35 * float(current.get(key, 0.0)),
        )
    candidates["minimum_variance"] = inverse_variance
    candidates["maximum_sharpe"] = sharpe
    candidates["return_risk_turnover"] = objective

    evaluated = []
    for name, raw in candidates.items():
        weights = (
            enforce_limits(raw, instruments, config)
            if name != "current"
            else enforce_limits(current, instruments, config)
        )
        metrics = portfolio_metrics(weights, by_id, current)
        metrics["objective_value"] = round(
            metrics["expected_return"]
            - 0.55 * metrics["volatility_proxy"]
            - 0.35 * metrics["turnover"],
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
    return {
        "selected": selected["name"],
        "current_weights": dict(sorted(current.items())),
        "target_weights": selected["weights"],
        "comparisons": evaluated,
        "rules_passed": selected in eligible,
        "no_leverage": abs(sum(selected["weights"].values()) - 1.0) < 1e-6,
        "no_short_positions": all(
            value >= 0 for value in selected["weights"].values()
        ),
    }
