#!/usr/bin/env python3
"""Belief-first final decision synthesis for Daily EUR/USD.

This module is the production epistemic consumer for TR-03. It does not fetch
raw sources and it does not execute trades. Direction is synthesized only from
fresh EUR/USD Belief Core state that was created upstream by adapters and
EvidenceAssessment.

Canonical order:
RAW SOURCES -> ADAPTERS -> OBSERVATIONS -> EVIDENCE -> BELIEF CORE
-> DAILY EURUSD FINAL DECISION -> RISK/ENTRY -> EPE.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from epistemic_consumer_interface import (
    EURUSD_MIN_COVERAGE_WEIGHT,
    EURUSD_PROFILE_WEIGHTS,
    EURUSD_REQUIRED_ANCHOR,
    EpistemicConsumerInterface,
)

LONG_THRESHOLD = 60.0
SHORT_THRESHOLD = 40.0

# CF-07 owns the predeclared EUR/USD epistemic aggregation weights. The
# decision engine consumes that authoritative aggregate rather than defining a
# second, potentially divergent aggregation.
BELIEF_WEIGHTS = dict(EURUSD_PROFILE_WEIGHTS)

# Freshness follows the corresponding BeliefDefinition half-life. Missing or
# stale evidence is not converted to a neutral vote; it reduces coverage.
BELIEF_MAX_AGE_HOURS = {
    "eurusd.trend.bullish": 18.0,
    "eurusd.usd_environment.supportive": 24.0,
    "eurusd.us_rates_pressure.supportive": 24.0,
    "eurusd.macro_surprise.supportive": 12.0,
    "eurusd.policy_differential.supportive": 18.0,
}

MIN_COVERAGE_WEIGHT = EURUSD_MIN_COVERAGE_WEIGHT
REQUIRED_ANCHOR_BELIEF = EURUSD_REQUIRED_ANCHOR


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def belief_state_path() -> Optional[Path]:
    raw = os.environ.get("BELIEF_CORE_STATE", "").strip()
    return Path(raw) if raw else None


def load_state(path: Path | None = None) -> dict[str, Any]:
    target = path or belief_state_path()
    if target is None or not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


EPISTEMIC_MAX_SNAPSHOT_AGE_HOURS = 2.0


def epistemic_state_path() -> Optional[Path]:
    raw = os.environ.get("BELIEF_EPISTEMIC_STATE", "").strip()
    return Path(raw) if raw else None


def _epistemic_projection_to_belief_state(
    payload: Mapping[str, Any],
    *,
    observed_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Translate the bounded CF-07 consumer envelope into synthesis inputs.

    The decision engine never mutates the epistemic aggregate. Freshness from
    EpistemicState is converted back into an equivalent age only so the existing
    per-belief half-life admission contract can remain explicit.
    """
    try:
        interface = EpistemicConsumerInterface(payload)
        envelope = interface.envelope("DAILY_EURUSD")
    except Exception as exc:
        return {}, {
            "contract": "epistemic-consumer-interface-v1",
            "consumer": "DAILY_EURUSD",
            "available": False,
            "reason": f"epistemic_consumer_invalid:{type(exc).__name__}",
        }

    source_at = _parse_time(envelope.source_created_at)
    now = observed_at.astimezone(timezone.utc)
    if source_at is None:
        return {}, {
            "contract": envelope.contract_version,
            "consumer": "DAILY_EURUSD",
            "available": False,
            "reason": "epistemic_timestamp_missing",
        }
    snapshot_age = (now - source_at).total_seconds() / 3600.0
    if snapshot_age < -0.10:
        return {}, {
            "contract": envelope.contract_version,
            "consumer": "DAILY_EURUSD",
            "available": False,
            "reason": "epistemic_state_created_after_engine_state",
            "source_created_at": envelope.source_created_at,
            "age_hours": round(snapshot_age, 6),
        }
    if snapshot_age > EPISTEMIC_MAX_SNAPSHOT_AGE_HOURS:
        return {}, {
            "contract": envelope.contract_version,
            "consumer": "DAILY_EURUSD",
            "available": False,
            "reason": "epistemic_state_too_old",
            "source_created_at": envelope.source_created_at,
            "age_hours": round(snapshot_age, 6),
        }
    if not envelope.available:
        return {}, {
            "contract": envelope.contract_version,
            "consumer": "DAILY_EURUSD",
            "available": False,
            "reason": envelope.reason,
            "source_created_at": envelope.source_created_at,
            "age_hours": round(snapshot_age, 6),
        }

    beliefs: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for row in envelope.states:
        belief_id = str(row.get("belief_id") or "")
        if belief_id not in BELIEF_WEIGHTS:
            continue
        try:
            freshness = max(0.0, min(1.0, float(row.get("freshness") or 0.0)))
        except (TypeError, ValueError):
            freshness = 0.0
        half_life = BELIEF_MAX_AGE_HOURS[belief_id]
        if freshness <= 0.0:
            equivalent_age = half_life * 100.0
        else:
            equivalent_age = max(0.0, -half_life * math.log(freshness, 2.0))
        evidence_at = now - timedelta(hours=equivalent_age)
        evidence_id = f"epistemic:{row.get('state_id') or belief_id}"
        evidence.append({
            "evidence_id": evidence_id,
            "observed_at": evidence_at.isoformat().replace("+00:00", "Z"),
        })
        beliefs.append({
            "belief_id": belief_id,
            "probability": row.get("probability"),
            "confidence": row.get("confidence"),
            "audit_status": row.get("audit_status"),
            "representative_evidence_ids": [evidence_id],
        })

    return {
        "beliefs": beliefs,
        "evidence": evidence,
    }, {
        "contract": envelope.contract_version,
        "source_contract": envelope.source_contract_version,
        "consumer": "DAILY_EURUSD",
        "available": True,
        "reason": envelope.reason,
        "source_created_at": envelope.source_created_at,
        "age_hours": round(snapshot_age, 6),
        "source_sha256": envelope.source_sha256,
        "aggregate_authoritative": envelope.authority.aggregate_authoritative,
        "consumer_may_override_probability": envelope.authority.consumer_may_override_probability,
        "belief_core_writeback_enabled": envelope.authority.belief_core_writeback_enabled,
        "state_count": len(envelope.states),
        "aggregate_probability": envelope.aggregate_probability,
        "aggregate_confidence": envelope.aggregate_confidence,
        "coverage_weight": envelope.coverage_weight,
        "qualified_state_count": envelope.qualified_state_count,
        "qualified_belief_ids": [
            str(row.get("belief_id"))
            for row in envelope.states
            if float(row.get("freshness") or 0.0) >= 0.50
            and str(row.get("audit_status") or "").lower() != "critical"
        ],
    }


def load_effective_state(
    *,
    observed_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    ep_path = epistemic_state_path()
    require_epistemic = os.environ.get("DAILY_EURUSD_REQUIRE_EPISTEMIC", "").strip().lower() in {
        "1", "true", "yes",
    }
    if ep_path is not None and ep_path.exists():
        try:
            payload = json.loads(ep_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        state, meta = _epistemic_projection_to_belief_state(payload, observed_at=observed_at)
        return state, meta

    if require_epistemic:
        return {}, {
            "contract": "epistemic-consumer-interface-v1",
            "consumer": "DAILY_EURUSD",
            "available": False,
            "reason": "required_epistemic_consumer_state_unavailable",
        }

    # Compatibility path for isolated unit tests/manual research. Production
    # workflow sets DAILY_EURUSD_REQUIRE_EPISTEMIC=1 and cannot use this path.
    return load_state(), {
        "contract": "legacy-raw-belief-state-compat",
        "consumer": "DAILY_EURUSD",
        "available": bool(load_state()),
        "reason": "test_or_manual_compatibility_path",
    }


def _evidence_index(state: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row.get("evidence_id")): row
        for row in (state.get("evidence") or [])
        if isinstance(row, Mapping) and row.get("evidence_id")
    }


def _latest_representative_evidence_at(
    belief: Mapping[str, Any],
    evidence: Mapping[str, Mapping[str, Any]],
) -> Optional[datetime]:
    times = []
    for evidence_id in belief.get("representative_evidence_ids") or []:
        row = evidence.get(str(evidence_id))
        when = _parse_time((row or {}).get("observed_at"))
        if when is not None:
            times.append(when)
    return max(times) if times else None


def synthesize(
    state: Mapping[str, Any],
    *,
    observed_at: datetime,
    authoritative_consumer: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the single final LONG/SHORT/FLAT decision from Belief Core state."""
    now = observed_at.astimezone(timezone.utc)
    beliefs = {
        str(row.get("belief_id")): row
        for row in (state.get("beliefs") or [])
        if isinstance(row, Mapping) and row.get("belief_id")
    }
    evidence = _evidence_index(state)

    used: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    weighted_signal = 0.0
    used_weight = 0.0
    weighted_confidence = 0.0

    for belief_id, weight in BELIEF_WEIGHTS.items():
        row = beliefs.get(belief_id)
        if row is None:
            unavailable.append({"belief_id": belief_id, "reason": "missing_belief"})
            continue
        if str(row.get("audit_status") or "").lower() == "critical":
            unavailable.append({"belief_id": belief_id, "reason": "belief_audit_critical"})
            continue
        updated = _latest_representative_evidence_at(row, evidence)
        if updated is None:
            unavailable.append({"belief_id": belief_id, "reason": "no_representative_evidence"})
            continue
        age_hours = (now - updated).total_seconds() / 3600.0
        max_age = BELIEF_MAX_AGE_HOURS[belief_id]
        if age_hours < -0.10 or age_hours > max_age:
            unavailable.append({
                "belief_id": belief_id,
                "reason": "stale_or_future_evidence",
                "age_hours": round(age_hours, 4),
                "max_age_hours": max_age,
            })
            continue
        try:
            probability = float(row.get("probability"))
            confidence = float(row.get("confidence"))
        except (TypeError, ValueError):
            unavailable.append({"belief_id": belief_id, "reason": "invalid_probability_or_confidence"})
            continue
        if not math.isfinite(probability) or not math.isfinite(confidence):
            unavailable.append({"belief_id": belief_id, "reason": "non_finite_probability_or_confidence"})
            continue
        probability = max(0.0, min(1.0, probability))
        confidence = max(0.0, min(1.0, confidence))
        directional = (probability - 0.5) * 2.0
        effective = directional * confidence
        weighted_signal += float(weight) * effective
        weighted_confidence += float(weight) * confidence
        used_weight += float(weight)
        used.append({
            "belief_id": belief_id,
            "probability": round(probability, 6),
            "confidence": round(confidence, 6),
            "directional_support": round(directional, 6),
            "effective_support": round(effective, 6),
            "weight": float(weight),
            "observed_at": updated.isoformat().replace("+00:00", "Z"),
            "age_hours": round(age_hours, 4),
        })

    total_weight = sum(BELIEF_WEIGHTS.values())
    coverage = 0.0 if total_weight <= 0 else used_weight / total_weight
    anchor_present = any(row["belief_id"] == REQUIRED_ANCHOR_BELIEF for row in used)

    if used_weight <= 0:
        normalized = 0.0
        mean_confidence = 0.0
    else:
        normalized = max(-1.0, min(1.0, weighted_signal / used_weight))
        mean_confidence = max(0.0, min(1.0, weighted_confidence / used_weight))

    score = round(50.0 + 50.0 * normalized, 2)
    confidence = round(mean_confidence * coverage, 4)

    consumer_authoritative = bool(
        authoritative_consumer
        and authoritative_consumer.get("available") is True
        and authoritative_consumer.get("contract") == "epistemic-consumer-interface-v1"
        and authoritative_consumer.get("aggregate_authoritative") is True
    )
    if consumer_authoritative:
        try:
            score = round(100.0 * float(authoritative_consumer["aggregate_probability"]), 2)
            confidence = round(float(authoritative_consumer["aggregate_confidence"]), 4)
            coverage = float(authoritative_consumer.get("coverage_weight") or 0.0)
        except (KeyError, TypeError, ValueError):
            consumer_authoritative = False

    reasons: list[str] = []
    if not state:
        reasons.append("belief_state_unavailable")
    if not anchor_present:
        reasons.append("required_eurusd_trend_belief_unavailable")
    if coverage < MIN_COVERAGE_WEIGHT:
        reasons.append("insufficient_belief_coverage")

    if reasons:
        direction = "FLAT"
    elif score >= LONG_THRESHOLD:
        direction = "LONG"
    elif score <= SHORT_THRESHOLD:
        direction = "SHORT"
    else:
        direction = "FLAT"
        reasons.append("belief_score_neutral")

    return {
        "schema_version": "daily-eurusd-belief-decision-v1",
        "owner": "NATIVE_DAILY_EURUSD_BELIEF_FIRST_DECISION_ENGINE",
        "decision_source": "BELIEF_CORE",
        "direction": direction,
        "score": score,
        "confidence": confidence,
        "thresholds": {"long": LONG_THRESHOLD, "short": SHORT_THRESHOLD},
        "coverage_weight": round(coverage, 6),
        "minimum_coverage_weight": MIN_COVERAGE_WEIGHT,
        "required_anchor_belief": REQUIRED_ANCHOR_BELIEF,
        "used_beliefs": used,
        "unavailable_beliefs": unavailable,
        "reasons": reasons,
        "routing": [
            "RAW_SOURCES",
            "SPECIALIZED_ADAPTERS",
            "OBSERVATIONS",
            "EVIDENCE_ASSESSMENT",
            "BELIEF_CORE",
            "NATIVE_DAILY_EURUSD_FINAL_DECISION",
        ],
        "legacy_raw_score_direction_authority": False,
        "shadow_engine_direction_authority": False,
        "epistemic_aggregate_authoritative": consumer_authoritative,
    }


def calendar_safety(
    *,
    observed_at: datetime,
    state_path: Path | None = None,
    decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read only Belief-adapter calendar observations for execution safety.

    Scheduled-event proximity is not a direction vote. It can block execution
    after the final decision while preserving that final decision in metadata.
    """
    path = state_path or belief_state_path()
    if path is None:
        return {"available": False, "blocked": True, "reason": "belief_state_path_unavailable"}
    observations = path.parent / "observations.jsonl"
    if not observations.exists():
        return {"available": False, "blocked": True, "reason": "belief_calendar_observations_unavailable"}

    now = observed_at.astimezone(timezone.utc)
    events: list[dict[str, Any]] = []
    latest_coverage: dict[str, Any] | None = None
    latest_coverage_at: Optional[datetime] = None
    try:
        for raw in observations.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)
            if row.get("adapter") != "macro_event_calendar":
                continue
            if row.get("metric") == "calendar_coverage":
                coverage_at = _parse_time(row.get("observed_at"))
                if coverage_at is not None and (latest_coverage_at is None or coverage_at > latest_coverage_at):
                    latest_coverage_at = coverage_at
                    latest_coverage = dict(row.get("metadata") or {})
                continue
            if row.get("metric") != "scheduled_macro_event":
                continue
            meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            if str(meta.get("importance") or "").lower() != "high":
                continue
            event_at = _parse_time(meta.get("event_at"))
            if event_at is None:
                continue
            hours = (event_at - now).total_seconds() / 3600.0
            if -0.5 <= hours <= 24.0:
                events.append({
                    "title": meta.get("title"),
                    "event_at": event_at.isoformat().replace("+00:00", "Z"),
                    "hours_until": round(hours, 4),
                    "source": row.get("source"),
                    "source_ref": row.get("source_ref"),
                    "region": meta.get("region"),
                })
    except (OSError, json.JSONDecodeError):
        return {"available": False, "blocked": True, "reason": "belief_calendar_observations_unreadable"}

    events.sort(key=lambda row: float(row["hours_until"]))
    imminent = [row for row in events if 0.0 <= float(row["hours_until"]) <= 1.0]
    recent = [row for row in events if -0.5 <= float(row["hours_until"]) < 0.0]

    coverage_age_minutes: float | None = None
    coverage_complete = False
    if latest_coverage_at is not None:
        coverage_age_minutes = (now - latest_coverage_at).total_seconds() / 60.0
        coverage_complete = bool((latest_coverage or {}).get("complete")) and -5.0 <= coverage_age_minutes <= 90.0

    latest_macro_at: Optional[datetime] = None
    for row in (decision or {}).get("used_beliefs") or []:
        if str(row.get("belief_id")) not in {
            "eurusd.macro_surprise.supportive",
            "eurusd.policy_differential.supportive",
        }:
            continue
        stamp = _parse_time(row.get("observed_at"))
        if stamp is not None and (latest_macro_at is None or stamp > latest_macro_at):
            latest_macro_at = stamp

    post_release_pending = False
    recent_event = recent[-1] if recent else None
    if recent_event is not None:
        event_at = _parse_time(recent_event.get("event_at"))
        post_release_pending = bool(
            event_at is not None
            and (latest_macro_at is None or latest_macro_at < event_at)
        )

    coverage_failed = not coverage_complete
    blocked = bool(imminent or post_release_pending or coverage_failed)
    reason = (
        "belief_high_impact_event_imminent"
        if imminent
        else "belief_post_release_evidence_pending"
        if post_release_pending
        else "belief_calendar_coverage_unavailable"
        if latest_coverage is None
        else "belief_calendar_coverage_stale"
        if coverage_age_minutes is None or coverage_age_minutes > 90.0
        else "belief_calendar_coverage_failed"
        if not bool(latest_coverage.get("complete"))
        else "clear"
    )
    return {
        "available": latest_coverage is not None,
        "blocked": blocked,
        "reason": reason,
        "imminent": bool(imminent),
        "post_release_evidence_pending": post_release_pending,
        "calendar_coverage": {
            "complete": coverage_complete,
            "observed_at": (
                latest_coverage_at.isoformat().replace("+00:00", "Z")
                if latest_coverage_at is not None
                else None
            ),
            "age_minutes": None if coverage_age_minutes is None else round(coverage_age_minutes, 3),
            "source_status": dict((latest_coverage or {}).get("sources") or {}),
        },
        "events": events[:8],
        "source": "belief_macro_calendar_adapter",
        "direction_mutation_allowed": False,
    }
