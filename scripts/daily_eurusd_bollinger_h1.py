"""EUR/USD Daily-only H1 Bollinger admission; Belief remains direction owner.

Build closed H1 candles only from pairs of completed 30-minute candles.
A marginal crossing of a flat middle band is NOT confirmation of a trend.
This module only vetoes new EUR/USD Daily entries; it never manufactures or
reverses a Belief decision and never has execution or exit authority.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from math import isfinite, sqrt

PERIOD = 30
MULTIPLIER = 2.5
MAX_AGE_MINUTES = 90
PIP = 0.0001
# Predeclared admission thresholds; sensitivity should be evaluated on actual
# forward records and never retrofitted to one losing transaction.
MIDDLE_NOISE_PIPS = 1.5
FLAT_MIDDLE_SLOPE_PIPS = 1.2
FLAT_THREE_HOUR_SLOPE_PIPS = 4.5
NEAR_MIDDLE_WIDTH_FRACTION = 0.12
OSCILLATION_LOOKBACK_HOURS = 7
MIN_MIDDLE_CROSSINGS = 2


def evaluate(bars, direction, observed_at):
    if direction not in ("LONG", "SHORT"):
        return {"available": False, "allowed": False, "reason": "belief_not_directional",
                "weight": "PRIMARY_TECHNICAL"}
    observed_at = observed_at.astimezone(timezone.utc)
    buckets = {}
    for bar in bars:
        t = bar.timestamp.astimezone(timezone.utc)
        # The adapter timestamps bars at OPEN, not at candle close.
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
        candles.append((hour + timedelta(hours=1), float(first.open),
                        float(max(first.high, last.high)),
                        float(min(first.low, last.low)), float(last.close)))
    if len(candles) < PERIOD + 3:
        return {"available": False, "allowed": False,
                "reason": "insufficient_closed_h1_bars", "closed_h1": len(candles)}
    age_minutes = (observed_at - candles[-1][0]).total_seconds() / 60
    if age_minutes < 0 or age_minutes > MAX_AGE_MINUTES:
        return {"available": False, "allowed": False,
                "reason": "stale_h1_bollinger",
                "closed_h1": len(candles),
                "last_closed_h1": candles[-1][0].isoformat(),
                "age_minutes": round(age_minutes, 2)}
    closes = [row[4] for row in candles]

    def band(index):
        values = closes[index - PERIOD + 1:index + 1]
        mean = sum(values) / PERIOD
        sd = sqrt(sum((v - mean) ** 2 for v in values) / PERIOD)
        return mean, mean + MULTIPLIER * sd, mean - MULTIPLIER * sd

    mid, upper, lower = band(len(closes) - 1)
    prev_mid, _, _ = band(len(closes) - 2)
    prev2_mid, _, _ = band(len(closes) - 3)
    prev3_mid, _, _ = band(len(closes) - 4)
    price, previous = closes[-1], closes[-2]
    width = upper - lower
    if width <= 1e-10 or not all(isfinite(x) for x in
                                (mid, upper, lower, price, previous, prev_mid, prev3_mid)):
        return {"available": False, "allowed": False,
                "reason": "invalid_bollinger_geometry"}
    percent_b = (price - lower) / width
    slope = mid - prev_mid
    slope_prior = prev_mid - prev2_mid

    # Only CLOSED hourly evidence participates. Count sign changes relative to
    # that hour's own historical middle band, not the latest middle in hindsight.
    start = max(PERIOD - 1, len(closes) - OSCILLATION_LOOKBACK_HOURS)
    side = []
    for index in range(start, len(closes)):
        historical_mid, _, _ = band(index)
        distance = closes[index] - historical_mid
        side.append(1 if distance > 1e-10 else -1 if distance < -1e-10 else 0)
    crossings = sum(a != b for a, b in zip(side, side[1:]) if a and b)
    middle_gap_pips = (price - mid) / PIP
    width_pips = width / PIP
    slope_pips = slope / PIP
    slope_3h_pips = (mid - prev3_mid) / PIP
    near_middle = abs(middle_gap_pips) <= max(
        MIDDLE_NOISE_PIPS, width_pips * NEAR_MIDDLE_WIDTH_FRACTION)
    flat_middle = (abs(slope_pips) <= FLAT_MIDDLE_SLOPE_PIPS
                   and abs(slope_3h_pips) <= FLAT_THREE_HOUR_SLOPE_PIPS)
    oscillating = crossings >= MIN_MIDDLE_CROSSINGS
    range_regime = bool(
        (near_middle and flat_middle)
        or (oscillating and abs(middle_gap_pips) <= 0.20 * width_pips
            and abs(slope_3h_pips) <= max(FLAT_THREE_HOUR_SLOPE_PIPS,
                                         0.12 * width_pips))
    )
    # A crossing smaller than 1.5 pip is microstructure noise even before a
    # regime is confidently classified. This prevents today's ~0.135-pip case.
    marginal_crossing = (abs(middle_gap_pips) <= MIDDLE_NOISE_PIPS
                         and abs(slope_pips) <= 2.0)

    trend_long = price > mid and previous > prev_mid and slope > 0 and slope_prior > 0
    trend_short = price < mid and previous < prev_mid and slope < 0 and slope_prior < 0
    upper_rejection = previous >= upper and price < previous
    lower_rejection = previous <= lower and price > previous
    aligned = trend_long if direction == "LONG" else trend_short
    exhaustion = upper_rejection if direction == "LONG" else lower_rejection
    allowed = bool(aligned and not exhaustion and not range_regime and not marginal_crossing)
    strong_trend_confirmed = bool(
        allowed and abs(middle_gap_pips) >= 5.0
        and abs(slope_pips) >= 1.0 and abs(slope_3h_pips) >= 2.0
    )
    reason = ("h1_range_middle_whipsaw" if range_regime
              else "h1_middle_break_unconfirmed" if marginal_crossing
              else "outer_band_exhaustion" if exhaustion
              else "aligned_h1_trend" if aligned
              else "h1_trend_not_confirmed")

    return {
        "available": True, "allowed": allowed, "reason": reason,
        "role": "PRIMARY_TECHNICAL_ADMISSION",
        "direction_mutation_allowed": False, "weight": "HIGH_VETO_NEW_ENTRY",
        "period": PERIOD, "deviations": MULTIPLIER,
        "last_closed_h1": candles[-1][0].isoformat(), "closed_h1": len(candles),
        "age_minutes": round(age_minutes, 2), "close": price,
        "middle": mid, "upper": upper, "lower": lower, "percent_b": percent_b,
        "middle_slope": slope, "aligned": aligned, "exhaustion": exhaustion,
        "regime": "RANGE" if range_regime else "TREND" if allowed else "TRANSITION",
        "range_regime": range_regime, "near_middle": near_middle,
        "flat_middle": flat_middle, "marginal_crossing": marginal_crossing,
        "middle_crossings_6h": crossings,
        "middle_gap_pips": round(middle_gap_pips, 3),
        "middle_slope_pips_per_h": round(slope_pips, 3),
        "middle_slope_3h_pips": round(slope_3h_pips, 3),
        "width_pips": round(width_pips, 2),
        "strong_trend_confirmed": strong_trend_confirmed,
    }
