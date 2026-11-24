"""Prospective EUR/USD-only WES paper profit exit after hard SL/TP.

Uses verified post-entry *completed* Yahoo 5m OHLC. Historic highs/lows can
measure MFE AS OF NOW, but can never fabricate an earlier exit or a fill at
that extreme. Execution is the current completed 5m CLOSE (mid-price proxy)
with separately disclosed round-trip cost. No new entry, direction authority,
frozen stop/target edit, trailing-stop mutation or BTC/SPX side effects.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite, sqrt
from typing import Any, Mapping, Sequence

PIP = .0001
DAILY_PERIOD = 30
DAILY_DEVIATIONS = 2.5
MAX_MARK_AGE_MINUTES = 12
MIN_PEAK_NET_PIPS = 65.0
MIN_BAND_EXIT_NET_PIPS = 65.0
MIN_GIVEBACK_ABSOLUTE_PIPS = 18.0
GIVEBACK_PEAK_FRACTION = .28
MAX_GIVEBACK_TRIGGER_PIPS = 32.0
MIN_PROFIT_AT_GIVEBACK_NET_PIPS = 4.0
MIN_D1_BAND_PROXIMITY_PIPS = 25.0
BAND_TOUCH_BUFFER_PIPS = 5.0
MAX_D1_AGE_DAYS = 6


def _float(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if isfinite(v) else None


def _time(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else None


def _no(reason: str, **details: Any) -> dict[str, Any]:
    return {"close": False, "reason": reason, "policy": "WES_EURUSD_PROFIT_LOCK_5M_V1",
            "instrument_id": "eurusd", "execution_authority": False,
            "historical_fill_inference": False, "frozen_sl_tp_mutated": False,
            **details}


def daily_band_from_completed_closes(
    closes: Sequence[tuple[datetime, float]], now: datetime
) -> dict[str, Any]:
    """Daily Bollinger 30/2.5 from prior *completed* D1 OHLC only."""
    if now.tzinfo is None:
        return {"available": False, "reason": "unaware_clock"}
    rows = []
    for stamp, value in closes:
        at = stamp.astimezone(timezone.utc) if stamp.tzinfo else None
        p = _float(value)
        if at is None or p is None or p <= 0 or at > now:
            return {"available": False, "reason": "invalid_completed_daily_observation"}
        rows.append((at, p))
    rows.sort()
    if len(rows) < DAILY_PERIOD:
        return {"available": False, "reason": "insufficient_completed_daily"}
    if len(set(t for t, _ in rows)) != len(rows):
        return {"available": False, "reason": "duplicate_daily"}
    age = now.astimezone(timezone.utc) - rows[-1][0]
    if age < timedelta() or age > timedelta(days=MAX_D1_AGE_DAYS):
        return {"available": False, "reason": "stale_completed_daily"}
    values = [v for _, v in rows[-DAILY_PERIOD:]]
    middle = sum(values) / DAILY_PERIOD
    variance = sum((p - middle)**2 for p in values) / DAILY_PERIOD
    sd = sqrt(variance)
    if sd <= 0:
        return {"available": False, "reason": "invalid_daily_band_width"}
    return {"available": True, "asof": rows[-1][0].isoformat(),
            "middle": round(middle, 8), "upper": round(middle + DAILY_DEVIATIONS*sd, 8),
            "lower": round(middle - DAILY_DEVIATIONS*sd, 8),
            "period": DAILY_PERIOD, "deviations": DAILY_DEVIATIONS,
            "source": "YAHOO_EURUSD_PRIOR_COMPLETED_D1",
            "uses_forming_daily_candle": False}


def h1_ma30_from_completed_5m(bars: Sequence[Any], now: datetime) -> float | None:
    """Optional 30-hour MA context, never an execution prerequisite."""
    groups: dict[datetime, dict[int, float]] = {}
    for bar in bars:
        at = getattr(bar, "ts", None)
        close = _float(getattr(bar, "close", None))
        if at is None or at.tzinfo is None or close is None:
            continue
        hour = at.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if hour + timedelta(hours=1) > now:
            continue
        groups.setdefault(hour, {})[at.minute] = close
    candles = []
    for hour, parts in sorted(groups.items()):
        if len(parts) >= 10 and max(parts) >= 50 and min(parts) <= 10:
            candles.append((hour+timedelta(hours=1), parts[max(parts)]))
    if len(candles) < 30:
        return None
    if any(b[0] - a[0] != timedelta(hours=1) for a,b in zip(candles[-30:], candles[-29:])):
        return None
    return round(sum(p for _,p in candles[-30:])/30,8)


def assess(
    item: Mapping[str, Any],
    bars: Sequence[Any],
    completed_daily: Sequence[tuple[datetime, float]],
    checked_at: datetime,
    *,
    round_trip_cost_pips: float,
) -> dict[str, Any]:
    """Decision for *this* completed 5m close; never a historical replayed exit."""
    if str(item.get("instrument_id") or "") != "eurusd":
        return _no("instrument_not_eurusd")
    side = str(item.get("direction") or "")
    if side not in {"long", "short"} or item.get("exit_price") is not None:
        return _no("no_open_directional_leg")
    if checked_at.tzinfo is None:
        return _no("unaware_clock")
    now = checked_at.astimezone(timezone.utc)
    entry = _float(item.get("entry_price"))
    opened = _time(item.get("entry_captured_at"))
    plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), Mapping) else {}
    stop = _float(plan.get("stop_loss_price"))
    tp = _float(plan.get("take_profit_price"))
    cost = _float(round_trip_cost_pips)
    if (entry is None or entry <= 0 or opened is None or opened >= now
            or stop is None or tp is None or cost is None or cost < 0):
        return _no("missing_frozen_entry_risk_or_cost")
    if not (stop > entry > tp if side == "short" else stop < entry < tp):
        return _no("invalid_frozen_risk_geometry")
    rows = sorted((b for b in bars
                   if getattr(b, "ts", None) is not None
                   and b.ts.tzinfo is not None
                   and b.ts >= opened
                   and b.ts + timedelta(minutes=5) <= now),
                  key=lambda b:b.ts)
    if len(rows) < 2:
        return _no("insufficient_completed_post_entry_5m")
    if any(b.ts <= a.ts for a,b in zip(rows, rows[1:])):
        return _no("duplicate_post_entry_5m")
    last = rows[-1]
    price = _float(getattr(last, "close", None))
    if (price is None or price <= 0 or price < last.low - PIP
            or price > last.high + PIP):
        return _no("invalid_completed_5m_close")
    completed_at = last.ts + timedelta(minutes=5)
    age = (now - completed_at).total_seconds()/60
    if age < 0 or age > MAX_MARK_AGE_MINUTES:
        return _no("stale_completed_5m", mark_age_minutes=round(age, 2))

    # All history here is MFE EVIDENCE available now, not an earlier eligible
    # execution price. A missed historical giveback NEVER causes backfilled P/L.
    sign = -1 if side == "short" else 1
    extrema = [(b.low if side == "short" else b.high) for b in rows]
    if any(_float(p) is None or p <= 0 for p in extrema):
        return _no("invalid_historical_5m_extrema")
    completed_closes = [_float(getattr(b, "close", None)) for b in rows]
    if any(v is None or v <= 0 for v in completed_closes):
        return _no("missing_confirmed_5m_closes_for_peak")
    # Use *completed candle closes* for the peak threshold. A solitary
    # extraordinary wick is not evidence of an executable 100-pip profit.
    best_price = min(completed_closes) if side == "short" else max(completed_closes)
    extreme_wick = min(extrema) if side == "short" else max(extrema)
    peak_gross = sign*(best_price-entry)/PIP
    current_gross = sign*(price-entry)/PIP
    peak_net = peak_gross-cost
    current_net = current_gross-cost
    giveback = peak_gross-current_gross
    if peak_net < MIN_PEAK_NET_PIPS:
        return _no("peak_below_profit_protection_threshold",
                   peak_net_pips=round(peak_net,2),
                   current_net_pips=round(current_net,2))

    daily = daily_band_from_completed_closes(completed_daily, now)
    h1_ma30 = h1_ma30_from_completed_5m(rows,now)
    band_near = False
    if daily.get("available"):
        outer = float(daily["lower"] if side == "short" else daily["upper"])
        # The lower/upper Daily band was frozen from PRIOR completed days.
        # The last three completed 5m candles provide a current price touch,
        # not a reconstructed yesterday-only shadow fill.
        recent = [b for b in rows if b.ts >= last.ts - timedelta(minutes=10)]
        touch = (min(b.low for b in recent) <= outer + BAND_TOUCH_BUFFER_PIPS*PIP
                 if side == "short"
                 else max(b.high for b in recent) >= outer - BAND_TOUCH_BUFFER_PIPS*PIP)
        proximity = abs(price-outer)/PIP <= MIN_D1_BAND_PROXIMITY_PIPS
        band_near = bool(touch and proximity)
    band_take = band_near and current_net >= MIN_BAND_EXIT_NET_PIPS
    giveback_limit = min(MAX_GIVEBACK_TRIGGER_PIPS,
                         max(MIN_GIVEBACK_ABSOLUTE_PIPS,
                             GIVEBACK_PEAK_FRACTION*peak_gross))
    lock = (giveback >= giveback_limit
            and current_net >= MIN_PROFIT_AT_GIVEBACK_NET_PIPS)
    evidence = {
        "policy": "WES_EURUSD_PROFIT_LOCK_5M_V1",
        "source": str(getattr(last,"source","")),
        "peak_basis": "POST_ENTRY_5M_EXTREME_ASOF_THIS_CYCLE_NOT_A_HISTORICAL_FILL",
        "peak_price": round(best_price,8),
        "extreme_wick_price_not_used_for_peak": round(extreme_wick,8),
        "peak_gross_pips": round(peak_gross,2),
        "peak_net_proxy_pips": round(peak_net,2),
        "current_gross_pips": round(current_gross,2),
        "current_net_proxy_pips": round(current_net,2),
        "giveback_pips": round(giveback,2),
        "giveback_trigger_pips": round(giveback_limit,2),
        "cost_assumption_pips": round(cost,2),
        "daily_bollinger": daily,
        "daily_band_touch_now": band_near,
        "h1_ma30_completed": h1_ma30,
        "closed_5m_at": completed_at.isoformat(),
        "evaluated_at": now.isoformat(),
        "paper_exit_price_basis": "YAHOO_COMPLETED_5M_CLOSE_MID_PROXY",
        "historical_fill_inference": False,
        "frozen_sl_tp_mutated": False,
    }
    if not band_take and not lock:
        return _no("profit_protection_not_triggered",**evidence)
    return {
        **evidence, "close": True, "execution_authority": False,
        "exit_reason": ("wes_eurusd_daily_band_profit_take"
                        if band_take else "wes_eurusd_peak_profit_giveback_exit"),
        "exit_price": price,
        "exit_captured_at": completed_at.isoformat(),
        "reason": "D1_OUTER_BAND_PROFIT_TAKE" if band_take else "VERIFIED_MFE_GIVEBACK",
    }
