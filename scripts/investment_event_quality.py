#!/usr/bin/env python3
"""Precision guard for production Investment Event Intelligence.

The base collector intentionally has broad recall. This module removes lexical false
positives before any event is allowed to influence portfolio decisions and
recomputes authority with token/phrase boundaries rather than substring matches.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Iterable, Mapping

import investment_event_intelligence as event


HEAD_OF_STATE = (
    "president", "prime minister", "supreme leader", "chancellor", "premier",
    "white house", "kremlin",
)
SENIOR_CABINET = (
    "foreign minister", "defense minister", "defence minister", "finance minister",
    "secretary of state", "treasury secretary", "national security adviser",
)
STATE_INSTITUTION = (
    "government", "ministry", "nato", "european commission", "central bank",
    "military chief", "armed forces", "u.s. house", "us house", "senate",
    "congress", "parliament",
)

LABOR_CONTEXT = (
    "worker", "workers", "employee", "employees", "union", "unions", "wage",
    "wages", "pay dispute", "labor strike", "labour strike", "walkout",
)
MILITARY_CONTEXT = (
    "airstrike", "air strike", "missile", "drone", "military", "army", "armed",
    "attack", "bomb", "war", "combat", "retaliat", "invasion", "blockade",
    "ukraine", "russia", "israel", "iran", "gaza", "taiwan", "hormuz",
)
DEESCALATION_ACTION = (
    "announce", "agree", "agreed", "sign", "signed", "reach", "reached",
    "begin", "began", "start", "started", "extend", "extended", "accept",
    "accepted", "propose", "proposed", "prepare", "prepared", "negotiat",
    "peace talks", "talks", "hold", "holds", "entered into force", "withdraw",
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _phrase(text: str, phrase: str) -> bool:
    """Match a word or multiword phrase without substring collisions.

    Example: NATO must not match seNATOr.
    """
    escaped = re.escape(phrase.casefold()).replace(r"\ ", r"\s+")
    return re.search(rf"(?<![\w]){escaped}(?![\w])", text) is not None


def exact_authority(title: str) -> tuple[float, str]:
    text = _norm(title)
    if any(_phrase(text, token) for token in HEAD_OF_STATE):
        return 0.98, "head_of_state"
    if any(_phrase(text, token) for token in SENIOR_CABINET):
        return 0.93, "senior_cabinet"
    if any(_phrase(text, token) for token in STATE_INSTITUTION):
        return 0.88, "state_institution"
    return 0.58, "reported_actor"


def rejection_reason(row: Mapping[str, Any]) -> str | None:
    text = _norm(row.get("title"))

    # "Strike" is highly ambiguous. A labour strike is not a geopolitical market
    # shock merely because a president is mentioned in the same headline.
    if _phrase(text, "strike") or _phrase(text, "strikes"):
        labor = any(_phrase(text, token) for token in LABOR_CONTEXT)
        military = any(_phrase(text, token) for token in MILITARY_CONTEXT)
        if labor and not military:
            return "labor_strike_not_military_strike"

    # A ceasefire/truce mentioned only as background (e.g. an accident "during
    # truce") is not itself a new de-escalation event. Require an operative verb.
    if str(row.get("event_type") or "") == "deescalation":
        has_action = any(token in text for token in DEESCALATION_ACTION)
        if not has_action:
            return "contextual_deescalation_without_new_action"

    return None


def curate_events(rows: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for original in rows:
        row = dict(original)
        reason = rejection_reason(row)
        if reason:
            rejected.append({
                "event_id": row.get("event_id"),
                "title": row.get("title"),
                "reason": reason,
            })
            continue

        authority, role = exact_authority(str(row.get("title") or ""))
        row["authority"] = authority
        row["actor_role"] = role
        confidence = (
            authority
            * float(row.get("source_reliability") or 0.0)
            * float(row.get("action_strength") or 0.0)
            * float(row.get("corroboration") or 0.0)
        )
        confidence = event.clamp(confidence, 0.0, 1.0)
        strength = (
            float(row.get("severity") or 0.0)
            * confidence
            * float(row.get("time_decay") or 0.0)
        )
        row["confidence"] = round(confidence, 4)
        row["event_strength"] = round(event.clamp(strength, 0.0, 1.0), 4)
        row["quality_guard"] = "accepted_precision_v1"
        accepted.append(row)

    accepted.sort(
        key=lambda row: (float(row.get("event_strength") or 0.0), -float(row.get("age_hours") or 0.0)),
        reverse=True,
    )
    return accepted[: event.MAX_EVENTS], rejected


def collect(now: datetime) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    raw, errors = event.collect_events(now)
    accepted, rejected = curate_events(raw)
    return accepted, errors, rejected


def build_snapshot(now: datetime) -> tuple[dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
    events, errors, rejected = collect(now)
    targets, stock_state, week_path, week = event.build_targets(now)
    scores = {str(target["target_id"]): event.score_target(target, events) for target in targets}
    payload = {
        "schema_version": event.SCHEMA,
        "engine_version": event.VERSION,
        "quality_guard_version": "event-precision-v1",
        "mode": "production_decision_overlay",
        "generated_at": event.iso_z(now),
        "status": "healthy" if events else "degraded_no_fresh_classified_events",
        "source_errors": errors,
        "quality_rejections": rejected,
        "controls": {
            "standalone_entry_authority": False,
            "existing_model_entry_authority_preserved": True,
            "entry_veto_enabled": True,
            "close_override_enabled": True,
            "partial_reduce_execution_enabled": False,
            "market_confirmation_is_input_not_mandatory_gate": True,
            "fail_closed_on_missing_event_feed": False,
            "no_event_data_means_no_overlay_change": True,
            "lexical_precision_guard_enabled": True,
        },
        "thresholds": {
            "entry_block": event.ENTRY_BLOCK_THRESHOLD,
            "reduce_risk": event.REDUCE_RISK_THRESHOLD,
            "close": event.CLOSE_THRESHOLD,
            "minimum_entry_confidence": event.MIN_ENTRY_CONFIDENCE,
            "minimum_close_confidence": event.MIN_CLOSE_CONFIDENCE,
        },
        "events": events,
        "targets": list(scores.values()),
        "actions": [],
        "weekly_state_path": str(week_path.relative_to(event.ROOT)) if week_path else None,
    }
    event._write(event.OUTPUT_PATH, payload)
    return payload, stock_state, week_path, week
