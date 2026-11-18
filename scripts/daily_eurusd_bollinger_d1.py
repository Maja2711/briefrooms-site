"""Auxiliary Daily EUR/USD Bollinger (30, 2.5) trend context.

Observational only: no direction, admission veto, execution, or exit authority.
Only completed D1 bars are used; incomplete/latest session is excluded.
"""
from __future__ import annotations
from datetime import timedelta, timezone
from math import sqrt, isfinite

PERIOD = 30
MULTIPLIER = 2.5

def evaluate(bars, observed_at):
    observed_at = observed_at.astimezone(timezone.utc)
    completed = sorted((b for b in bars if b.timestamp.astimezone(timezone.utc) + timedelta(days=1) <= observed_at),
                       key=lambda b: b.timestamp)
    if len(completed) < PERIOD + 2:
        return {"available": False, "reason": "insufficient_completed_d1_bars", "role": "AUXILIARY_TECHNICAL"}
    closes = [float(b.close) for b in completed]
    if not all(isfinite(v) and v > 0 for v in closes):
        return {"available": False, "reason": "invalid_d1_prices", "role": "AUXILIARY_TECHNICAL"}
    def bands(end):
        window = closes[end-PERIOD:end]
        middle = sum(window) / PERIOD
        sigma = sqrt(sum((p-middle)**2 for p in window)/PERIOD)
        return middle, middle+MULTIPLIER*sigma, middle-MULTIPLIER*sigma
    middle, upper, lower = bands(len(closes))
    previous_middle, _, _ = bands(len(closes)-1)
    price, previous = closes[-1], closes[-2]
    # Raw completed D1 close-to-close movement is independent of
    # Bollinger-band geometry and can disagree with the D1 band trend.
    raw_d1_price_action = {
        "source": "COMPLETED_D1_RAW_CLOSES",
        "change_5_completed_intervals_pips": round((closes[-1] - closes[-6]) / .0001, 3),
        "up_intervals_5d": sum(closes[i] > closes[i-1]
                              for i in range(len(closes)-5, len(closes))),
        "down_intervals_5d": sum(closes[i] < closes[i-1]
                                for i in range(len(closes)-5, len(closes))),
    }
    width = upper-lower
    if width <= 0:
        return {"available": False, "reason": "zero_bandwidth", "role": "AUXILIARY_TECHNICAL"}
    slope = middle-previous_middle
    cross_up = previous <= previous_middle and price > middle
    cross_down = previous >= previous_middle and price < middle
    trend = "UP" if price > middle and slope > 0 else "DOWN" if price < middle and slope < 0 else "MIXED"
    return {
        "available": True, "role": "AUXILIARY_TECHNICAL",
        "direction_mutation_allowed": False, "admission_veto_allowed": False,
        "period": PERIOD, "deviations": MULTIPLIER,
        "last_completed_d1": completed[-1].timestamp.astimezone(timezone.utc).isoformat(),
        "close": price, "middle": middle, "upper": upper, "lower": lower,
        "percent_b": (price-lower)/width, "middle_slope": slope,
        "trend": trend, "cross_above_middle": cross_up, "cross_below_middle": cross_down,
        "near_upper_band": (upper-price)/width <= 0.1,
        "near_lower_band": (price-lower)/width <= 0.1,
        "raw_d1_price_action": raw_d1_price_action,
    }
