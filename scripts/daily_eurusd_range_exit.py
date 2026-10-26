"""Prospective H1 range-aware exit for the EUR/USD Daily *production* lifecycle.

Only observes completed H1 candles reconstructed from canonical 1m EURUSD=X
data. No broker execution, no direction authority, no retrospective fills,
and no modification of the hard SL/TP or legacy R_PACE rules.
When H1 data are incomplete, remain with the existing production lifecycle.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import isfinite, sqrt
from typing import Any, Mapping, Sequence

PIP = 0.0001
PERIOD = 20
DEVIATIONS = 2.0
MIN_MINUTES_PER_HOUR = 40
MAX_CLOSED_H1_AGE_MINUTES = 90
MAX_LAST_BAR_AGE_SECONDS = 180
MAX_WIDTH_PIPS = 70.0
MAX_OUTER_BAND_SLOPE_3H_PIPS = 8.0
MAX_BAND_DISTANCE_PIPS = 12.0
MIN_NET_PROFIT_PIPS = 7.0
MAX_LAST_15M_MOMENTUM_PIPS = 1.5
MIN_MFE_PIPS_FOR_GIVEBACK = 10.0
MIN_GIVEBACK_PIPS = 4.0


def _bands(closes: Sequence[float]) -> tuple[float, float, float]:
    values = closes[-PERIOD:]
    middle = sum(values) / PERIOD
    dev = sqrt(sum((x - middle) ** 2 for x in values) / PERIOD)
    return middle, middle + DEVIATIONS * dev, middle - DEVIATIONS * dev


def _hourly_closes(bars: Sequence[Any], observed_at: datetime) -> list[tuple[datetime, float]]:
    buckets: dict[datetime, list[tuple[datetime, float]]] = defaultdict(list)
    for bar in bars:
        at = bar.timestamp.astimezone(timezone.utc)
        if at > observed_at:
            continue
        hour = at.replace(minute=0, second=0, microsecond=0)
        if hour + timedelta(hours=1) > observed_at:
            continue  # no currently forming H1 candle / no lookahead
        try:
            value = float(bar.close)
        except (TypeError, ValueError):
            continue
        if isfinite(value) and value > 0:
            buckets[hour].append((at, value))
    completed = []
    for hour, minute_bars in sorted(buckets.items()):
        distinct_minutes = {when.replace(second=0, microsecond=0) for when, _ in minute_bars}
        if len(distinct_minutes) < MIN_MINUTES_PER_HOUR:
            continue
        completed.append((hour + timedelta(hours=1), max(minute_bars)[1]))
    return completed


def assess(position: Mapping[str, Any], bars: Sequence[Any], observed_at: datetime,
           *, exit_price_at_mid: Any) -> dict[str, Any]:
    """Return a decision based strictly on information available at observed_at.

    Current price and MFE are converted to the same executable-side convention
    as the production exit engine, not to optimistic mid-only P&L.
    """
    direction = str(position.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"} or not bars:
        return {"close": False, "reason": "invalid_position_or_bars"}
    observed_at = observed_at.astimezone(timezone.utc)
    relevant = sorted((b for b in bars if b.timestamp <= observed_at), key=lambda b: b.timestamp)
    if not relevant:
        return {"close": False, "reason": "no_asof_bars"}
    latest = relevant[-1]
    if not 0 <= (observed_at - latest.timestamp).total_seconds() <= MAX_LAST_BAR_AGE_SECONDS:
        return {"close": False, "reason": "stale_minute_bar"}
    hourly = _hourly_closes(relevant, observed_at)
    if len(hourly) < PERIOD + 3:
        return {"close": False, "reason": "insufficient_complete_h1", "count": len(hourly)}
    if (observed_at - hourly[-1][0]).total_seconds() > MAX_CLOSED_H1_AGE_MINUTES * 60:
        return {"close": False, "reason": "stale_complete_h1"}

    closes = [value for _, value in hourly]
    middle, upper, lower = _bands(closes)
    prior = _bands(closes[:-3])
    width_pips = (upper - lower) / PIP
    outer = upper if direction == "LONG" else lower
    prior_outer = prior[1] if direction == "LONG" else prior[2]
    slope_pips = abs((outer - prior_outer) / PIP)
    if not all(isfinite(v) for v in (middle, upper, lower, width_pips, slope_pips)):
        return {"close": False, "reason": "nonfinite_geometry"}
    if width_pips <= 0.1 or width_pips > MAX_WIDTH_PIPS or slope_pips > MAX_OUTER_BAND_SLOPE_3H_PIPS:
        return {"close": False, "reason": "trend_or_unreliable_range",
                "width_pips": round(width_pips, 2), "outer_slope_3h_pips": round(slope_pips, 2)}

    try:
        entry = float(position["entry"])
        stop = float(position["stop"])
        mark = float(exit_price_at_mid(position, float(latest.close)))
    except (ValueError, TypeError, KeyError):
        return {"close": False, "reason": "invalid_execution_basis"}
    if not all(isfinite(x) for x in (entry, stop, mark)) or abs(entry - stop) < PIP:
        return {"close": False, "reason": "invalid_risk"}
    sign = 1 if direction == "LONG" else -1
    profit_pips = sign * (mark - entry) / PIP
    band_gap_pips = sign * (outer - float(latest.close)) / PIP
    if profit_pips < MIN_NET_PROFIT_PIPS or not -2.0 <= band_gap_pips <= MAX_BAND_DISTANCE_PIPS:
        return {"close": False, "reason": "not_profitable_near_outer_band",
                "profit_pips": round(profit_pips, 2), "band_gap_pips": round(band_gap_pips, 2)}

    opened_text = str(position.get("opened_at") or "").replace("Z", "+00:00")
    try:
        opened = datetime.fromisoformat(opened_text).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return {"close": False, "reason": "invalid_open_time"}
    trade_bars = [b for b in relevant if b.timestamp >= opened]
    if not trade_bars:
        return {"close": False, "reason": "no_trade_bars"}

    best_mid = (max(float(b.high if b.high is not None else b.close) for b in trade_bars)
                if sign > 0 else min(float(b.low if b.low is not None else b.close) for b in trade_bars))
    best_profit = sign * (float(exit_price_at_mid(position, best_mid)) - entry) / PIP
    giveback = max(0.0, best_profit - profit_pips)
    earlier = [b for b in relevant if b.timestamp <= latest.timestamp - timedelta(minutes=15)]
    if not earlier or (latest.timestamp - earlier[-1].timestamp).total_seconds() > 20 * 60:
        return {"close": False, "reason": "missing_recent_momentum"}
    momentum = sign * (float(latest.close) - float(earlier[-1].close)) / PIP
    weakening = momentum <= MAX_LAST_15M_MOMENTUM_PIPS
    returned_from_high = best_profit >= MIN_MFE_PIPS_FOR_GIVEBACK and giveback >= MIN_GIVEBACK_PIPS
    close = (weakening or returned_from_high or band_gap_pips <= 3.0)
    return {
        "close": bool(close),
        "reason": "range_profit_near_outer_band" if close else "momentum_supports_hold",
        "regime": "RANGE",
        "upper": round(upper, 5), "lower": round(lower, 5),
        "middle": round(middle, 5), "width_pips": round(width_pips, 2),
        "outer_slope_3h_pips": round(slope_pips, 2),
        "profit_pips": round(profit_pips, 2), "band_gap_pips": round(band_gap_pips, 2),
        "mfe_pips": round(best_profit, 2), "giveback_pips": round(giveback, 2),
        "momentum_15m_pips": round(momentum, 2),
        "last_closed_h1": hourly[-1][0].isoformat(),
        "last_minute": latest.timestamp.isoformat(),
        "partial_close_supported": False,
    }
