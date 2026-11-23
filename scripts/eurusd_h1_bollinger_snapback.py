"""Shared EUR/USD H1 Bollinger 30/2.5 snapback *evidence*.

Pure, source-agnostic price-pattern primitive. No trading direction, order,
risk, policy or close authority. Consumers must independently enforce their
existing Champion/Belief, price, risk and NO RETROACTIVE contracts.

Input H1 tuples are (completed_at, open, high, low, close), where timestamps
mark *CLOSES*, not opens. An unfinished hour must never be supplied.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite, sqrt
from typing import Any, Iterable

PERIOD = 30
DEVIATIONS = 2.5
PIP = 0.0001
MAX_H1_AGE_MINUTES = 90
BREACH_LOOKBACK_HOURS = 2
MIN_BREACH_PIPS = 4.0
MIN_BREACH_BAND_WIDTH_FRACTION = 0.08
MIN_REENTRY_PIPS = 1.5
MIN_REENTRY_WIDTH_FRACTION = 0.025
MIN_RECOVERY_FROM_EXTREME_PIPS = 6.0
MIN_LAST_H1_REBOUND_PIPS = 1.5


def _invalid(reason: str, **details: Any) -> dict[str, Any]:
    return {
        "available": False, "confirmed": False, "pattern": "UNAVAILABLE",
        "reason": reason, "period": PERIOD, "deviations": DEVIATIONS,
        "direction_authority": False, "execution_authority": False,
        **details,
    }


def _band(values: list[float]) -> tuple[float, float, float]:
    segment = values[-PERIOD:]
    mean = sum(segment) / PERIOD
    std = sqrt(sum((v - mean) ** 2 for v in segment) / PERIOD)
    return mean, mean + DEVIATIONS * std, mean - DEVIATIONS * std


def assess_closed_h1(
    candles: Iterable[tuple[datetime, float, float, float, float]],
    observed_at: datetime,
    *,
    source: str,
) -> dict[str, Any]:
    """Only a past extreme + subsequent completed H1 reentry may qualify.

    Either a completed wick or a completed close can pierce the *prior* band;
    the NEXT completed H1 (or the one after) must finish clearly back inside
    the current band and reverse in the opposite direction.
    """
    if observed_at.tzinfo is None:
        return _invalid("unaware_observation_clock")
    now = observed_at.astimezone(timezone.utc)
    rows = []
    try:
        for stamp, o, h, l, c in candles:
            if stamp.tzinfo is None:
                return _invalid("unaware_candle_timestamp")
            at = stamp.astimezone(timezone.utc)
            prices = tuple(float(v) for v in (o, h, l, c))
            if (not all(isfinite(v) and v > 0 for v in prices)
                    or prices[1] < max(prices[0], prices[3])
                    or prices[2] > min(prices[0], prices[3])):
                return _invalid("invalid_h1_ohlc")
            if at > now or at.minute or at.second or at.microsecond:
                return _invalid("future_or_unclosed_h1")
            rows.append((at, *prices))
    except (TypeError, ValueError, AttributeError, OverflowError):
        return _invalid("invalid_h1_candle")
    rows.sort(key=lambda row: row[0])
    if len(rows) < PERIOD + 4:
        return _invalid("insufficient_closed_h1", closed_h1=len(rows))
    if any(right[0] <= left[0] for left, right in zip(rows, rows[1:])):
        return _invalid("duplicate_or_out_of_order_h1")
    age = (now - rows[-1][0]).total_seconds() / 60.0
    if not 0 <= age <= MAX_H1_AGE_MINUTES:
        return _invalid("stale_closed_h1", age_minutes=round(age, 2))
    # A break (weekend or missing quote) in the last three hours invalidates
    # the quick-reentry interpretation instead of interpolating false candles.
    tail = rows[-(BREACH_LOOKBACK_HOURS + 2):]
    if any((b[0] - a[0]) != timedelta(hours=1) for a, b in zip(tail, tail[1:])):
        return _invalid("gapped_recovery_h1")
    closes = [row[4] for row in rows]
    mid, upper, lower = _band(closes)
    width = upper - lower
    if width <= 0 or not isfinite(width):
        return _invalid("invalid_band_width")
    last = rows[-1]
    prior = rows[-2]
    lower_confirm = last[4] >= lower + max(MIN_REENTRY_PIPS * PIP, MIN_REENTRY_WIDTH_FRACTION * width)
    upper_confirm = last[4] <= upper - max(MIN_REENTRY_PIPS * PIP, MIN_REENTRY_WIDTH_FRACTION * width)
    rising = last[4] - prior[4] >= MIN_LAST_H1_REBOUND_PIPS * PIP
    falling = prior[4] - last[4] >= MIN_LAST_H1_REBOUND_PIPS * PIP

    result = {
        "available": True, "confirmed": False, "pattern": "NONE",
        "reason": "no_verified_fast_outer_band_reentry",
        "period": PERIOD, "deviations": DEVIATIONS,
        "source": source, "last_closed_h1": last[0].isoformat(),
        "age_minutes": round(age, 2), "closed_h1": len(rows),
        "last_h1_close": round(last[4], 8),
        "lower": round(lower, 8), "upper": round(upper, 8),
        "middle": round(mid, 8), "width_pips": round(width / PIP, 3),
        "direction_authority": False, "execution_authority": False,
    }
    # A breach of the previous, frozen-in-time band; no use of future
    # closes when testing whether the extreme was actually beyond the band.
    for index in range(len(rows) - 2, len(rows) - BREACH_LOOKBACK_HOURS - 2, -1):
        if index < PERIOD:
            continue
        breach = rows[index]
        _, prev_upper, prev_lower = _band(closes[index - PERIOD:index])
        prior_width = prev_upper - prev_lower
        depth = max(MIN_BREACH_PIPS * PIP, MIN_BREACH_BAND_WIDTH_FRACTION * prior_width)
        drop = prev_lower - breach[3]
        rise = breach[2] - prev_upper
        enough_lower_recovery = last[4] - breach[3] >= MIN_RECOVERY_FROM_EXTREME_PIPS * PIP
        enough_upper_recovery = breach[2] - last[4] >= MIN_RECOVERY_FROM_EXTREME_PIPS * PIP
        if drop >= depth and lower_confirm and rising and enough_lower_recovery:
            return {**result, "confirmed": True, "pattern": "LOWER_FAST_REENTRY",
                    "reason": "verified_lower_band_breach_and_h1_reentry",
                    "breach_h1": breach[0].isoformat(),
                    "breach_depth_pips": round(drop / PIP, 3),
                    "rebound_from_extreme_pips": round((last[4] - breach[3]) / PIP, 3),
                    "counter_to_direction": "SHORT"}
        if rise >= depth and upper_confirm and falling and enough_upper_recovery:
            return {**result, "confirmed": True, "pattern": "UPPER_FAST_REENTRY",
                    "reason": "verified_upper_band_breach_and_h1_reentry",
                    "breach_h1": breach[0].isoformat(),
                    "breach_depth_pips": round(rise / PIP, 3),
                    "rebound_from_extreme_pips": round((breach[2] - last[4]) / PIP, 3),
                    "counter_to_direction": "LONG"}
    return result


def blocks_direction(result: dict[str, Any], direction: str) -> bool:
    side = str(direction).strip().upper()
    return bool(result.get("available") is True
                and result.get("confirmed") is True
                and result.get("counter_to_direction") == side)
