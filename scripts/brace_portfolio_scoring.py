#!/usr/bin/env python3
"""Transparent risk-aware scoring shared by holdings and candidates."""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any, Dict, Iterable, List, Mapping, Optional

from brace_portfolio_config import EngineConfig
from brace_portfolio_features import clamp, finite

SCORE_WEIGHTS = {
    "quality_score": 0.19,
    "valuation_score": 0.14,
    "momentum_score": 0.19,
    "risk_score": 0.22,
    "diversification_score": 0.11,
    "thesis_score": 0.09,
    "data_quality_score": 0.06,
}


def score_linear(
    value: Any,
    bad: float,
    good: float,
    neutral: float = 50.0,
) -> Optional[float]:
    number = finite(value)
    if number is None:
        return None
    if good == bad:
        return neutral
    ratio = (number - bad) / (good - bad)
    return clamp(ratio * 100.0, 0.0, 100.0)


def average(values: Iterable[Optional[float]], default: float = 50.0) -> float:
    available = [float(value) for value in values if value is not None]
    return sum(available) / len(available) if available else default


def _component_scores(
    features: Mapping[str, Any],
    instrument: Mapping[str, Any],
    config: EngineConfig,
) -> Dict[str, float]:
    quality = features.get("quality") or {}
    valuation = features.get("valuation") or {}
    momentum = features.get("momentum") or {}
    risk = features.get("risk") or {}
    asset_type = str(features.get("asset_type") or "STOCK")

    if "ETF" in asset_type:
        quality_score = 62.0
        valuation_score = 55.0
    else:
        quality_score = average(
            [
                score_linear(quality.get("revenue_growth"), -0.1, 0.2),
                score_linear(quality.get("earnings_growth"), -0.15, 0.25),
                score_linear(quality.get("profit_margin"), 0.0, 0.3),
                score_linear(quality.get("operating_margin"), 0.0, 0.3),
                score_linear(quality.get("free_cashflow_yield"), -0.02, 0.08),
                score_linear(quality.get("debt_to_equity"), 250.0, 20.0),
                score_linear(quality.get("roe"), 0.0, 0.3),
            ]
        )
        valuation_score = average(
            [
                score_linear(valuation.get("pe"), 55.0, 12.0),
                score_linear(valuation.get("forward_pe"), 45.0, 10.0),
                score_linear(valuation.get("price_to_sales"), 12.0, 1.2),
                score_linear(valuation.get("ev_to_ebitda"), 35.0, 7.0),
                score_linear(
                    valuation.get("free_cashflow_yield"),
                    -0.01,
                    0.08,
                ),
            ]
        )

    momentum_score = average(
        [
            score_linear(momentum.get("return_1m"), -0.12, 0.12),
            score_linear(momentum.get("return_3m"), -0.2, 0.25),
            score_linear(momentum.get("return_6m"), -0.3, 0.4),
            score_linear(momentum.get("return_12m"), -0.4, 0.55),
            score_linear(momentum.get("distance_ma50"), -0.15, 0.15),
            score_linear(momentum.get("distance_ma200"), -0.3, 0.3),
            score_linear(momentum.get("relative_strength_6m"), -0.2, 0.2),
            score_linear(momentum.get("relative_strength_12m"), -0.3, 0.3),
        ]
    )
    risk_score = average(
        [
            score_linear(risk.get("volatility"), 0.65, 0.12),
            score_linear(risk.get("downside_volatility"), 0.5, 0.08),
            score_linear(risk.get("maximum_drawdown"), -0.55, 0.0),
            score_linear(abs(finite(risk.get("beta")) or 1.0), 1.8, 0.6),
            score_linear(
                risk.get("portfolio_correlation"),
                0.95,
                0.15,
            ),
        ]
    )
    diversification_score = average(
        [
            score_linear(
                risk.get("sector_weight"),
                config.max_sector_weight,
                0.0,
            ),
            score_linear(
                risk.get("currency_weight"),
                config.max_currency_weight,
                0.0,
            ),
            score_linear(
                risk.get("region_weight"),
                config.max_region_weight,
                0.0,
            ),
            score_linear(
                risk.get("portfolio_correlation"),
                0.95,
                0.1,
            ),
        ]
    )
    thesis_score = 55.0
    if instrument.get("thesis_pl") or instrument.get("thesis_en"):
        thesis_score += 8.0
    if str(instrument.get("review_flag") or "").upper() == "THESIS_REVIEW":
        thesis_score = 25.0

    required_market = [
        (features.get("momentum") or {}).get("return_3m"),
        (features.get("momentum") or {}).get("return_6m"),
        (features.get("momentum") or {}).get("return_12m"),
        (features.get("risk") or {}).get("volatility"),
        (features.get("risk") or {}).get("maximum_drawdown"),
        (features.get("risk") or {}).get("beta"),
    ]
    market_completeness = sum(value is not None for value in required_market) / len(
        required_market
    )
    if "ETF" in asset_type:
        fundamental_completeness = 1.0
    else:
        fundamental_values = [
            *(features.get("quality") or {}).values(),
            *(features.get("valuation") or {}).values(),
        ]
        fundamental_completeness = sum(
            value is not None for value in fundamental_values
        ) / max(1, len(fundamental_values))
    freshness = (
        1.0
        if (features.get("price_age_days") or 0) <= 3
        else 0.5
        if (features.get("price_age_days") or 0) <= 7
        else 0.0
    )
    data_quality_score = 100.0 * (
        0.55 * market_completeness
        + 0.30 * fundamental_completeness
        + 0.15 * freshness
    )
    return {
        "quality_score": round(clamp(quality_score, 0, 100), 2),
        "valuation_score": round(clamp(valuation_score, 0, 100), 2),
        "momentum_score": round(clamp(momentum_score, 0, 100), 2),
        "risk_score": round(clamp(risk_score, 0, 100), 2),
        "diversification_score": round(
            clamp(diversification_score, 0, 100),
            2,
        ),
        "thesis_score": round(clamp(thesis_score, 0, 100), 2),
        "data_quality_score": round(clamp(data_quality_score, 0, 100), 2),
    }


def expected_return_scenarios(
    features: Mapping[str, Any],
    scores: Mapping[str, float],
    config: EngineConfig,
) -> Dict[str, Any]:
    momentum = features.get("momentum") or {}
    observations = [
        finite(momentum.get("return_3m")),
        finite(momentum.get("return_6m")),
        finite(momentum.get("return_12m")),
    ]
    scaled = [
        value * scale
        for value, scale in zip(observations, (2.0, 1.4, 1.0))
        if value is not None
    ]
    historical_anchor = sum(scaled) / len(scaled) if scaled else 0.0
    quality_adjustment = (scores["quality_score"] - 50.0) / 1000.0
    valuation_adjustment = (scores["valuation_score"] - 50.0) / 1200.0
    risk_penalty = max(0.0, (50.0 - scores["risk_score"]) / 700.0)
    base = clamp(
        historical_anchor * 0.55
        + quality_adjustment
        + valuation_adjustment
        - risk_penalty,
        -0.25,
        0.3,
    )
    volatility = finite((features.get("risk") or {}).get("volatility")) or 0.25
    bull = clamp(base + 0.65 * volatility, -0.1, 0.55)
    bear = clamp(base - 0.85 * volatility, -0.55, 0.15)
    confidence = clamp(scores["data_quality_score"] / 100.0, 0.0, 1.0)
    probability = 0.0
    if volatility > 0:
        probability = 1.0 - NormalDist(mu=base, sigma=volatility).cdf(
            config.target_annual_return
        )
    target_shortfall = max(0.0, config.target_annual_return - base)
    required_risk = target_shortfall / max(volatility, 0.01)
    return {
        "expected_return_base": round(base, 6),
        "expected_return_bull": round(bull, 6),
        "expected_return_bear": round(bear, 6),
        "confidence_score": round(confidence, 4),
        "expected_drawdown": round(
            min(
                config.max_expected_drawdown * 1.5,
                max(
                    abs(
                        finite(
                            (features.get("risk") or {}).get(
                                "maximum_drawdown"
                            )
                        )
                        or 0.0
                    ),
                    volatility * 0.75,
                ),
            ),
            6,
        ),
        "probability_of_reaching_target": round(probability, 6),
        "target_shortfall": round(target_shortfall, 6),
        "required_risk_to_target": round(required_risk, 6),
    }


def score_instrument(
    features: Mapping[str, Any],
    instrument: Mapping[str, Any],
    config: EngineConfig,
) -> Dict[str, Any]:
    components = _component_scores(features, instrument, config)
    weighted = sum(
        components[name] * weight for name, weight in SCORE_WEIGHTS.items()
    )
    data_multiplier = 0.55 + 0.45 * components["data_quality_score"] / 100.0
    risk_gate = min(1.0, 0.65 + components["risk_score"] / 285.0)
    uncertainty_penalty = max(
        0.0,
        (60.0 - components["data_quality_score"]) * 0.12,
    )
    final_score = clamp(
        weighted * data_multiplier * risk_gate - uncertainty_penalty,
        0.0,
        100.0,
    )
    scenarios = expected_return_scenarios(features, components, config)
    volatility = finite((features.get("risk") or {}).get("volatility")) or 0.35
    risk_adjusted = (
        scenarios["expected_return_base"] - config.risk_free_rate
    ) / max(volatility, 0.05)

    positives = [
        name
        for name, score in components.items()
        if name != "data_quality_score" and score >= 65
    ]
    negatives = [
        name
        for name, score in components.items()
        if score < 45
    ]
    if features.get("fundamental_data_status") == "DATA_UNAVAILABLE":
        negatives.append("fundamental_data_unavailable")
    return {
        **components,
        "final_score": round(final_score, 2),
        "risk_adjusted_score": round(risk_adjusted, 4),
        **scenarios,
        "positive_factors": positives,
        "negative_factors": sorted(set(negatives)),
        "data_status": features.get("fundamental_data_status"),
        "reason": (
            "Score combines quality, valuation, momentum, risk, diversification, "
            "thesis evidence, data quality and estimation uncertainty."
        ),
        "conditions_for_change": [
            "material thesis change",
            "risk-limit breach",
            "persistent score change",
            "new candidate advantage after costs",
        ],
    }
