#!/usr/bin/env python3
"""Point-in-time feature engineering for positions and candidates."""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

TRADING_DAYS = 252


def finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalized_history(history: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    by_date: Dict[str, float] = {}
    for row in history:
        value = finite(row.get("close"))
        when = str(row.get("date") or "")[:10]
        if value is None or value <= 0 or not when:
            continue
        try:
            date.fromisoformat(when)
        except ValueError:
            continue
        by_date[when] = value
    return [{"date": key, "close": by_date[key]} for key in sorted(by_date)]


def simple_returns(values: Sequence[float]) -> List[float]:
    return [
        values[index] / values[index - 1] - 1.0
        for index in range(1, len(values))
        if values[index - 1] > 0
    ]


def return_over(values: Sequence[float], sessions: int) -> Optional[float]:
    if len(values) <= sessions or values[-sessions - 1] <= 0:
        return None
    return values[-1] / values[-sessions - 1] - 1.0


def moving_average_distance(
    values: Sequence[float],
    sessions: int,
) -> Optional[float]:
    if len(values) < sessions:
        return None
    average = statistics.fmean(values[-sessions:])
    return values[-1] / average - 1.0 if average > 0 else None


def annualized_volatility(returns: Sequence[float]) -> Optional[float]:
    if len(returns) < 20:
        return None
    return statistics.stdev(returns) * math.sqrt(TRADING_DAYS)


def downside_volatility(returns: Sequence[float]) -> Optional[float]:
    downside = [min(0.0, value) for value in returns]
    if len(downside) < 20:
        return None
    return math.sqrt(statistics.fmean(value * value for value in downside)) * math.sqrt(
        TRADING_DAYS
    )


def maximum_drawdown(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    return worst


def _aligned_returns(
    history: Sequence[Mapping[str, Any]],
    benchmark: Sequence[Mapping[str, Any]],
) -> tuple[List[float], List[float]]:
    asset_prices = {str(row["date"]): float(row["close"]) for row in history}
    benchmark_prices = {str(row["date"]): float(row["close"]) for row in benchmark}
    dates = sorted(set(asset_prices) & set(benchmark_prices))
    asset = [asset_prices[when] for when in dates]
    bench = [benchmark_prices[when] for when in dates]
    return simple_returns(asset), simple_returns(bench)


def beta_and_correlation(
    history: Sequence[Mapping[str, Any]],
    benchmark: Sequence[Mapping[str, Any]],
) -> tuple[Optional[float], Optional[float]]:
    asset, bench = _aligned_returns(history, benchmark)
    length = min(len(asset), len(bench))
    if length < 30:
        return None, None
    asset, bench = asset[-length:], bench[-length:]
    benchmark_variance = statistics.variance(bench)
    if benchmark_variance <= 0:
        return None, None
    covariance = sum(
        (left - statistics.fmean(asset)) * (right - statistics.fmean(bench))
        for left, right in zip(asset, bench)
    ) / (length - 1)
    beta = covariance / benchmark_variance
    denominator = statistics.stdev(asset) * statistics.stdev(bench)
    correlation = covariance / denominator if denominator > 0 else None
    return beta, correlation


def correlation_with_portfolio(
    history: Sequence[Mapping[str, Any]],
    portfolio_histories: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Optional[float]:
    correlations = []
    for other in portfolio_histories.values():
        _, correlation = beta_and_correlation(history, other)
        if correlation is not None:
            correlations.append(correlation)
    return statistics.fmean(correlations) if correlations else None


def _fundamental_value(
    fundamentals: Mapping[str, Any],
    key: str,
) -> Optional[float]:
    value = finite(fundamentals.get(key))
    return value


def build_features(
    instrument: Mapping[str, Any],
    history: Iterable[Mapping[str, Any]],
    benchmark_history: Iterable[Mapping[str, Any]],
    fundamentals: Optional[Mapping[str, Any]] = None,
    portfolio_histories: Optional[
        Mapping[str, Sequence[Mapping[str, Any]]]
    ] = None,
    portfolio_group_weights: Optional[Mapping[str, Mapping[str, float]]] = None,
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    fundamentals = fundamentals or {}
    as_of = as_of or datetime.utcnow().date()
    normalized = normalized_history(history)
    benchmark = normalized_history(benchmark_history)
    closes = [float(row["close"]) for row in normalized]
    benchmark_closes = [float(row["close"]) for row in benchmark]
    returns = simple_returns(closes)
    beta, benchmark_correlation = beta_and_correlation(normalized, benchmark)
    portfolio_correlation = correlation_with_portfolio(
        normalized,
        portfolio_histories or {},
    )
    asset_type = str(instrument.get("asset_type") or "STOCK").upper()
    latest_date = normalized[-1]["date"] if normalized else None
    price_age_days = None
    if latest_date:
        price_age_days = (as_of - date.fromisoformat(latest_date)).days

    momentum = {
        "return_1m": return_over(closes, 21),
        "return_3m": return_over(closes, 63),
        "return_6m": return_over(closes, 126),
        "return_12m": return_over(closes, 252),
        "distance_ma50": moving_average_distance(closes, 50),
        "distance_ma200": moving_average_distance(closes, 200),
        "relative_strength_6m": None,
        "relative_strength_12m": None,
    }
    benchmark_6m = return_over(benchmark_closes, 126)
    benchmark_12m = return_over(benchmark_closes, 252)
    if momentum["return_6m"] is not None and benchmark_6m is not None:
        momentum["relative_strength_6m"] = momentum["return_6m"] - benchmark_6m
    if momentum["return_12m"] is not None and benchmark_12m is not None:
        momentum["relative_strength_12m"] = momentum["return_12m"] - benchmark_12m

    market_cap = _fundamental_value(fundamentals, "marketCap")
    free_cashflow = _fundamental_value(fundamentals, "freeCashflow")
    fundamental_values = {
        "revenue_growth": _fundamental_value(fundamentals, "revenueGrowth"),
        "earnings_growth": _fundamental_value(fundamentals, "earningsGrowth"),
        "profit_margin": _fundamental_value(fundamentals, "profitMargins"),
        "operating_margin": _fundamental_value(fundamentals, "operatingMargins"),
        "debt_to_equity": _fundamental_value(fundamentals, "debtToEquity"),
        "roe": _fundamental_value(fundamentals, "returnOnEquity"),
        "roa": _fundamental_value(fundamentals, "returnOnAssets"),
        "pe": _fundamental_value(fundamentals, "trailingPE"),
        "forward_pe": _fundamental_value(fundamentals, "forwardPE"),
        "price_to_sales": _fundamental_value(
            fundamentals,
            "priceToSalesTrailing12Months",
        ),
        "ev_to_ebitda": _fundamental_value(fundamentals, "enterpriseToEbitda"),
        "free_cashflow_yield": (
            free_cashflow / market_cap
            if free_cashflow is not None and market_cap and market_cap > 0
            else None
        ),
        "average_volume": _fundamental_value(fundamentals, "averageVolume"),
    }
    fundamental_available = sum(
        value is not None for value in fundamental_values.values()
    )
    fundamental_required = 0 if "ETF" in asset_type else 6
    fundamental_status = (
        "NOT_APPLICABLE"
        if "ETF" in asset_type
        else "AVAILABLE"
        if fundamental_available >= fundamental_required
        else "DATA_UNAVAILABLE"
    )

    group_weights = portfolio_group_weights or {}
    sector = str(instrument.get("sector") or "Unknown")
    currency = str(instrument.get("currency") or "Unknown")
    region = str(instrument.get("region") or "Unknown")
    result = {
        "instrument_id": instrument.get("instrument_id") or instrument.get("id"),
        "broker_symbol": instrument.get("broker_symbol"),
        "data_symbol": instrument.get("data_symbol")
        or instrument.get("market_symbol"),
        "asset_type": asset_type,
        "sector": sector,
        "currency": currency,
        "region": region,
        "latest_price_date": latest_date,
        "price_age_days": price_age_days,
        "observations": len(closes),
        "current_price": closes[-1] if closes else None,
        "momentum": momentum,
        "risk": {
            "volatility": annualized_volatility(returns[-252:]),
            "downside_volatility": downside_volatility(returns[-252:]),
            "maximum_drawdown": maximum_drawdown(closes[-504:]),
            "beta": beta,
            "benchmark_correlation": benchmark_correlation,
            "portfolio_correlation": portfolio_correlation,
            "sector_weight": finite(
                (group_weights.get("sector") or {}).get(sector)
            )
            or 0.0,
            "currency_weight": finite(
                (group_weights.get("currency") or {}).get(currency)
            )
            or 0.0,
            "region_weight": finite(
                (group_weights.get("region") or {}).get(region)
            )
            or 0.0,
        },
        "quality": {
            key: fundamental_values[key]
            for key in (
                "revenue_growth",
                "earnings_growth",
                "profit_margin",
                "operating_margin",
                "debt_to_equity",
                "roe",
                "roa",
                "free_cashflow_yield",
            )
        },
        "valuation": {
            key: fundamental_values[key]
            for key in (
                "pe",
                "forward_pe",
                "price_to_sales",
                "ev_to_ebitda",
                "free_cashflow_yield",
            )
        },
        "liquidity": {
            "average_volume": fundamental_values["average_volume"],
        },
        "fundamental_data_status": fundamental_status,
    }
    return result
