"""EUR/USD Daily-only H1 Bollinger technical admission (Belief direction remains authoritative).

Uses only fully closed pairs of 30-minute EUR/USD bars. Never creates or reverses
direction; missing or incomplete H1 history fails closed for *new entries*.
"""
from __future__ import annotations
from datetime import timedelta, timezone
from math import sqrt, isfinite

PERIOD = 30
MULTIPLIER = 2.5
MAX_AGE_MINUTES = 90

def evaluate(bars, direction, observed_at):
    if direction not in ("LONG", "SHORT"):
        return {"allowed": False, "reason": "belief_not_directional", "weight": "PRIMARY_TECHNICAL"}
    observed_at = observed_at.astimezone(timezone.utc)
    buckets = {}
    for bar in bars:
        t = bar.timestamp.astimezone(timezone.utc)
        # The market adapter timestamps bars at opening time.
        if t + timedelta(minutes=30) > observed_at:
            continue
        hour = t.replace(minute=0, second=0, microsecond=0)
        if t.minute not in (0, 30) or t.second or t.microsecond:
            continue
        buckets.setdefault(hour, {})[t.minute] = bar
    candles = []
    for hour, halves in sorted(buckets.items()):
        if set(halves) != {0, 30} or hour + timedelta(hours=1) > observed_at:
            continue
        first, last = halves[0], halves[30]
        candles.append((hour + timedelta(hours=1), float(first.open), float(max(first.high, last.high)),
                        float(min(first.low, last.low)), float(last.close)))
    if len(candles) < PERIOD + 3:
        return {"allowed": False, "reason": "insufficient_closed_h1_bars", "closed_h1": len(candles)}
    if observed_at - candles[-1][0] > timedelta(minutes=MAX_AGE_MINUTES):
        return {"allowed": False, "reason": "stale_h1_bollinger"}
    closes = [x[4] for x in candles]
    def band(index):
        values = closes[index-PERIOD+1:index+1]
        mean = sum(values) / PERIOD
        sd = sqrt(sum((v-mean)**2 for v in values) / PERIOD)
        return mean, mean + MULTIPLIER*sd, mean - MULTIPLIER*sd
    mid, upper, lower = band(len(closes)-1)
    prev_mid, _, _ = band(len(closes)-2)
    prev2_mid, _, _ = band(len(closes)-3)
    price, previous = closes[-1], closes[-2]
    width = upper - lower
    if width <= 0 or not all(isfinite(x) for x in (mid, upper, lower, price)):
        return {"allowed": False, "reason": "invalid_bollinger_geometry"}
    percent_b = (price-lower)/width
    slope = mid-prev_mid
    slope_prior = prev_mid-prev2_mid
    # Confirmed trend requires current and preceding middle-band slope.
    trend_long = price > mid and previous > prev_mid and slope > 0 and slope_prior > 0
    trend_short = price < mid and previous < prev_mid and slope < 0 and slope_prior < 0
    # Strong rejection at an outer band: price returns inside after previous excursion.
    upper_rejection = previous >= upper and price < previous
    lower_rejection = previous <= lower and price > previous
    aligned = trend_long if direction == "LONG" else trend_short
    exhaustion = upper_rejection if direction == "LONG" else lower_rejection
    allowed = aligned and not exhaustion
    return {
        "allowed": allowed, "reason": "aligned_h1_trend" if allowed else
            "outer_band_exhaustion" if exhaustion else "h1_trend_not_confirmed",
        "role": "PRIMARY_TECHNICAL_ADMISSION", "direction_mutation_allowed": False,
        "weight": "HIGH_VETO_NEW_ENTRY", "period": PERIOD, "deviations": MULTIPLIER,
        "last_closed_h1": candles[-1][0].isoformat(), "close": price,
        "middle": mid, "upper": upper, "lower": lower, "percent_b": percent_b,
        "middle_slope": slope, "aligned": aligned, "exhaustion": exhaustion,
    }
