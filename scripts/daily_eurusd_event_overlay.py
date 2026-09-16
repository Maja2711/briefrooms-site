#!/usr/bin/env python3
"""Production Event Intelligence adapter for Daily EUR/USD.

Daily Trading consumes the already curated shared Event Intelligence snapshot. It does
not refetch news. Missing/stale event state is fail-neutral: technical/lifecycle and
ECB policy remain authoritative, while a fresh high-confidence event can veto a new
entry or close a position through the existing Daily trade-record contract.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from belief_market_data_adapter import Bar
import daily_eurusd_lifecycle as lifecycle
import investment_event_engine_profiles as profiles
import investment_event_intelligence as event

UTC = timezone.utc
MAX_SNAPSHOT_AGE_MINUTES = 40.0
MAX_FUTURE_SKEW_MINUTES = 15.0


def _load_snapshot(path: Path = event.OUTPUT_PATH) -> dict[str, Any]:
    payload = event._load(path, {})
    return payload if isinstance(payload, dict) else {}


def build_context(observed_at: datetime, path: Path = event.OUTPUT_PATH) -> dict[str, Any]:
    observed = observed_at.astimezone(UTC)
    payload = _load_snapshot(path)
    generated = event.parse_time(payload.get("generated_at"))
    base = {
        "schema_version": "daily-eurusd-event-overlay-v1",
        "enabled": True,
        "engine_profile": profiles.DAILY,
        "profile": profiles.profile(profiles.DAILY),
        "snapshot_path": str(path),
        "snapshot_generated_at": payload.get("generated_at"),
        "coverage": False,
        "decision_influence": False,
        "fail_neutral": True,
        "score": None,
    }
    if generated is None:
        return {**base, "status": "EVENT_SNAPSHOT_UNAVAILABLE", "age_minutes": None}

    signed_age = (observed - generated).total_seconds() / 60.0
    if signed_age < -MAX_FUTURE_SKEW_MINUTES:
        return {**base, "status": "EVENT_SNAPSHOT_AHEAD_OF_MARKET_CLOCK", "age_minutes": round(signed_age, 2)}
    age = max(0.0, signed_age)
    if age > MAX_SNAPSHOT_AGE_MINUTES:
        return {**base, "status": "EVENT_SNAPSHOT_STALE", "age_minutes": round(age, 2)}

    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    if not events:
        return {
            **base,
            "status": "NO_FRESH_CLASSIFIED_EVENTS",
            "age_minutes": round(age, 2),
            "coverage": True,
        }

    target = {
        "target_id": "eurusd",
        "symbol": "EURUSD=X",
        "market": "DAILY_FX",
        "sector": "fx",
        "name": "EUR/USD",
    }
    score = profiles.score_target(target, events, engine_profile=profiles.DAILY)
    return {
        **base,
        "status": "ACTIVE",
        "age_minutes": round(age, 2),
        "coverage": True,
        "decision_influence": bool(score.get("top_events")),
        "score": score,
    }


def entry_blocked(context: Mapping[str, Any], direction: str) -> bool:
    score = context.get("score") if isinstance(context.get("score"), Mapping) else None
    if not score or not context.get("coverage"):
        return False
    return profiles.entry_blocked_for_direction(score, direction)


def position_decision(context: Mapping[str, Any], direction: str) -> str:
    score = context.get("score") if isinstance(context.get("score"), Mapping) else None
    if not score or not context.get("coverage"):
        return "HOLD"
    return profiles.directional_decision(score, direction)


def maybe_close_position(
    position: Mapping[str, Any],
    bars: Sequence[Bar],
    observed_at: datetime,
    *,
    path: Path = event.OUTPUT_PATH,
) -> dict[str, Any] | None:
    context = build_context(observed_at, path)
    direction = str(position.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"} or position_decision(context, direction) != "CLOSE":
        return None
    eligible = [bar for bar in bars if bar.timestamp <= observed_at]
    if not eligible:
        return None
    last = sorted(eligible, key=lambda bar: bar.timestamp)[-1]
    trade = lifecycle._close_record(
        position,
        exit_reason="EVENT_INTELLIGENCE_THESIS_INVALIDATION",
        exit_price=float(last.close),
        exited_at=last.timestamp,
        exit_bar=last,
    )
    trade["event_intelligence"] = {
        "engine_profile": profiles.DAILY,
        "profile_version": profiles.PROFILE_VERSION,
        "snapshot_generated_at": context.get("snapshot_generated_at"),
        "age_minutes": context.get("age_minutes"),
        "score": context.get("score"),
    }
    return trade
