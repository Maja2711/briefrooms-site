#!/usr/bin/env python3
"""Deterministic current-session confirmation model for GPW Daily Trading.

The model is intentionally small and bounded.  It does not predict a target and
is not a hard admission gate.  It answers one question: does the live session
confirm or weaken the completed-session setup that produced the base ranking?
"""
from __future__ import annotations

from typing import Any

try:
    from scripts import daily_stock_core as core
except ModuleNotFoundError:
    import daily_stock_core as core

ENGINE = "gpw-opening-confirmation-v1"
COMPONENT_WEIGHTS = {
    "open_return": 0.30,
    "intraday_position": 0.30,
    "distance_from_high": 0.20,
    "gap_quality": 0.20,
}
MAX_OVERLAY_WEIGHT = 0.50


def _gap_quality_score(gap: float) -> float:
    """Prefer controlled gaps and penalise chase-prone or distressed opens."""
    if -0.01 <= gap <= 0.02:
        return 100.0
    distance = (-0.01 - gap) if gap < -0.01 else (gap - 0.02)
    return core.clamp(100.0 - distance * 2500.0)


def score(candidate: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a 0..100 Opening Confirmation Score plus auditable components."""
    previous_close = float(candidate.get("reference_price") or 0.0)
    open_price = float(snapshot.get("open") or 0.0)
    high = float(snapshot.get("high") or 0.0)
    low = float(snapshot.get("low") or 0.0)
    last = float(snapshot.get("last") or 0.0)
    if min(previous_close, open_price, high, low, last) <= 0.0:
        raise ValueError("opening confirmation requires positive OHLC and previous close")
    if high < low:
        raise ValueError("opening confirmation received invalid intraday range")

    gap = open_price / previous_close - 1.0
    open_return = last / open_price - 1.0
    intraday_range = high - low
    intraday_position = (
        0.5
        if intraday_range <= max(last * 1e-8, 1e-8)
        else core.clamp((last - low) / intraday_range, 0.0, 1.0)
    )
    distance_from_high = last / high - 1.0

    # The continuation component peaks around +1.5% from the open and tapers
    # when the move is either failing or becoming chase-prone.
    open_return_score = core.clamp(
        100.0 - abs(open_return - 0.015) * 2000.0
    )
    intraday_position_score = core.clamp(intraday_position * 100.0)
    distance_from_high_score = core.clamp(100.0 + distance_from_high * 2500.0)
    gap_quality_score = _gap_quality_score(gap)

    components = {
        "open_return": round(open_return_score, 2),
        "intraday_position": round(intraday_position_score, 2),
        "distance_from_high": round(distance_from_high_score, 2),
        "gap_quality": round(gap_quality_score, 2),
    }
    total = sum(
        components[name] * weight
        for name, weight in COMPONENT_WEIGHTS.items()
    )
    return {
        "engine": ENGINE,
        "score": round(core.clamp(total), 2),
        "gap": round(gap, 5),
        "open_return": round(open_return, 5),
        "intraday_position": round(intraday_position, 5),
        "distance_from_high": round(distance_from_high, 5),
        "components": components,
        "component_weights": dict(COMPONENT_WEIGHTS),
        "observed_at": snapshot.get("observed_at"),
    }


def bounded_weight(value: Any, default: float = 0.25) -> float:
    """Interpret the configured overlay weight as a decimal fraction."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return core.clamp(parsed, 0.0, MAX_OVERLAY_WEIGHT)


def blend(base_score: float, opening_score: float, weight: float) -> float:
    """Blend 0..100 base and opening scores without allowing overlay dominance."""
    bounded = bounded_weight(weight)
    return core.round2(
        float(base_score) * (1.0 - bounded)
        + float(opening_score) * bounded
    )
