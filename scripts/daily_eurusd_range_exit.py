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

import eurusd_h1_bollinger_snapback as snapback

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


def assess_snapback(
    position: Mapping[str, Any],
    bars: Sequence[Any],
    observed_at: datetime,
    *,
    exit_price_at_mid: Any,
) -> dict[str, Any]:
    """Daily H1 Bollinger 30/2.5 profit protection on canonical 1m candles.

    A quick lower-band reentry protects a winning SHORT; upper protects LONG.
    Hard SL/TP and ordinary dynamic exits remain prior authority. No CLOSE is
    permitted on a still-forming H1, a stale minute bar or a pre-entry breach.
    """
    base = {"close": False, "policy": "H1_BOLLINGER_FAST_REENTRY_30_2_5",
            "direction_authority": False, "risk_plan_mutation": False}
    direction = str(position.get("direction") or "").upper()
    if direction not in {"SHORT", "LONG"} or observed_at.tzinfo is None:
        return {**base, "reason": "invalid_position_or_observation"}
    now = observed_at.astimezone(timezone.utc)
    completed = []
    for bar in sorted(bars, key=lambda x: x.timestamp):
        at = bar.timestamp.astimezone(timezone.utc)
        if at + timedelta(minutes=1) > now or at > now:
            continue
        if any(v is None for v in (bar.open, bar.high, bar.low, bar.close)):
            continue
        values = (float(bar.open), float(bar.high), float(bar.low), float(bar.close))
        if (not all(isfinite(v) and v > 0 for v in values)
                or values[1] < max(values[0], values[3])
                or values[2] > min(values[0], values[3])):
            continue
        completed.append((at, values))
    if not completed:
        return {**base, "reason": "no_completed_minute_bars"}
    latest_at, latest_ohlc = completed[-1]
    seconds = (now - latest_at - timedelta(minutes=1)).total_seconds()
    if seconds < 0 or seconds > MAX_LAST_BAR_AGE_SECONDS:
        return {**base, "reason": "stale_canonical_1m"}

    buckets = defaultdict(dict)
    for at, prices in completed:
        hour = at.replace(minute=0, second=0, microsecond=0)
        if hour + timedelta(hours=1) > now:
            continue
        if at.second or at.microsecond:
            continue
        buckets[hour][at.minute] = prices
    hourly = []
    for hour, minutes in sorted(buckets.items()):
        # Demand enough genuine minutes across the hour. Never treat a few
        # scattered ticks as a complete H1 candle.
        if (len(minutes) < 50 or min(minutes) > 5 or max(minutes) < 54):
            continue
        first, last = minutes[min(minutes)], minutes[max(minutes)]
        hourly.append((hour + timedelta(hours=1), first[0],
                       max(v[1] for v in minutes.values()),
                       min(v[2] for v in minutes.values()), last[3]))
    signal = snapback.assess_closed_h1(
        hourly, now, source="DAILY_EURUSD_CANONICAL_COMPLETED_1M_TO_H1")
    if not snapback.blocks_direction(signal, direction):
        return {**base, "reason": signal.get("reason"), "signal": signal}
    try:
        opened = datetime.fromisoformat(str(position["opened_at"]).replace("Z", "+00:00"))
        breach = datetime.fromisoformat(signal["breach_h1"])
        h1_closed = datetime.fromisoformat(signal["last_closed_h1"])
        entry = float(position["entry"])
        stop = float(position["stop"])
        mark = float(exit_price_at_mid(position, latest_ohlc[3]))
    except (ValueError, KeyError, TypeError):
        return {**base, "reason": "invalid_entry_or_exec_basis", "signal": signal}
    if (opened.tzinfo is None or opened > breach or latest_at < h1_closed
            or latest_at <= opened or not all(isfinite(x) for x in (entry, stop, mark))):
        return {**base, "reason": "pre_entry_signal_or_invalid_mark", "signal": signal}
    risk = abs(entry - stop)
    if risk <= 0:
        return {**base, "reason": "invalid_risk_geometry", "signal": signal}
    side = -1.0 if direction == "SHORT" else 1.0
    profit_pips = side * (mark - entry) / PIP
    profit_r = side * (mark - entry) / risk
    if profit_pips < 6.0 or profit_r < 0.20:
        return {**base, "reason": "insufficient_executable_profit",
                "profit_pips": round(profit_pips, 2), "profit_r": round(profit_r, 3),
                "signal": signal}
    previous = next(((at, row) for at, row in reversed(completed)
                     if at <= latest_at - timedelta(minutes=15)), None)
    if previous is None or (latest_at - previous[0]).total_seconds() > 20 * 60:
        return {**base, "reason": "unverified_counter_momentum", "signal": signal}
    against_pips = -side * (latest_ohlc[3] - previous[1][3]) / PIP
    if against_pips < 1.5:
        return {**base, "reason": "no_recent_countertrend_followthrough",
                "counter_momentum_15m_pips": round(against_pips, 2), "signal": signal}
    return {
        **base, "close": True, "reason": "winning_position_h1_outer_band_snapback",
        "signal": signal, "profit_pips": round(profit_pips, 2),
        "profit_r": round(profit_r, 3),
        "counter_momentum_15m_pips": round(against_pips, 2),
        "last_completed_1m": (latest_at + timedelta(minutes=1)).isoformat(),
        "execution_basis": "EXISTING_DAILY_POSITION_EXIT_MID_TO_EXEC_SIDE",
    }


def assess_extreme_profit(
    position: Mapping[str, Any], bars: Sequence[Any], observed_at: datetime,
    *, exit_price_at_mid: Any,
) -> dict[str, Any]:
    """Current favorable H1 extension is a profit exit, not a reversal trade.

    Reference uses the prior completed 30 H1 candles, and the mark is the
    latest completed 1m close. The currently forming H1 need not finish.
    """
    base = {"close": False, "policy": "DAILY_H1_EXTREME_PROFIT_V1",
            "direction_authority": False, "risk_plan_mutation": False}
    if observed_at.tzinfo is None:
        return {**base, "reason": "unaware_clock"}
    try:
        side = str(position["direction"]).upper()
        opened = datetime.fromisoformat(str(position["opened_at"]).replace("Z", "+00:00"))
        entry, stop, target = (float(position[k]) for k in ("entry", "stop", "target"))
        if (side not in {"LONG", "SHORT"} or opened.tzinfo is None
                or not all(isfinite(p) and p > 0 for p in (entry, stop, target))
                or not (stop > entry > target if side == "SHORT" else stop < entry < target)):
            return {**base, "reason": "invalid_frozen_risk"}
        eligible = sorted((b for b in bars if b.timestamp.tzinfo is not None
                           and b.timestamp >= opened
                           and b.timestamp + timedelta(minutes=1) <= observed_at),
                          key=lambda b: b.timestamp)
        if not eligible:
            return {**base, "reason": "no_completed_post_entry_mark"}
        last = eligible[-1]
        at = last.timestamp
        completed_at = at + timedelta(minutes=1)
        if (at.second or at.microsecond
                or sum(b.timestamp == at for b in eligible) != 1
                or not timedelta(0) <= observed_at - completed_at <= timedelta(seconds=120)):
            return {**base, "reason": "stale_or_duplicate_mark"}
        mid, low, high = (float(getattr(last,k)) for k in ("close","low","high"))
        if not all(isfinite(v) and v > 0 for v in (mid, low, high)) or not low <= mid <= high:
            return {**base, "reason": "invalid_mark"}
        band = snapback.completed_h1_band(bars, at, minutes=1, timestamp_field="timestamp")
        signal = snapback.assess_profit_extension(
            band, side, mid, observed_at=completed_at, reference_before=at, timeframe="H1")
        mark = float(exit_price_at_mid(position, mid))
        profit = (entry-mark if side == "SHORT" else mark-entry)
        risk = abs(entry-stop)
        required = max(10.0 * PIP, 0.50 * risk)
        dynamics = band.get("bollinger_dynamics") or {}
        dynamics_review = snapback.dynamics_profit_review(
            dynamics, side, opened, mid, completed_at,
            profit_pips=profit/PIP, risk_pips=risk/PIP, minimum_pips=10.0)
        extreme = bool(signal.get("extreme") and isfinite(mark) and mark > 0
                       and profit >= required)
        qualifies = extreme or bool(dynamics_review.get("close") and isfinite(mark) and mark > 0)
        return {**base, "close": qualifies,
                "reason": ("favorable_extreme_profit_realization" if extreme
                           else dynamics_review["reason"] if qualifies else "no_qualified_extreme"),
                "exit_reason": ("DYNAMIC_H1_BOLLINGER_EXTREME_PROFIT_EXIT" if extreme
                                else "DYNAMIC_H1_BOLLINGER_COMPRESSION_PROFIT_EXIT"),
                "bollinger_dynamics": dynamics, "dynamics_review": dynamics_review,
                "signal": signal, "profit_pips": round(profit/PIP,3),
                "profit_r": round(profit/risk,4),
                "min_profit_pips": round(required/PIP,3),
                "mark_bar_open": at.isoformat(), "mark_completed_at": completed_at.isoformat(),
                "exit_price": mark,
                "exposure_reduction": {"intent": "REDUCE_EXPOSURE" if qualifies else "HOLD",
                    "requested_reduction_fraction": 1.0 if qualifies else 0.0,
                    "partial_execution_supported": False,
                    "trend_reversal_required": False}}
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        return {**base, "reason": "invalid_extension_input"}
