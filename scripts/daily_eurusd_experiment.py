#!/usr/bin/env python3
"""Prospective A/B/C research shadow for Daily EUR/USD.

A = multi-timeframe technical-only timing arm.
B = canonical frozen Belief Core arm.
C = overlap-controlled hybrid: technical timing + orthogonal Belief context.

The module never writes to Belief Core and never influences the active Daily
EUR/USD decision path. Captures are prospective and outcomes resolve forward-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar, YahooChartClient

SCHEMA_VERSION = "eurusd-daily-abc-experiment-v1"
REPORT_SCHEMA_VERSION = "eurusd-daily-abc-report-v1"
ENGINE_VERSION = "eurusd-daily-abc-v1.1.0"
MODE = "research_shadow"
EURUSD = "EURUSD=X"

HORIZONS_MINUTES = (30, 60, 120, 240, 1440)
LONG_THRESHOLD = 60.0
SHORT_THRESHOLD = 40.0
MA_WINDOWS = (30, 60, 100, 200)
TIMEFRAME_FAST_WEIGHT = 0.65
TIMEFRAME_SLOW_WEIGHT = 0.35

TECHNICAL_WEIGHTS = {
    "multi_timeframe_ma": 0.24,
    "pivot_structure": 0.16,
    "multi_timeframe_macd": 0.18,
    "multi_timeframe_bollinger": 0.14,
    "rsi_momentum": 0.08,
    "trendline": 0.09,
    "price_momentum": 0.11,
}

BELIEF_WEIGHTS = {
    "eurusd.trend.bullish": 0.55,
    "eurusd.usd_environment.supportive": 0.25,
    "eurusd.us_rates_pressure.supportive": 0.20,
}

# Direct EUR/USD trend Belief overlaps with Arm A's price-derived trend family,
# so C excludes it from context instead of double-counting the same information.
HYBRID_CONTEXT_BELIEF_IDS = (
    "eurusd.usd_environment.supportive",
    "eurusd.us_rates_pressure.supportive",
)
HYBRID_TECHNICAL_WEIGHT = 0.70
HYBRID_BELIEF_CONTEXT_WEIGHT = 0.30


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _canonical_sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _return(rows: Sequence[Bar], bars: int) -> float | None:
    if len(rows) <= bars:
        return None
    start = float(rows[-1 - bars].close)
    if start == 0:
        return None
    return float(rows[-1].close) / start - 1.0


def _sma(rows: Sequence[Bar], window: int) -> float | None:
    if len(rows) < window:
        return None
    values = [float(bar.close) for bar in rows[-window:]]
    return sum(values) / len(values)


def _ema_from_values(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    alpha = 2.0 / (window + 1.0)
    value = float(values[-window])
    for item in values[-window + 1 :]:
        value = alpha * float(item) + (1.0 - alpha) * value
    return value


def _ema(rows: Sequence[Bar], window: int) -> float | None:
    return _ema_from_values([float(bar.close) for bar in rows], window)


def _ema_series(values: Sequence[float], window: int) -> list[float]:
    if len(values) < window:
        return []
    alpha = 2.0 / (window + 1.0)
    current = sum(float(x) for x in values[:window]) / window
    output = [current]
    for item in values[window:]:
        current = alpha * float(item) + (1.0 - alpha) * current
        output.append(current)
    return output


def _atr(rows: Sequence[Bar], window: int = 14) -> float | None:
    chunk = list(rows[-window - 1 :])
    if len(chunk) < 3:
        return None
    values: list[float] = []
    for previous, current in zip(chunk[:-1], chunk[1:]):
        high = float(current.high if current.high is not None else current.close)
        low = float(current.low if current.low is not None else current.close)
        prev_close = float(previous.close)
        values.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(values) / len(values) if values else None


def _rsi(rows: Sequence[Bar], window: int = 14) -> float | None:
    if len(rows) < window + 1:
        return None
    closes = [float(bar.close) for bar in rows]
    changes = [b - a for a, b in zip(closes[:-1], closes[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window
    for gain, loss in zip(gains[window:], losses[window:]):
        avg_gain = ((window - 1) * avg_gain + gain) / window
        avg_loss = ((window - 1) * avg_loss + loss) / window
    if avg_loss <= 1e-15:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _trendline(rows: Sequence[Bar], window: int = 24) -> tuple[float, float, float] | None:
    if len(rows) < window:
        return None
    ys = [float(bar.close) for bar in rows[-window:]]
    n = len(ys)
    x_mean = (n - 1) / 2.0
    y_mean = sum(ys) / n
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    if denominator <= 0:
        return None
    slope = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(ys)) / denominator
    intercept = y_mean - slope * x_mean
    fitted = [intercept + slope * i for i in range(n)]
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - yhat) ** 2 for y, yhat in zip(ys, fitted))
    r2 = 0.0 if ss_tot <= 1e-18 else _clamp(1.0 - ss_res / ss_tot, 0.0, 1.0)
    return slope, r2, fitted[-1]


def _ma_structure(rows: Sequence[Bar]) -> dict[str, Any]:
    if len(rows) < max(MA_WINDOWS):
        raise ValueError("MA structure requires at least 200 bars")
    atr = float(_atr(rows, 14) or 0.0)
    if atr <= 0:
        raise ValueError("MA structure requires positive ATR")
    close = float(rows[-1].close)
    values = {window: float(_sma(rows, window)) for window in MA_WINDOWS}
    pair_diffs = [
        (values[30] - values[60]) / atr,
        (values[60] - values[100]) / atr,
        (values[100] - values[200]) / atr,
    ]
    hierarchy = sum(_clamp(diff / 0.75) for diff in pair_diffs) / len(pair_diffs)
    price_position = sum(_clamp((close - values[window]) / (2.0 * atr)) for window in MA_WINDOWS) / len(MA_WINDOWS)
    score = _clamp(0.75 * hierarchy + 0.25 * price_position)
    bullish_order = values[30] > values[60] > values[100] > values[200]
    bearish_order = values[30] < values[60] < values[100] < values[200]
    return {
        "score": round(score, 6),
        "values": {f"ma{window}": round(values[window], 5) for window in MA_WINDOWS},
        "bullish_order": bullish_order,
        "bearish_order": bearish_order,
        "close_above_all": all(close > value for value in values.values()),
        "close_below_all": all(close < value for value in values.values()),
    }


def _macd(rows: Sequence[Bar]) -> dict[str, Any]:
    closes = [float(bar.close) for bar in rows]
    if len(closes) < 40:
        raise ValueError("MACD requires at least 40 bars")
    fast = _ema_series(closes, 12)
    slow = _ema_series(closes, 26)
    offset = len(fast) - len(slow)
    aligned_fast = fast[offset:]
    macd_series = [a - b for a, b in zip(aligned_fast, slow)]
    if len(macd_series) < 9:
        raise ValueError("MACD signal requires at least 9 MACD observations")
    signal = float(_ema_from_values(macd_series, 9))
    line = float(macd_series[-1])
    histogram = line - signal
    atr = float(_atr(rows, 14) or 0.0)
    if atr <= 0:
        raise ValueError("MACD requires positive ATR")
    score = _clamp(
        0.60 * _clamp(histogram / (0.15 * atr))
        + 0.40 * _clamp(line / (0.60 * atr))
    )
    return {
        "score": round(score, 6),
        "line": round(line, 8),
        "signal": round(signal, 8),
        "histogram": round(histogram, 8),
        "bullish_cross_state": line > signal,
        "bearish_cross_state": line < signal,
        "parameters": {"fast": 12, "slow": 26, "signal": 9},
    }


def _bollinger(rows: Sequence[Bar], window: int = 20, stddevs: float = 2.0) -> dict[str, Any]:
    if len(rows) < window + 1:
        raise ValueError("Bollinger Bands require at least 21 bars")
    closes = [float(bar.close) for bar in rows]
    recent = closes[-window:]
    middle = sum(recent) / window
    sigma = statistics.pstdev(recent)
    upper = middle + stddevs * sigma
    lower = middle - stddevs * sigma
    previous_middle = sum(closes[-window - 1 : -1]) / window
    close = closes[-1]
    atr = float(_atr(rows, 14) or 0.0)
    half_width = upper - middle
    location = 0.0 if half_width <= 1e-15 else _clamp((close - middle) / half_width)
    middle_slope = 0.0 if atr <= 0 else _clamp((middle - previous_middle) / (0.20 * atr))
    score = _clamp(0.75 * location + 0.25 * middle_slope)
    bandwidth = 0.0 if abs(middle) <= 1e-15 else (upper - lower) / abs(middle)
    return {
        "score": round(score, 6),
        "middle": round(middle, 5),
        "upper": round(upper, 5),
        "lower": round(lower, 5),
        "bandwidth": round(bandwidth, 8),
        "percent_b": None if upper - lower <= 1e-15 else round((close - lower) / (upper - lower), 6),
        "above_upper": close > upper,
        "below_lower": close < lower,
        "parameters": {"window": window, "stddevs": stddevs},
    }


def _previous_daily_bar(rows: Sequence[Bar], observed_at: datetime) -> Bar:
    eligible = [bar for bar in rows if bar.timestamp.astimezone(timezone.utc).date() < observed_at.date()]
    if eligible:
        return eligible[-1]
    if len(rows) >= 2:
        return rows[-2]
    raise ValueError("Pivot requires a prior completed daily bar")


def _classic_pivots(rows: Sequence[Bar], observed_at: datetime, reference_price: float) -> dict[str, Any]:
    previous = _previous_daily_bar(rows, observed_at)
    high = float(previous.high if previous.high is not None else previous.close)
    low = float(previous.low if previous.low is not None else previous.close)
    close = float(previous.close)
    pivot = (high + low + close) / 3.0
    r1 = 2.0 * pivot - low
    s1 = 2.0 * pivot - high
    r2 = pivot + (high - low)
    s2 = pivot - (high - low)
    r3 = high + 2.0 * (pivot - low)
    s3 = low - 2.0 * (high - pivot)
    price = float(reference_price)
    if price >= r3:
        score = 1.0
        zone = "ABOVE_R3"
    elif price >= r2:
        score = 0.75
        zone = "R2_R3"
    elif price >= r1:
        score = 0.50
        zone = "R1_R2"
    elif price >= pivot:
        score = 0.20
        zone = "P_R1"
    elif price <= s3:
        score = -1.0
        zone = "BELOW_S3"
    elif price <= s2:
        score = -0.75
        zone = "S3_S2"
    elif price <= s1:
        score = -0.50
        zone = "S2_S1"
    else:
        score = -0.20
        zone = "S1_P"
    return {
        "score": score,
        "method": "classic_floor_pivot",
        "source_daily_bar_at": _iso_z(previous.timestamp.astimezone(timezone.utc)),
        "source_high": round(high, 5),
        "source_low": round(low, 5),
        "source_close": round(close, 5),
        "pivot": round(pivot, 5),
        "r1": round(r1, 5),
        "r2": round(r2, 5),
        "r3": round(r3, 5),
        "s1": round(s1, 5),
        "s2": round(s2, 5),
        "s3": round(s3, 5),
        "price_zone": zone,
    }


def _as_of(rows: Sequence[Bar], observed_at: datetime) -> list[Bar]:
    return [bar for bar in rows if bar.timestamp.astimezone(timezone.utc) <= observed_at]


def technical_snapshot(
    hourly_rows: Sequence[Bar],
    daily_rows: Sequence[Bar],
    *,
    reference_price: float,
    observed_at: datetime,
) -> dict[str, Any]:
    h1 = _as_of(sorted(hourly_rows, key=lambda bar: bar.timestamp), observed_at)
    d1 = _as_of(sorted(daily_rows, key=lambda bar: bar.timestamp), observed_at)
    if len(h1) < 220:
        raise ValueError("technical arm requires at least 220 EUR/USD H1 bars")
    if len(d1) < 220:
        raise ValueError("technical arm requires at least 220 EUR/USD D1 bars")

    h1_atr = float(_atr(h1, 14) or 0.0)
    d1_atr = float(_atr(d1, 14) or 0.0)
    if h1_atr <= 0 or d1_atr <= 0:
        raise ValueError("technical arm requires positive H1 and D1 ATR")

    ma_h1 = _ma_structure(h1)
    ma_d1 = _ma_structure(d1)
    ma_score = _clamp(TIMEFRAME_FAST_WEIGHT * float(ma_h1["score"]) + TIMEFRAME_SLOW_WEIGHT * float(ma_d1["score"]))

    macd_h1 = _macd(h1)
    macd_d1 = _macd(d1)
    macd_score = _clamp(TIMEFRAME_FAST_WEIGHT * float(macd_h1["score"]) + TIMEFRAME_SLOW_WEIGHT * float(macd_d1["score"]))

    boll_h1 = _bollinger(h1)
    boll_d1 = _bollinger(d1)
    bollinger_score = _clamp(TIMEFRAME_FAST_WEIGHT * float(boll_h1["score"]) + TIMEFRAME_SLOW_WEIGHT * float(boll_d1["score"]))

    pivot = _classic_pivots(d1, observed_at, reference_price)

    rsi_h1 = float(_rsi(h1, 14))
    rsi_d1 = float(_rsi(d1, 14))
    rsi_score = _clamp(
        TIMEFRAME_FAST_WEIGHT * _clamp((rsi_h1 - 50.0) / 20.0)
        + TIMEFRAME_SLOW_WEIGHT * _clamp((rsi_d1 - 50.0) / 20.0)
    )

    trendline = _trendline(h1, 24)
    if trendline is None:
        raise ValueError("insufficient H1 bars for trendline")
    slope, r2, trendline_last = trendline
    slope_atr_per_bar = float(slope) / h1_atr
    trendline_score = _clamp(slope_atr_per_bar / 0.08) * math.sqrt(max(0.0, float(r2)))

    r3 = _return(h1, 3)
    r12 = _return(h1, 12)
    r24 = _return(h1, 24)
    if None in {r3, r12, r24}:
        raise ValueError("insufficient H1 bars for price momentum")
    momentum_score = _clamp(
        0.30 * _clamp(float(r3) / 0.0025)
        + 0.40 * _clamp(float(r12) / 0.0060)
        + 0.30 * _clamp(float(r24) / 0.0100)
    )

    components = {
        "multi_timeframe_ma": ma_score,
        "pivot_structure": float(pivot["score"]),
        "multi_timeframe_macd": macd_score,
        "multi_timeframe_bollinger": bollinger_score,
        "rsi_momentum": rsi_score,
        "trendline": float(trendline_score),
        "price_momentum": float(momentum_score),
    }
    composite = _clamp(sum(TECHNICAL_WEIGHTS[key] * components[key] for key in TECHNICAL_WEIGHTS))
    score = round(50.0 + 50.0 * composite, 2)
    direction = _direction_from_score(score)

    return {
        "signed_score": round(composite, 6),
        "score": score,
        "direction": direction,
        "confidence": _confidence_from_score(score),
        "components": {key: round(value, 6) for key, value in components.items()},
        "weights": dict(TECHNICAL_WEIGHTS),
        "timeframe_blend": {"H1": TIMEFRAME_FAST_WEIGHT, "D1": TIMEFRAME_SLOW_WEIGHT},
        "indicators": {
            "reference_price": round(float(reference_price), 5),
            "H1": {
                "observed_at": _iso_z(h1[-1].timestamp.astimezone(timezone.utc)),
                "ma": ma_h1,
                "macd": macd_h1,
                "bollinger": boll_h1,
                "rsi14": round(rsi_h1, 4),
                "atr14": round(h1_atr, 6),
                "trendline": {
                    "window_bars": 24,
                    "slope": round(float(slope), 8),
                    "slope_atr_per_bar": round(slope_atr_per_bar, 6),
                    "r2": round(float(r2), 6),
                    "last": round(float(trendline_last), 5),
                },
                "momentum": {
                    "3h": round(float(r3), 8),
                    "12h": round(float(r12), 8),
                    "24h": round(float(r24), 8),
                },
            },
            "D1": {
                "observed_at": _iso_z(d1[-1].timestamp.astimezone(timezone.utc)),
                "ma": ma_d1,
                "macd": macd_d1,
                "bollinger": boll_d1,
                "rsi14": round(rsi_d1, 4),
                "atr14": round(d1_atr, 6),
            },
            "pivot": pivot,
        },
    }


def _direction_from_score(score: float) -> str:
    if float(score) >= LONG_THRESHOLD:
        return "LONG"
    if float(score) <= SHORT_THRESHOLD:
        return "SHORT"
    return "FLAT"


def _confidence_from_score(score: float) -> float:
    if _direction_from_score(score) == "FLAT":
        return 0.0
    return round(min(0.90, abs(float(score) - 50.0) / 50.0), 3)


def _belief_rows(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = payload.get("beliefs")
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("belief_id")): row
        for row in rows
        if isinstance(row, Mapping) and row.get("belief_id")
    }


def belief_snapshot(payload: Mapping[str, Any] | None, observed_at: datetime) -> dict[str, Any]:
    if not payload:
        return {
            "available": False,
            "reason": "belief_state_unavailable",
            "signed_score": None,
            "score": None,
            "direction": "UNAVAILABLE",
            "confidence": None,
            "beliefs": {},
        }
    rows = _belief_rows(payload)
    missing = [belief_id for belief_id in BELIEF_WEIGHTS if belief_id not in rows]
    if missing:
        return {
            "available": False,
            "reason": "missing_required_beliefs",
            "missing_belief_ids": missing,
            "signed_score": None,
            "score": None,
            "direction": "UNAVAILABLE",
            "confidence": None,
            "beliefs": {},
        }

    states: dict[str, Any] = {}
    for belief_id in BELIEF_WEIGHTS:
        row = rows[belief_id]
        try:
            probability = float(row["probability"])
            confidence = float(row["confidence"])
            last_updated = _parse_time(str(row["last_updated"]))
        except (KeyError, TypeError, ValueError):
            return {
                "available": False,
                "reason": "invalid_belief_state",
                "belief_id": belief_id,
                "signed_score": None,
                "score": None,
                "direction": "UNAVAILABLE",
                "confidence": None,
                "beliefs": {},
            }
        if last_updated > observed_at:
            return {
                "available": False,
                "reason": "future_belief_state_rejected",
                "belief_id": belief_id,
                "last_updated": _iso_z(last_updated),
                "market_observed_at": _iso_z(observed_at),
                "signed_score": None,
                "score": None,
                "direction": "UNAVAILABLE",
                "confidence": None,
                "beliefs": {},
            }
        signal = _clamp((2.0 * probability - 1.0) * confidence)
        states[belief_id] = {
            "probability": round(probability, 6),
            "confidence": round(confidence, 6),
            "last_updated": _iso_z(last_updated),
            "age_minutes": round((observed_at - last_updated).total_seconds() / 60.0, 2),
            "signed_support": round(signal, 6),
        }

    signed = _clamp(sum(BELIEF_WEIGHTS[belief_id] * states[belief_id]["signed_support"] for belief_id in BELIEF_WEIGHTS))
    score = round(50.0 + 50.0 * signed, 2)
    return {
        "available": True,
        "reason": None,
        "signed_score": round(signed, 6),
        "score": score,
        "direction": _direction_from_score(score),
        "confidence": _confidence_from_score(score),
        "beliefs": states,
        "weights": dict(BELIEF_WEIGHTS),
        "known_information_overlap_with_arm_a": ["eurusd.trend.bullish"],
    }


def _hybrid_context_signed(belief: Mapping[str, Any]) -> float | None:
    if not belief.get("available"):
        return None
    states = belief.get("beliefs") or {}
    weights = {belief_id: BELIEF_WEIGHTS[belief_id] for belief_id in HYBRID_CONTEXT_BELIEF_IDS}
    total = sum(weights.values()) or 1.0
    return _clamp(sum((weights[belief_id] / total) * float(states[belief_id]["signed_support"]) for belief_id in HYBRID_CONTEXT_BELIEF_IDS))


def build_arms(
    hourly_rows: Sequence[Bar],
    daily_rows: Sequence[Bar],
    *,
    reference_price: float,
    observed_at: datetime,
    belief_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    technical = technical_snapshot(
        hourly_rows,
        daily_rows,
        reference_price=reference_price,
        observed_at=observed_at,
    )
    belief = belief_snapshot(belief_payload, observed_at)

    arm_a = {
        "arm_id": "A",
        "label": "TECHNICAL_ONLY",
        "available": True,
        "direction": technical["direction"],
        "score": technical["score"],
        "confidence": technical["confidence"],
        "decision_influence": False,
        "technical": technical,
    }
    arm_b = {
        "arm_id": "B",
        "label": "BELIEF_ONLY",
        "available": bool(belief.get("available")),
        "direction": belief["direction"],
        "score": belief["score"],
        "confidence": belief["confidence"],
        "decision_influence": False,
        "belief": belief,
    }

    context_signed = _hybrid_context_signed(belief)
    if context_signed is None:
        arm_c = {
            "arm_id": "C",
            "label": "HYBRID",
            "available": False,
            "direction": "UNAVAILABLE",
            "score": None,
            "confidence": None,
            "decision_influence": False,
            "reason": "belief_context_unavailable",
            "overlap_control": {
                "excluded_from_hybrid_context": ["eurusd.trend.bullish"],
                "rationale": "direct EUR/USD trend is already represented by Arm A technical features",
            },
        }
    else:
        technical_signed = float(technical["signed_score"])
        hybrid_signed = _clamp(
            HYBRID_TECHNICAL_WEIGHT * technical_signed
            + HYBRID_BELIEF_CONTEXT_WEIGHT * float(context_signed)
        )
        hybrid_score = round(50.0 + 50.0 * hybrid_signed, 2)
        strong_conflict = (
            technical_signed * float(context_signed) < 0
            and abs(technical_signed) >= 0.35
            and abs(float(context_signed)) >= 0.35
        )
        direction = "FLAT" if strong_conflict else _direction_from_score(hybrid_score)
        confidence = 0.0 if direction == "FLAT" else _confidence_from_score(hybrid_score)
        arm_c = {
            "arm_id": "C",
            "label": "HYBRID",
            "available": True,
            "direction": direction,
            "score": hybrid_score,
            "confidence": confidence,
            "decision_influence": False,
            "hybrid": {
                "signed_score": round(hybrid_signed, 6),
                "technical_signed_score": round(technical_signed, 6),
                "belief_context_signed_score": round(float(context_signed), 6),
                "technical_weight": HYBRID_TECHNICAL_WEIGHT,
                "belief_context_weight": HYBRID_BELIEF_CONTEXT_WEIGHT,
                "strong_conflict_filter_triggered": strong_conflict,
            },
            "technical": technical,
            "belief_context": {belief_id: belief["beliefs"][belief_id] for belief_id in HYBRID_CONTEXT_BELIEF_IDS},
            "overlap_control": {
                "excluded_from_hybrid_context": ["eurusd.trend.bullish"],
                "rationale": "direct EUR/USD trend is already represented by Arm A technical features",
                "remaining_context": list(HYBRID_CONTEXT_BELIEF_IDS),
            },
        }
    return {"A": arm_a, "B": arm_b, "C": arm_c}


def _decision_fingerprint(capture: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "capture_id": capture["capture_id"],
        "engine_version": capture["engine_version"],
        "market_observed_at": capture["market_observed_at"],
        "reference_price": capture["reference_price"],
        "arms": capture["arms"],
        "horizons": {
            key: {"minutes": value["minutes"], "target_at": value["target_at"]}
            for key, value in capture["horizons"].items()
        },
        "research_boundary": capture["research_boundary"],
    }


def build_capture(
    rows_30m: Sequence[Bar],
    belief_payload: Mapping[str, Any] | None,
    *,
    hourly_rows: Sequence[Bar],
    daily_rows: Sequence[Bar],
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    if not rows_30m:
        raise ValueError("EUR/USD 30-minute rows are required")
    observed_at = rows_30m[-1].timestamp.astimezone(timezone.utc)
    captured = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if captured < observed_at:
        captured = observed_at
    reference = round(float(rows_30m[-1].close), 5)
    capture_id = "eurusd-abc-" + _canonical_sha(
        {"engine_version": ENGINE_VERSION, "market_observed_at": _iso_z(observed_at), "reference_price": reference}
    )[:24]
    horizons = {
        f"{minutes}m": {
            "minutes": minutes,
            "target_at": _iso_z(observed_at + timedelta(minutes=minutes)),
            "outcome": None,
        }
        for minutes in HORIZONS_MINUTES
    }
    capture = {
        "capture_id": capture_id,
        "engine_version": ENGINE_VERSION,
        "mode": MODE,
        "captured_at": _iso_z(captured),
        "market_observed_at": _iso_z(observed_at),
        "reference_price": reference,
        "arms": build_arms(
            hourly_rows,
            daily_rows,
            reference_price=reference,
            observed_at=observed_at,
            belief_payload=belief_payload,
        ),
        "horizons": horizons,
        "research_boundary": {
            "prospective_only": True,
            "historical_backfill": False,
            "decision_influence": False,
            "trade_execution": False,
            "belief_writeback": False,
            "automatic_tuning": False,
            "pnl_tuned_weights": False,
            "executable_bid_ask_available": False,
            "cost_adjusted_performance_enabled": False,
        },
    }
    capture["decision_sha256"] = _canonical_sha(_decision_fingerprint(capture))
    return capture


def empty_state(now: datetime | None = None) -> dict[str, Any]:
    activated = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "mode": MODE,
        "activated_at": _iso_z(activated),
        "updated_at": _iso_z(activated),
        "captures": [],
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION or not isinstance(payload.get("captures"), list):
        raise ValueError("invalid EUR/USD A/B/C experiment state")
    return payload


def _find_outcome_bar(rows: Sequence[Bar], target_at: datetime) -> Bar | None:
    eligible = [bar for bar in rows if bar.timestamp.astimezone(timezone.utc) >= target_at]
    return min(eligible, key=lambda bar: bar.timestamp) if eligible else None


def resolve_outcomes(state: Mapping[str, Any], rows: Sequence[Bar]) -> dict[str, Any]:
    payload = json.loads(json.dumps(state))
    for capture in payload.get("captures") or []:
        decision_sha = str(capture.get("decision_sha256") or "")
        if decision_sha != _canonical_sha(_decision_fingerprint(capture)):
            raise ValueError(f"decision fingerprint mismatch for {capture.get('capture_id')}")
        reference = float(capture["reference_price"])
        for horizon in (capture.get("horizons") or {}).values():
            if horizon.get("outcome") is not None:
                continue
            target_at = _parse_time(horizon["target_at"])
            bar = _find_outcome_bar(rows, target_at)
            if bar is None:
                continue
            exit_price = float(bar.close)
            raw_return = exit_price / reference - 1.0 if reference else 0.0
            arm_results: dict[str, Any] = {}
            for arm_id, arm in capture["arms"].items():
                direction = str(arm.get("direction") or "UNAVAILABLE")
                available = bool(arm.get("available"))
                if not available or direction == "UNAVAILABLE":
                    arm_results[arm_id] = {"available": False, "direction": direction, "directional_correct": None, "signed_return_bps": None}
                    continue
                if direction == "LONG":
                    signed = raw_return
                    correct = raw_return > 0
                elif direction == "SHORT":
                    signed = -raw_return
                    correct = raw_return < 0
                else:
                    signed = 0.0
                    correct = None
                arm_results[arm_id] = {
                    "available": True,
                    "direction": direction,
                    "directional_correct": correct,
                    "signed_return_bps": round(signed * 10000.0, 4),
                }
            horizon["outcome"] = {
                "resolved_at": _iso_z(bar.timestamp.astimezone(timezone.utc)),
                "price": round(exit_price, 5),
                "raw_return_bps": round(raw_return * 10000.0, 4),
                "source": "Yahoo Finance EURUSD=X 30m OHLC close",
                "cost_adjusted": False,
                "arms": arm_results,
            }
    if rows:
        payload["updated_at"] = _iso_z(rows[-1].timestamp.astimezone(timezone.utc))
    return payload


def append_capture(state: Mapping[str, Any], capture: Mapping[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(state))
    captures = payload.setdefault("captures", [])
    capture_id = str(capture["capture_id"])
    existing = next((row for row in captures if str(row.get("capture_id")) == capture_id), None)
    if existing is not None:
        if str(existing.get("decision_sha256")) != str(capture.get("decision_sha256")):
            raise ValueError("capture_id collision with different frozen decision")
        return payload
    captures.append(dict(capture))
    captures.sort(key=lambda row: str(row.get("market_observed_at") or ""))
    payload["updated_at"] = str(capture["captured_at"])
    return payload


def performance_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for arm_id in ("A", "B", "C"):
        by_horizon: dict[str, Any] = {}
        for minutes in HORIZONS_MINUTES:
            key = f"{minutes}m"
            rows: list[Mapping[str, Any]] = []
            total_captures = 0
            available_captures = 0
            for capture in state.get("captures") or []:
                outcome = ((capture.get("horizons") or {}).get(key) or {}).get("outcome")
                if outcome is None:
                    continue
                total_captures += 1
                result = (outcome.get("arms") or {}).get(arm_id)
                if not isinstance(result, Mapping) or not result.get("available"):
                    continue
                available_captures += 1
                rows.append(result)
            signals = [row for row in rows if row.get("direction") in {"LONG", "SHORT"}]
            correct = [row for row in signals if row.get("directional_correct") is True]
            signal_returns = [float(row["signed_return_bps"]) for row in signals if row.get("signed_return_bps") is not None]
            strategy_returns = [float(row["signed_return_bps"]) for row in rows if row.get("signed_return_bps") is not None]
            by_horizon[key] = {
                "matured_captures": total_captures,
                "available_captures": available_captures,
                "signals": len(signals),
                "decision_rate": None if not available_captures else round(len(signals) / available_captures, 6),
                "hit_rate": None if not signals else round(len(correct) / len(signals), 6),
                "mean_signed_return_bps_signal_only": None if not signal_returns else round(sum(signal_returns) / len(signal_returns), 4),
                "mean_strategy_return_bps_all_available": None if not strategy_returns else round(sum(strategy_returns) / len(strategy_returns), 4),
            }
        summary[arm_id] = by_horizon
    return summary


def build_report(state: Mapping[str, Any]) -> dict[str, Any]:
    captures = list(state.get("captures") or [])
    latest = captures[-1] if captures else None
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "mode": MODE,
        "decision_influence": False,
        "sample": {"captures": len(captures), "latest_market_observed_at": None if latest is None else latest["market_observed_at"]},
        "arms": {
            "A": {
                "label": "TECHNICAL_ONLY",
                "technical_indicators": [
                    "H1_MA30_MA60_MA100_MA200",
                    "D1_MA30_MA60_MA100_MA200",
                    "classic_daily_pivot_R1_R2_R3_S1_S2_S3",
                    "H1_MACD_12_26_9",
                    "D1_MACD_12_26_9",
                    "H1_Bollinger_20_2",
                    "D1_Bollinger_20_2",
                    "H1_D1_RSI14",
                    "H1_algorithmic_trendline",
                    "H1_ATR14",
                    "H1_price_momentum",
                ],
            },
            "B": {"label": "BELIEF_ONLY", "belief_ids": list(BELIEF_WEIGHTS)},
            "C": {
                "label": "HYBRID",
                "technical_weight": HYBRID_TECHNICAL_WEIGHT,
                "belief_context_weight": HYBRID_BELIEF_CONTEXT_WEIGHT,
                "overlap_control_excludes": ["eurusd.trend.bullish"],
                "belief_context_ids": list(HYBRID_CONTEXT_BELIEF_IDS),
            },
        },
        "performance": performance_summary(state),
        "latest_capture": latest,
        "governance": {
            "prospective_only": True,
            "historical_backfill": False,
            "pnl_tuned_weights": False,
            "automatic_tuning": False,
            "belief_writeback": False,
            "trade_execution": False,
            "active_daily_engine_influence": False,
            "promotion_status": "NOT_ELIGIBLE_FOR_PROMOTION_REVIEW",
            "cost_adjusted_performance_enabled": False,
            "note": "Yahoo OHLC does not provide executable EUR/USD bid/ask; no synthetic spread is fabricated.",
        },
    }


def _load_belief_state(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def run_cycle(
    state_dir: Path,
    *,
    belief_state_path: Path | None = None,
    client: YahooChartClient | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "EURUSD_DAILY_ABC_STATE.json"
    report_path = state_dir / "EURUSD_DAILY_ABC_REPORT.json"
    state = load_state(state_path)
    client = client or YahooChartClient(timeout=15)
    rows_30m = sorted(client.bars(EURUSD, "10d", "30m"), key=lambda bar: bar.timestamp)
    hourly_rows = sorted(client.bars(EURUSD, "1y", "1h"), key=lambda bar: bar.timestamp)
    daily_rows = sorted(client.bars(EURUSD, "2y", "1d"), key=lambda bar: bar.timestamp)
    if len(rows_30m) < 60:
        raise ValueError("EUR/USD 30-minute market data unavailable or insufficient")
    state = resolve_outcomes(state, rows_30m)

    belief_payload = _load_belief_state(belief_state_path)
    capture = build_capture(
        rows_30m,
        belief_payload,
        hourly_rows=hourly_rows,
        daily_rows=daily_rows,
        captured_at=now,
    )
    state = append_capture(state, capture)
    report = build_report(state)

    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state, report


def validate_state(state: Mapping[str, Any]) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected experiment state schema")
    seen: set[str] = set()
    previous_time: datetime | None = None
    for capture in state.get("captures") or []:
        capture_id = str(capture.get("capture_id") or "")
        if not capture_id or capture_id in seen:
            raise ValueError("duplicate or missing capture_id")
        seen.add(capture_id)
        observed = _parse_time(capture["market_observed_at"])
        if previous_time is not None and observed < previous_time:
            raise ValueError("captures are not chronologically ordered")
        previous_time = observed
        if str(capture.get("decision_sha256")) != _canonical_sha(_decision_fingerprint(capture)):
            raise ValueError(f"decision fingerprint mismatch for {capture_id}")
        if capture.get("research_boundary", {}).get("decision_influence") is not False:
            raise ValueError("research capture must have zero decision influence")
        for horizon in (capture.get("horizons") or {}).values():
            if _parse_time(horizon["target_at"]) <= observed:
                raise ValueError("target_at must be prospective")
        arms = capture.get("arms") or {}
        if set(arms) != {"A", "B", "C"}:
            raise ValueError("capture must contain A/B/C arms")
        if "technical" not in arms["A"]:
            raise ValueError("Arm A must contain technical features")
        if arms["C"].get("available") and "technical" not in arms["C"]:
            raise ValueError("available Arm C must contain technical features")
        if arms["B"].get("available") and "technical" in arms["B"]:
            raise ValueError("Arm B must not contain Arm A technical feature payload")
        technical = arms["A"]["technical"]
        indicators = technical.get("indicators") or {}
        for tf in ("H1", "D1"):
            ma = ((indicators.get(tf) or {}).get("ma") or {}).get("values") or {}
            if set(ma) != {"ma30", "ma60", "ma100", "ma200"}:
                raise ValueError(f"{tf} MA contract must be MA30/60/100/200")
            if "macd" not in (indicators.get(tf) or {}) or "bollinger" not in (indicators.get(tf) or {}):
                raise ValueError(f"{tf} must contain MACD and Bollinger")
        pivot = indicators.get("pivot") or {}
        if not {"pivot", "r1", "r2", "r3", "s1", "s2", "s3"}.issubset(pivot):
            raise ValueError("classic Pivot R1-R3/S1-S3 contract missing")


def validate_files(state_dir: Path) -> None:
    state = json.loads((state_dir / "EURUSD_DAILY_ABC_STATE.json").read_text(encoding="utf-8"))
    report = json.loads((state_dir / "EURUSD_DAILY_ABC_REPORT.json").read_text(encoding="utf-8"))
    validate_state(state)
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ValueError("unexpected experiment report schema")
    if report.get("decision_influence") is not False:
        raise ValueError("report must remain research shadow")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--belief-state")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    if args.validate:
        validate_files(state_dir)
        state = json.loads((state_dir / "EURUSD_DAILY_ABC_STATE.json").read_text(encoding="utf-8"))
        print("EURUSD_ABC_EXPERIMENT_OK", len(state.get("captures") or []))
        return 0
    _, report = run_cycle(state_dir, belief_state_path=Path(args.belief_state) if args.belief_state else None)
    latest = report.get("latest_capture") or {}
    arms = latest.get("arms") or {}
    print("EURUSD_ABC_RESEARCH_SHADOW", latest.get("market_observed_at"), {arm_id: (arm.get("direction"), arm.get("score")) for arm_id, arm in arms.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
