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
        "bollinger_dynamics": assess_dynamics([(r[0],r[4]) for r in rows], now),
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


# A distinct, price-extension profit-taking signal, NOT a trend reversal.
# Calibrated starting values, not a statistically proven optimum.
EXTREME_MIN_PIPS = 5.0
EXTREME_WIDTH_FRACTION = 0.10
EXTREME_ATR_FRACTION = 0.50


def completed_h1_band(bars: Iterable, asof: datetime, *, minutes: int,
                      timestamp_field: str) -> dict:
    """Build a pre-mark 30/2.5 band and ATR14 from complete subhour buckets.

    Each input timestamp marks the OPEN. Only hours completed before or at
    asof (the OPEN of the price-test candle) enter the indicator. A full
    hour requires every unique expected constituent bar, with valid OHLC.
    Market session gaps may occur in the historical window; a stale latest
    hour cannot authorize a signal. No synthetic missing observations.
    """
    if asof.tzinfo is None or minutes not in (1, 5):
        return {"available": False, "reason": "invalid_clock_or_interval"}
    now = asof.astimezone(timezone.utc)
    groups = {}
    for bar in bars:
        try:
            at = getattr(bar, timestamp_field)
            if at.tzinfo is None:
                continue
            at = at.astimezone(timezone.utc)
            h, l, c = (float(getattr(bar, k)) for k in ("high", "low", "close"))
            if (at.second or at.microsecond or at.minute % minutes
                    or at + timedelta(minutes=minutes) > now
                    or not all(isfinite(v) and v > 0 for v in (h, l, c))
                    or not l <= c <= h):
                continue
            hour = at.replace(minute=0, second=0, microsecond=0)
            if hour + timedelta(hours=1) > now:
                continue
            groups.setdefault(hour, {}).setdefault(at.minute, []).append((h, l, c))
        except (AttributeError, ValueError, TypeError, OverflowError):
            continue
    candles = []
    for hour, group in sorted(groups.items()):
        if set(group) != set(range(0, 60, minutes)) or any(len(v) != 1 for v in group.values()):
            continue
        rows = [group[m][0] for m in range(0, 60, minutes)]
        candles.append((hour + timedelta(hours=1), max(r[0] for r in rows),
                        min(r[1] for r in rows), rows[-1][2]))
    if len(candles) < PERIOD:
        return {"available": False, "reason": "insufficient_complete_h1"}
    latest = candles[-1][0]
    if not timedelta(0) <= now - latest <= timedelta(minutes=MAX_H1_AGE_MINUTES):
        return {"available": False, "reason": "stale_h1_reference"}
    mid, upper, lower = _band([r[3] for r in candles])
    tr = [max(b[1]-b[2], abs(b[1]-a[3]), abs(b[2]-a[3]))
          for a,b in zip(candles[-15:-1], candles[-14:])]
    return {"available": upper > lower, "asof": latest.isoformat(),
            "period": PERIOD, "deviations": DEVIATIONS,
            "middle": mid, "upper": upper, "lower": lower,
            "atr14": sum(tr)/len(tr), "timeframe": "H1",
            "bollinger_dynamics": assess_dynamics([(r[0],r[3]) for r in candles], now)}


def assess_profit_extension(band: dict, direction: str, current_mid: float,
                            *, observed_at: datetime, reference_before: datetime,
                            timeframe: str) -> dict:
    """Measure a CURRENT favorable extension beyond an already-known band.

    No rebound, trend-change prediction or wick-only fill is required/allowed.
    This is a pure exposure-reduction recommendation, not an order or entry.
    D1 uses band width/sigma; H1 additionally uses its verified ATR14.
    """
    result = {"available": False, "extreme": False, "timeframe": timeframe,
              "direction_authority": False, "execution_authority": False,
              "trend_reversal_required": False, "intent": "HOLD",
              "requested_reduction_fraction": 0.0}
    if not band.get("available") or direction.upper() not in {"LONG", "SHORT"}:
        return {**result, "reason": "reference_unavailable"}
    try:
        asof = datetime.fromisoformat(str(band["asof"]).replace("Z", "+00:00"))
        upper, lower, mid = (float(band[k]) for k in ("upper", "lower", "middle"))
        px = float(current_mid)
        if (observed_at.tzinfo is None or reference_before.tzinfo is None
                or asof.tzinfo is None or not asof <= reference_before <= observed_at
                or not all(isfinite(v) and v > 0 for v in (upper, lower, mid, px))
                or not lower < mid < upper):
            return {**result, "reason": "invalid_or_lookahead_reference"}
        max_age = timedelta(minutes=90) if timeframe == "H1" else timedelta(days=4)
        if observed_at - asof > max_age:
            return {**result, "reason": "stale_reference"}
        width = upper - lower
        atr = float(band.get("atr14", 0))
        if not isfinite(atr) or atr < 0 or (timeframe == "H1" and atr <= 0):
            return {**result, "reason": "invalid_atr"}
        required = max(EXTREME_MIN_PIPS * PIP, EXTREME_WIDTH_FRACTION * width,
                       EXTREME_ATR_FRACTION * atr)
        depth = lower - px if direction.upper() == "SHORT" else px - upper
        extreme = depth >= required
        return {**result, "available": True, "extreme": extreme,
                "reason": "favorable_extreme_profit_realization" if extreme else "not_extreme",
                "intent": "REDUCE_EXPOSURE" if extreme else "HOLD",
                # The current ledger is all-or-nothing. Future quantity-aware
                # execution must explicitly authorize a partial fraction.
                "requested_reduction_fraction": 1.0 if extreme else 0.0,
                "band_asof": asof.isoformat(), "mark_observed_at": observed_at.isoformat(),
                "band_lower": lower, "band_upper": upper, "band_middle": mid,
                "depth_pips": round(depth/PIP, 3),
                "required_depth_pips": round(required/PIP, 3),
                "width_pips": round(width/PIP, 3),
                "atr14_pips": round(atr/PIP, 3) if atr > 0 else None}
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        return {**result, "reason": "invalid_extension_inputs"}


def assess_dynamics(closed_prices, observed_at):
    """Bollinger Dynamics Engine V1: pure, as-of H1 regime evidence.

    Input timestamps denote completed H1. Slopes use three consecutive
    intervals; acceleration compares against the preceding three. Units
    pips/hour and pips/hour². Classification is not direction authority.
    """
    base = {"available": False, "policy": "BOLLINGER_DYNAMICS_H1_V1",
            "regime": "UNAVAILABLE", "direction_authority": False,
            "execution_authority": False, "period": PERIOD, "deviations": DEVIATIONS}
    try:
        if observed_at.tzinfo is None:
            return {**base, "reason": "unaware_clock"}
        rows = []
        for t,p in closed_prices:
            if t.tzinfo is None:
                return {**base, "reason": "unaware_h1_timestamp"}
            rows.append((t.astimezone(timezone.utc), float(p)))
        rows.sort()
        if len(rows) < PERIOD + 6:
            return {**base, "reason": "insufficient_dynamics_history"}
        if any(not isfinite(p) or p <= 0 or t > observed_at or t.minute
               or t.second or t.microsecond for t,p in rows):
            return {**base, "reason": "invalid_or_future_observation"}
        if len({t for t,p in rows}) != len(rows):
            return {**base, "reason": "duplicate_h1"}
        if not timedelta(0) <= observed_at-rows[-1][0] <= timedelta(minutes=90):
            return {**base, "reason": "stale_h1"}
        if any(b[0]-a[0] != timedelta(hours=1) for a,b in zip(rows[-7:],rows[-6:])):
            return {**base, "reason": "gapped_recent_h1"}
        # Up to 25 continuous rolling snapshots (24h), so a delayed
        # contraction can still refer to its preceding expansion. Do not
        # bridge a weekend or a missing hour when measuring shape changes.
        prices = [p for _,p in rows]
        snapshots = []
        start = max(PERIOD-1,len(rows)-25)
        for i in range(start+1,len(rows)):
            if rows[i][0]-rows[i-1][0] != timedelta(hours=1):
                start = i
        for i in range(start,len(rows)):
            m,u,l = _band(prices[i-PERIOD+1:i+1])
            if not u > l:
                return {**base, "reason": "zero_bandwidth"}
            snapshots.append((rows[i][0],prices[i],m,u,l,u-l))
        old, prev, last = snapshots[-7], snapshots[-4], snapshots[-1]
        slopes = [(last[i]-prev[i])/PIP/3 for i in (2,3,4,5)]
        past = [(prev[i]-old[i])/PIP/3 for i in (2,3,4,5)]
        accel = [(a-b)/3 for a,b in zip(slopes,past)]
        ms,us,ls,ws = slopes
        width_change = (last[5]/prev[5]-1)*100
        prior_growth = (prev[5]/old[5]-1)*100
        tolerance = max(.35, last[5]/PIP*.0075)
        expanding = us > tolerance and ls < -tolerance and width_change >= 5
        compressing = us < -tolerance and ls > tolerance and width_change <= -5
        expansion_ends=[]
        for a,b in zip(snapshots[:-6], snapshots[3:-3]):
            tol=max(.35,b[5]/PIP*.0075)
            if ((b[3]-a[3])/PIP/3>tol and (b[4]-a[4])/PIP/3<-tol
                    and b[5]/a[5]>=1.05):
                expansion_ends.append(b[0])
        prior_expansion=bool(expansion_ends)
        q = (last[1]-last[4])/last[5]
        # Exhaustion is a recent expansion followed by contraction and
        # confirmed recovery away from the prior favorable outer edge.
        recent = [r for r in snapshots[:-1]
                  if expansion_ends and r[0]>=expansion_ends[-1]-timedelta(hours=3)]
        lower_edge = [r for r in recent if (r[1]-r[4])/r[5] <= .15]
        upper_edge = [r for r in recent if (r[1]-r[4])/r[5] >= .85]
        exhausted = None
        anchor = None
        if compressing and prior_expansion:
            if lower_edge:
                r=min(lower_edge,key=lambda x:x[1])
                if q >= .15 and (last[1]-r[1])/PIP >= 4 and last[1]>snapshots[-2][1]:
                    exhausted,anchor="SHORT",r
            if upper_edge:
                r=max(upper_edge,key=lambda x:x[1])
                if q <= .85 and (r[1]-last[1])/PIP >= 4 and last[1]<snapshots[-2][1]:
                    exhausted,anchor="LONG",r
        regime = ("EXHAUSTION" if exhausted else "EXPANSION" if expanding
                  else "COMPRESSION" if compressing
                  else "TREND_UP" if min(ms,us,ls)>tolerance
                  else "TREND_DOWN" if max(ms,us,ls)<-tolerance
                  else "CONSOLIDATION" if max(abs(ms),abs(us),abs(ls))<=tolerance
                  else "TRANSITION")
        return {**base, "available": True, "regime": regime,
                "asof": last[0].isoformat(), "percent_b": round(q,5),
                "middle": last[2], "upper":last[3], "lower":last[4],
                "width_pips":round(last[5]/PIP,3),
                "bandwidth_pct_change_3h":round(width_change,3),
                "previous_bandwidth_pct_change_3h":round(prior_growth,3),
                "slope_pips_per_h":dict(zip(("ma30","upper","lower","bandwidth"),map(lambda x:round(x,4),slopes))),
                "acceleration_pips_per_h2":dict(zip(("ma30","upper","lower","bandwidth"),map(lambda x:round(x,4),accel))),
                "prior_expansion":prior_expansion,
                "last_expansion_at":expansion_ends[-1].isoformat() if expansion_ends else None,
                "exhausted_direction":exhausted,
                "excursion_at":anchor[0].isoformat() if anchor else None,
                "excursion_price":anchor[1] if anchor else None,
                "last_h1_close":last[1], "reason":"verified_completed_h1_geometry"}
    except (TypeError,ValueError,AttributeError,OverflowError,ArithmeticError):
        return {**base,"reason":"invalid_dynamics_input"}


def dynamics_entry_conflict(dynamics, direction):
    """Pause chasing an exhausted leg near its original outer edge only.
    A recovered price toward MA30/opposite edge is not vetoed here.
    """
    if not dynamics.get("available") or dynamics.get("regime") != "EXHAUSTION":
        return False
    side=str(direction).upper()
    q=dynamics.get("percent_b")
    return (side==dynamics.get("exhausted_direction") and isinstance(q,(int,float))
            and (q<=.35 if side=="SHORT" else q>=.65 if side=="LONG" else False))


def dynamics_profit_review(dynamics, direction, opened_at, current_mid, observed_at,
                           *, profit_pips, risk_pips, minimum_pips):
    """Additional profit-only reduction. No change to hard risk or direction.
    Require post-entry expansion excursion, verified contraction, current
    price still consistent with the H1 recovery and positive material profit.
    """
    result={"close":False,"reason":"no_verified_dynamics_exit",
            "direction_authority":False,"execution_authority":False}
    if not dynamics.get("available") or dynamics.get("regime")!="EXHAUSTION":
        return result
    try:
        side=str(direction).upper()
        anchor=datetime.fromisoformat(dynamics["excursion_at"])
        asof=datetime.fromisoformat(dynamics["asof"])
        if (side!=dynamics.get("exhausted_direction") or opened_at.tzinfo is None
                or observed_at.tzinfo is None or not opened_at<anchor<=asof<=observed_at
                or observed_at-asof>timedelta(minutes=90)
                or not all(isfinite(v) for v in (profit_pips,risk_pips,current_mid))
                or risk_pips<=0 or profit_pips<max(minimum_pips,.35*risk_pips)):
            return result
        # Do not exit on a historical H1 recovery that has already reversed.
        sign=1 if side=="SHORT" else -1
        recovery=sign*(current_mid-float(dynamics["excursion_price"]))/PIP
        retained=sign*(current_mid-float(dynamics["last_h1_close"]))/PIP
        if recovery<4 or retained < -2:
            return result
        return {**result,"close":True,"reason":"post_expansion_compression_profit_exit",
                "recovery_pips":round(recovery,3),"profit_pips":round(profit_pips,3),
                "requested_reduction_fraction":1.0,"partial_execution_supported":False,
                "trend_reversal_required":False}
    except (KeyError,TypeError,ValueError,ArithmeticError,AttributeError):
        return result
