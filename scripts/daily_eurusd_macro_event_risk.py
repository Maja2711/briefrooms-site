from __future__ import annotations

"""Event-aware open-position risk overlay for Daily EUR/USD.

This layer has one narrow purpose: protect an already-open position around
high-impact macro events. It never creates or reverses a position. Direction
ownership remains with the Daily EUR/USD engine.
"""

from datetime import datetime
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar
import daily_eurusd_lifecycle as lifecycle

PRE_EVENT_WINDOW_HOURS = 20.0 / 60.0
POST_EVENT_WINDOW_HOURS = 1.0

PRE_EVENT_MIN_CURRENT_R = 0.50
PRE_EVENT_MIN_MFE_R = 0.50
PRE_EVENT_MAX_REWARD_TO_DOWNSIDE = 0.75

POST_EVENT_MIN_MFE_R = 0.40
POST_EVENT_MIN_GIVEBACK_R = 0.45
POST_EVENT_MAX_CURRENT_R = 0.25

BELIEF_CONFLICT_SCORE = 6.0


def _direction_sign(direction: str) -> float:
    return 1.0 if str(direction).upper() == "LONG" else -1.0


def _r_at_price(position: Mapping[str, Any], price: float) -> float:
    entry = float(position["entry"])
    stop = float(position["stop"])
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    return _direction_sign(str(position["direction"])) * (float(price) - entry) / risk


def _diagnostics(
    position: Mapping[str, Any],
    bars: Sequence[Bar],
    observed_at: datetime,
) -> dict[str, Any] | None:
    opened = lifecycle.parse_iso(str(position.get("opened_at") or ""))
    if opened is None:
        return None
    relevant = sorted(
        (bar for bar in bars if opened <= bar.timestamp <= observed_at),
        key=lambda bar: bar.timestamp,
    )
    if not relevant:
        return None

    direction = str(position["direction"]).upper()
    sign = _direction_sign(direction)
    entry = float(position["entry"])
    stop = float(position["stop"])
    target = float(position["target"])
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    current_r = _r_at_price(position, float(relevant[-1].close))
    target_r = sign * (target - entry) / risk
    if direction == "LONG":
        best = max(float(bar.high if bar.high is not None else bar.close) for bar in relevant)
    else:
        best = min(float(bar.low if bar.low is not None else bar.close) for bar in relevant)
    mfe_r = sign * (best - entry) / risk
    giveback_r = max(0.0, mfe_r - current_r)
    remaining_reward_r = max(0.0, target_r - current_r)
    downside_to_stop_r = max(0.0, current_r + 1.0)
    reward_to_downside = (
        remaining_reward_r / downside_to_stop_r
        if downside_to_stop_r > 1e-9
        else 0.0
    )
    return {
        "current_r": round(current_r, 4),
        "target_r": round(target_r, 4),
        "mfe_r": round(mfe_r, 4),
        "giveback_r": round(giveback_r, 4),
        "remaining_reward_r": round(remaining_reward_r, 4),
        "downside_to_stop_r": round(downside_to_stop_r, 4),
        "reward_to_downside": round(reward_to_downside, 4),
        "bar": relevant[-1],
    }


def _nearest_event(macro_context: Mapping[str, Any]) -> Mapping[str, Any] | None:
    calendar = macro_context.get("macro_calendar") if isinstance(macro_context.get("macro_calendar"), Mapping) else {}
    events = calendar.get("events") if isinstance(calendar.get("events"), list) else []
    rows = [row for row in events if isinstance(row, Mapping)]
    if not rows:
        return None
    def key(row: Mapping[str, Any]) -> float:
        try:
            return abs(float(row.get("hours_until")))
        except (TypeError, ValueError):
            return 9999.0
    return min(rows, key=key)


def _close(
    position: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    *,
    reason: str,
    event: Mapping[str, Any] | None,
    candidate_direction: str,
    macro_context: Mapping[str, Any],
) -> dict[str, Any]:
    bar = diagnostics["bar"]
    trade = lifecycle._close_record(
        position,
        exit_reason=reason,
        exit_price=lifecycle.execution_exit_price(position, float(bar.close)),
        exited_at=bar.timestamp,
        exit_bar=bar,
    )
    trade["macro_event_risk"] = {
        "policy": "DAILY_MACRO_EVENT_RISK_V1",
        "event": dict(event or {}),
        "candidate_direction": candidate_direction,
        "belief_available": bool(macro_context.get("available")),
        "belief_score": macro_context.get("score"),
        "current_r": diagnostics["current_r"],
        "mfe_r": diagnostics["mfe_r"],
        "giveback_r": diagnostics["giveback_r"],
        "remaining_reward_r": diagnostics["remaining_reward_r"],
        "downside_to_stop_r": diagnostics["downside_to_stop_r"],
        "reward_to_downside": diagnostics["reward_to_downside"],
    }
    return trade


def maybe_close_position(
    position: Mapping[str, Any],
    bars: Sequence[Bar],
    observed_at: datetime,
) -> dict[str, Any] | None:
    diagnostics = _diagnostics(position, bars, observed_at)
    if diagnostics is None:
        return None

    macro_context = (
        position.get("_belief_macro_context")
        if isinstance(position.get("_belief_macro_context"), Mapping)
        else {}
    )
    candidate = (
        position.get("_management_candidate")
        if isinstance(position.get("_management_candidate"), Mapping)
        else {}
    )
    candidate_direction = str(candidate.get("direction") or "FLAT").upper()
    position_direction = str(position.get("direction") or "").upper()

    # Fresh Belief Core macro conflict is an immediate thesis invalidation.
    if macro_context.get("available"):
        try:
            score = float(macro_context.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        conflict = (
            position_direction == "SHORT" and score >= BELIEF_CONFLICT_SCORE
        ) or (
            position_direction == "LONG" and score <= -BELIEF_CONFLICT_SCORE
        )
        if conflict:
            return _close(
                position,
                diagnostics,
                reason="BELIEF_MACRO_THESIS_INVALIDATION",
                event=_nearest_event(macro_context),
                candidate_direction=candidate_direction,
                macro_context=macro_context,
            )

    event = _nearest_event(macro_context)
    if event is None:
        return None
    try:
        hours_until = float(event.get("hours_until"))
    except (TypeError, ValueError):
        return None

    # Before NFP/CPI/PCE/FOMC-type high-impact releases, protect a substantial
    # existing gain when remaining upside to TP is poor relative to the distance
    # back to the hard stop. The event calendar is risk information, not a
    # directional forecast.
    if 0.0 <= hours_until <= PRE_EVENT_WINDOW_HOURS:
        if (
            float(diagnostics["current_r"]) >= PRE_EVENT_MIN_CURRENT_R
            and float(diagnostics["mfe_r"]) >= PRE_EVENT_MIN_MFE_R
            and float(diagnostics["reward_to_downside"]) <= PRE_EVENT_MAX_REWARD_TO_DOWNSIDE
        ):
            return _close(
                position,
                diagnostics,
                reason="MACRO_EVENT_ASYMMETRY_EXIT",
                event=event,
                candidate_direction=candidate_direction,
                macro_context=macro_context,
            )

    # After the release, a large giveback plus loss of directional support is
    # enough to invalidate the position. No minimum position age is allowed here.
    if -POST_EVENT_WINDOW_HOURS <= hours_until < 0.0:
        direction_lost = candidate_direction != position_direction
        if (
            direction_lost
            and float(diagnostics["mfe_r"]) >= POST_EVENT_MIN_MFE_R
            and float(diagnostics["giveback_r"]) >= POST_EVENT_MIN_GIVEBACK_R
            and float(diagnostics["current_r"]) <= POST_EVENT_MAX_CURRENT_R
        ):
            return _close(
                position,
                diagnostics,
                reason="MACRO_EVENT_THESIS_INVALIDATION",
                event=event,
                candidate_direction=candidate_direction,
                macro_context=macro_context,
            )

    return None
