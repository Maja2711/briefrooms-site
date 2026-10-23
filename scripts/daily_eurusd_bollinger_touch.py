"""Prospective EURUSD Daily D1 band-touch cooldown.

Belief remains the exclusive LONG/SHORT/FLAT authority. This stateful gate only
vetoes same-direction entry after a D1 outer-band touch until a verified
50% pullback toward the *currently recomputed* D1 middle band. It never
creates countertrend positions or changes an open position.

Each invocation consumes completed 1-minute observations exactly once and
returns a JSON-serializable state for the next Daily monitor cycle.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Mapping

RETRACE_FRACTION = 0.5
SCHEMA_VERSION = "eurusd-d1-bollinger-touch-v1"


def _utc(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("D1 touch requires timezone-aware market timestamps")
    return value.astimezone(timezone.utc)


def update(
    d1_context: Mapping[str, Any],
    minute_bars: Any,
    observed_at: datetime,
    *,
    previous_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Track touches and corrections without backdated fills or direction votes.

    At each completed D1 update, upper/middle/lower and the 50%-correction
    threshold are recalculated. Only the touch event and progress watermark
    are persisted; historical band levels do not control future eligibility.
    """
    observed_at = _utc(observed_at)
    previous = previous_state if isinstance(previous_state, Mapping) else {}
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "upper": {"blocked": False},
        "lower": {"blocked": False},
        "last_processed_at": None,
    }
    if previous.get("schema_version") == SCHEMA_VERSION:
        for side in ("upper", "lower"):
            old = previous.get(side)
            if isinstance(old, Mapping):
                state[side] = {
                    "blocked": old.get("blocked") is True,
                    "touched_at": old.get("touched_at"),
                    "touched_price": old.get("touched_price"),
                }
        stamp = previous.get("last_processed_at")
        if isinstance(stamp, str):
            _utc(stamp)
            state["last_processed_at"] = stamp

    result: dict[str, Any] = {
        "role": "D1_TOUCH_RETRACE_ENTRY_VETO",
        "direction_mutation_allowed": False,
        "opens_trade": False,
        "requires_belief_recheck": True,
        "fraction": RETRACE_FRACTION,
    }
    if d1_context.get("available") is True:
        upper, middle, lower = (
            float(d1_context[key]) for key in ("upper", "middle", "lower")
        )
        if not (all(isfinite(x) and x > 0 for x in (upper, middle, lower))
                and lower < middle < upper):
            raise ValueError("invalid D1 bands for touch cooldown")
        long_level = upper - RETRACE_FRACTION * (upper - middle)
        short_level = lower + RETRACE_FRACTION * (middle - lower)
        last_completed = _utc(d1_context["last_completed_d1"])
        # Only intraday candles after the latest completed D1 bar are scanned.
        # Previous cycles supply the watermark, so a processed touch is never
        # reactivated by replayed Yahoo 1-minute history.
        day_start = last_completed + timedelta(days=1)
        watermark = (_utc(state["last_processed_at"])
                     if state["last_processed_at"] else None)
        candles: list[tuple[datetime, float, float, float]] = []
        for bar in minute_bars or []:
            stamp = _utc(bar.timestamp)
            if (stamp < day_start or stamp + timedelta(minutes=1) > observed_at
                    or (watermark is not None and stamp <= watermark)):
                continue
            high, low, close = (
                float(getattr(bar, "high")), float(getattr(bar, "low")),
                float(getattr(bar, "close"))
            )
            if not all(isfinite(x) and x > 0 for x in (high, low, close)):
                raise ValueError("invalid completed 1m candle for D1 touch")
            if low > high or not low <= close <= high:
                raise ValueError("inconsistent completed 1m candle for D1 touch")
            candles.append((stamp, high, low, close))

        for stamp, high, low, close in sorted(candles):
            when = stamp.isoformat()
            hit_upper = high >= upper
            hit_lower = low <= lower
            if hit_upper:
                if not state["upper"]["blocked"]:
                    state["upper"] = {"blocked": True, "touched_at": when,
                                      "touched_price": high}
            elif state["upper"]["blocked"] and close <= long_level:
                state["upper"] = {"blocked": False, "touched_at": state["upper"].get("touched_at"),
                                  "touched_price": state["upper"].get("touched_price"),
                                  "released_at": when}
            if hit_lower:
                if not state["lower"]["blocked"]:
                    state["lower"] = {"blocked": True, "touched_at": when,
                                      "touched_price": low}
            elif state["lower"]["blocked"] and close >= short_level:
                state["lower"] = {"blocked": False, "touched_at": state["lower"].get("touched_at"),
                                  "touched_price": state["lower"].get("touched_price"),
                                  "released_at": when}
            state["last_processed_at"] = when
        result.update(available=True, upper=upper, middle=middle, lower=lower,
                      long_release_price=long_level,
                      short_release_price=short_level,
                      last_completed_d1=last_completed.isoformat())
    else:
        # Preserve an active veto through a temporary D1-feed outage.
        # Never infer a new band touch or an all-clear from missing evidence.
        result.update(available=False, reason=str(d1_context.get("reason") or "d1_unavailable"),
                      long_release_price=None, short_release_price=None)
    result.update(
        upper_long_blocked=state["upper"]["blocked"],
        lower_short_blocked=state["lower"]["blocked"],
        state=state,
    )
    return result
