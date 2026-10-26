"""Prospective EUR/USD Daily-only Belief flip and cross-direction whipsaw vetoes.

These gates consume the immutable, already-settled trade history and the
authoritative new Belief decision. They DO NOT create directions, mutate Belief,
retrofit past signals, close positions, or grant execution authority.
Confirmation state is saved in the canonical Daily output metadata; source
provenance (hash + source clock) must genuinely advance between observations.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Mapping

FLIP_LOOKBACK_HOURS = 12
WHIPSAW_AFTER_LOSS_HOURS = 6
CONFIRM_MINUTES = 40
STRONG_CONFIRM_MINUTES = 20
MAX_SOURCE_AGE_MINUTES = 90
STRONG_LONG_SCORE = 56.0
STRONG_SHORT_SCORE = 44.0
STRONG_MIN_CONFIDENCE = 0.36
ANCHOR = "eurusd.trend.bullish"


def _time(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def last_closed_trade(history: Mapping[str, Any] | None, now: datetime) -> dict | None:
    """Return latest actual observed exit, never a future or open position."""
    choices = []
    for trade in (history or {}).get("trades") or []:
        if not isinstance(trade, Mapping):
            continue
        direction = str(trade.get("direction") or "").upper()
        ended = _time(trade.get("closed_at"))
        if direction not in {"LONG", "SHORT"} or ended is None or ended > now:
            continue
        try:
            r_value = float(trade["r_multiple"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isfinite(r_value):
            continue
        choices.append((ended, str(trade.get("trade_id") or ""), dict(trade)))
    return max(choices, key=lambda item: (item[0], item[1]))[2] if choices else None


def _authorized_decision(candidate: Mapping[str, Any], now: datetime) -> dict:
    final = ((candidate.get("metadata") or {}).get("final_decision") or {})
    consumer = final.get("epistemic_consumer") or {}
    direction = str(candidate.get("direction") or "").upper()
    try:
        score = float(candidate.get("score"))
        confidence = float(candidate.get("confidence"))
    except (ValueError, TypeError):
        return {"available": False, "reason": "invalid_belief_numbers"}
    if not (isfinite(score) and isfinite(confidence) and 0 <= score <= 100
            and 0 <= confidence <= 1):
        return {"available": False, "reason": "invalid_belief_numbers"}
    if (final.get("decision_source") != "NATIVE_BELIEF_FIRST"
            or final.get("direction") != direction
            or final.get("epistemic_aggregate_authoritative") is not True
            or consumer.get("available") is not True
            or consumer.get("aggregate_authoritative") is not True
            or consumer.get("contract") != "epistemic-consumer-interface-v1"):
        return {"available": False, "reason": "non_authoritative_flip_decision"}
    if abs(float(final.get("score", -100)) - score) > 1e-6:
        return {"available": False, "reason": "score_not_belief_authoritative"}
    if abs(float(final.get("confidence", -100)) - confidence) > 1e-6:
        return {"available": False, "reason": "confidence_not_belief_authoritative"}
    source_at = _time(consumer.get("source_created_at"))
    fingerprint = str(consumer.get("source_sha256") or "")
    if source_at is None or len(fingerprint) < 16:
        return {"available": False, "reason": "flip_provenance_unavailable"}
    if not 0 <= (now - source_at).total_seconds() <= MAX_SOURCE_AGE_MINUTES * 60:
        return {"available": False, "reason": "flip_source_stale_or_future"}
    supports = final.get("used_beliefs") or []
    anchor = next((row for row in supports if isinstance(row, Mapping)
                   and row.get("belief_id") == ANCHOR), None)
    if not anchor:
        return {"available": False, "reason": "missing_trend_anchor"}
    try:
        anchor_prob = float(anchor["probability"])
    except (TypeError, ValueError, KeyError):
        return {"available": False, "reason": "invalid_trend_anchor"}
    if not isfinite(anchor_prob) or not (
            anchor_prob <= 0.48 if direction == "SHORT" else anchor_prob >= 0.52):
        return {"available": False, "reason": "trend_anchor_not_aligned"}
    strong_score = score <= STRONG_SHORT_SCORE if direction == "SHORT" else score >= STRONG_LONG_SCORE
    return {
        "available": True, "reason": "authoritative_fresh",
        "score": score, "confidence": confidence,
        "source_created_at": _iso(source_at), "source_sha256": fingerprint,
        "strong_belief": bool(strong_score and confidence >= STRONG_MIN_CONFIDENCE),
    }


def flip_confirmation(
    candidate: Mapping[str, Any], history: Mapping[str, Any] | None,
    observed_at: datetime, previous_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A fresh second Belief snapshot after a sustained interval is mandatory.

    Applies only if the most recently CLOSED Daily trade was in the opposite
    direction during the lookback; no historical trade gets a synthetic re-vote.
    """
    now = observed_at.astimezone(timezone.utc)
    direction = str(candidate.get("direction") or "").upper()
    latest = last_closed_trade(history, now)
    if latest is None or direction not in {"LONG", "SHORT"}:
        return {"allowed": True, "required": False, "reason": "no_recent_opposite_trade",
                "state": None}
    closed = _time(latest["closed_at"])
    assert closed is not None
    age = (now - closed).total_seconds()
    if direction == str(latest["direction"]).upper() or age > FLIP_LOOKBACK_HOURS * 3600:
        return {"allowed": True, "required": False, "reason": "no_recent_opposite_trade",
                "state": None}
    trade_id = str(latest.get("trade_id") or "")
    base = {"required": True, "opposite_trade_id": trade_id,
            "opposite_direction": str(latest["direction"]).upper(),
            "minutes_since_exit": round(age / 60, 2),
            "direction_mutation_allowed": False, "entry_authority": False}
    belief = _authorized_decision(candidate, now)
    if not belief.get("available"):
        return {**base, "allowed": False, "reason": belief["reason"], "state": None}
    strong = belief["strong_belief"]
    minimum = STRONG_CONFIRM_MINUTES if strong else CONFIRM_MINUTES
    previous = dict(previous_state or {})
    stable = (previous.get("trade_id") == trade_id
              and previous.get("direction") == direction)
    first = _time(previous.get("first_seen_at")) if stable else None
    first_source = _time(previous.get("first_source_created_at")) if stable else None
    if (first is None or first_source is None or first > now
            or first < closed or first_source > now):
        first = now
        first_source = _time(belief["source_created_at"])
        previous = {}
    current = {
        "schema_version": "eurusd-daily-belief-flip-confirmation-v1",
        "trade_id": trade_id, "direction": direction,
        "first_seen_at": _iso(first),
        "first_source_created_at": _iso(first_source),
        "first_source_sha256": previous.get("first_source_sha256") or belief["source_sha256"],
        "first_score": previous.get("first_score", belief["score"]),
        "last_observed_at": _iso(now),
        "last_source_sha256": belief["source_sha256"],
        "last_source_created_at": belief["source_created_at"],
        "confirmed": False,
    }
    elapsed = (now - first).total_seconds() / 60
    distinct = (belief["source_sha256"] != current["first_source_sha256"]
                and _time(belief["source_created_at"]) > first_source)
    if stable and previous.get("confirmed") is True:
        # Previously proved confirmation remains valid only with a currently
        # fresh and aligned authoritative state; never depends on stale evidence.
        current["confirmed"] = True
        return {**base, "allowed": True, "reason": "previously_confirmed_fresh",
                "state": current, "elapsed_minutes": round(elapsed, 2),
                "required_minutes": minimum, "strong_belief": strong}
    confirmed = bool(elapsed >= minimum and distinct)
    current["confirmed"] = confirmed
    return {**base, "allowed": confirmed,
            "reason": "flip_confirmed" if confirmed else "flip_requires_new_sustained_belief",
            "state": current, "elapsed_minutes": round(elapsed, 2),
            "required_minutes": minimum, "new_source_verified": distinct,
            "strong_belief": strong}


def cross_direction_whipsaw(
    candidate: Mapping[str, Any], history: Mapping[str, Any] | None,
    observed_at: datetime, h1: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """A recent loss of the OPPOSITE side must not trigger range whipsaws.

    A strong Belief PLUS verified H1 directional structure may pass; this gate
    never reclassifies a flat/range market into a trend by itself.
    """
    now = observed_at.astimezone(timezone.utc)
    direction = str(candidate.get("direction") or "").upper()
    trade = last_closed_trade(history, now)
    base = {"required": False, "allowed": True,
            "reason": "no_recent_opposite_loss", "direction_mutation_allowed": False}
    if trade is None or direction not in {"LONG", "SHORT"}:
        return base
    closed = _time(trade["closed_at"])
    assert closed is not None
    if (direction == str(trade["direction"]).upper()
            or float(trade["r_multiple"]) >= 0
            or (now - closed).total_seconds() > WHIPSAW_AFTER_LOSS_HOURS * 3600):
        return base
    base.update(required=True, opposite_loss_trade_id=str(trade.get("trade_id") or ""))
    technical = h1 or {}
    if technical.get("available") is not True:
        return {**base, "allowed": False, "reason": "opposite_loss_h1_unavailable"}
    if (technical.get("range_regime") is True
            or technical.get("marginal_crossing") is True
            or technical.get("regime") == "RANGE"):
        return {**base, "allowed": False, "reason": "opposite_loss_h1_range_whipsaw"}
    if technical.get("allowed") is not True or technical.get("aligned") is not True:
        return {**base, "allowed": False, "reason": "opposite_loss_h1_not_aligned"}
    belief = _authorized_decision(candidate, now)
    if not belief.get("available"):
        return {**base, "allowed": False, "reason": belief["reason"]}
    if not belief["strong_belief"] or technical.get("strong_trend_confirmed") is not True:
        return {**base, "allowed": False,
                "reason": "opposite_loss_requires_strong_belief_and_h1_trend"}
    return {**base, "allowed": True,
            "reason": "opposite_loss_strong_reversal_confirmed",
            "score": belief["score"], "confidence": belief["confidence"]}
