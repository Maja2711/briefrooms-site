#!/usr/bin/env python3
"""Canonical contracts for Stock Trading v2 shadow learning.

Phase 1A establishes immutable, prospective experience records without changing
production admission, ranking, sizing or portfolio decisions.  The contracts in
this module are intentionally market-agnostic so GPW and US can feed the same
learning layer.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

EXPERIENCE_SCHEMA_VERSION = "stock-trading-v2-experience-v1"
STORE_SCHEMA_VERSION = "stock-trading-v2-experience-store-v1"
SUPPORTED_MARKETS = {"GPW", "US"}
SUPPORTED_ACTIONS = {"LONG"}
SUPPORTED_DECISIONS = {"SELECTED", "REJECTED"}


class ContractError(ValueError):
    """Raised when an immutable v2 experience contract is invalid."""


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def payload_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def iso_utc(value: datetime | None = None) -> str:
    dt = value or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_event_id(
    *,
    market: str,
    decision_at: str,
    symbol: str,
    action: str,
    selected: bool,
) -> str:
    market_key = str(market).upper().strip()
    raw = {
        "market": market_key,
        "decision_at": str(decision_at),
        "symbol": str(symbol),
        "action": str(action).upper(),
        "selected": bool(selected),
    }
    digest = payload_sha256(raw)[:20]
    return f"stv2-{market_key.lower()}-{digest}"


def _normalise_market(value: Any) -> str:
    market = str(value or "").upper().strip()
    if market not in SUPPORTED_MARKETS:
        raise ContractError(f"unsupported market: {value}")
    return market


def _require_text(payload: Mapping[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ContractError(f"missing required field: {key}")
    return value


def make_experience_event(
    *,
    market: str,
    symbol: str,
    decision_at: str,
    session_date: str,
    selected: bool,
    source_engine: str,
    source_schema_version: str,
    source_policy_version: str,
    source_payload_sha256: str,
    candidate_state: Mapping[str, Any],
    recorded_at: str | None = None,
    action: str = "LONG",
) -> dict[str, Any]:
    market_key = _normalise_market(market)
    symbol_key = str(symbol or "").strip()
    if not symbol_key:
        raise ContractError("symbol is required")
    action_key = str(action or "").upper().strip()
    if action_key not in SUPPORTED_ACTIONS:
        raise ContractError(f"unsupported action: {action}")
    decision = "SELECTED" if selected else "REJECTED"
    decision_at_text = str(decision_at or "").strip()
    if not decision_at_text:
        raise ContractError("decision_at is required")
    session_date_text = str(session_date or "").strip()
    if not session_date_text:
        raise ContractError("session_date is required")
    if not str(source_payload_sha256 or "").strip():
        raise ContractError("source_payload_sha256 is required")

    event: dict[str, Any] = {
        "schema_version": EXPERIENCE_SCHEMA_VERSION,
        "event_type": "CANDIDATE_DECISION",
        "event_id": stable_event_id(
            market=market_key,
            decision_at=decision_at_text,
            symbol=symbol_key,
            action=action_key,
            selected=bool(selected),
        ),
        "market": market_key,
        "session_date": session_date_text,
        "decision_at": decision_at_text,
        "recorded_at": str(recorded_at or iso_utc()),
        "symbol": symbol_key,
        "action": action_key,
        "decision": decision,
        "selected": bool(selected),
        "source": {
            "engine": str(source_engine or "legacy-daily-stock"),
            "schema_version": str(source_schema_version or "unknown"),
            "policy_version": str(source_policy_version or "unknown"),
            "payload_sha256": str(source_payload_sha256),
        },
        "candidate_state": deepcopy(dict(candidate_state)),
        "governance": {
            "prospective_only": True,
            "immutable": True,
            "production_decision_influence": False,
            "automatic_policy_writeback": False,
            "learning_eligible": True,
            "phase": "v2_phase1a_shadow",
        },
    }
    event["event_sha256"] = payload_sha256(event)
    validate_experience_event(event)
    return event


def validate_experience_event(event: Mapping[str, Any]) -> None:
    if event.get("schema_version") != EXPERIENCE_SCHEMA_VERSION:
        raise ContractError("experience schema mismatch")
    if event.get("event_type") != "CANDIDATE_DECISION":
        raise ContractError("unsupported event_type")

    market = _normalise_market(event.get("market"))
    symbol = _require_text(event, "symbol")
    decision_at = _require_text(event, "decision_at")
    _require_text(event, "session_date")
    _require_text(event, "recorded_at")

    selected = event.get("selected")
    if not isinstance(selected, bool):
        raise ContractError("selected must be boolean")
    action = str(event.get("action") or "").upper()
    if action not in SUPPORTED_ACTIONS:
        raise ContractError("unsupported action")
    decision = str(event.get("decision") or "").upper()
    if decision not in SUPPORTED_DECISIONS:
        raise ContractError("unsupported decision")
    expected_decision = "SELECTED" if selected else "REJECTED"
    if decision != expected_decision:
        raise ContractError("decision/selected mismatch")

    expected_id = stable_event_id(
        market=market,
        decision_at=decision_at,
        symbol=symbol,
        action=action,
        selected=selected,
    )
    if event.get("event_id") != expected_id:
        raise ContractError("event_id mismatch")

    source = event.get("source")
    if not isinstance(source, Mapping):
        raise ContractError("source must be an object")
    for key in ("engine", "schema_version", "policy_version", "payload_sha256"):
        if not str(source.get(key) or "").strip():
            raise ContractError(f"source.{key} is required")

    candidate_state = event.get("candidate_state")
    if not isinstance(candidate_state, Mapping):
        raise ContractError("candidate_state must be an object")

    governance = event.get("governance")
    if not isinstance(governance, Mapping):
        raise ContractError("governance must be an object")
    required_governance = {
        "prospective_only": True,
        "immutable": True,
        "production_decision_influence": False,
        "automatic_policy_writeback": False,
    }
    for key, expected in required_governance.items():
        if governance.get(key) is not expected:
            raise ContractError(f"governance invariant failed: {key}")

    body = dict(event)
    stored_hash = str(body.pop("event_sha256", ""))
    if not stored_hash:
        raise ContractError("event_sha256 is required")
    if stored_hash != payload_sha256(body):
        raise ContractError("event hash mismatch")


def event_without_runtime_fields(event: Mapping[str, Any]) -> dict[str, Any]:
    """Stable semantic body used by idempotency checks.

    ``recorded_at`` is intentionally ignored because repeated ingestion of the
    exact same producer decision must remain idempotent even when executed at a
    later wall-clock time.
    """
    body = deepcopy(dict(event))
    body.pop("event_sha256", None)
    body.pop("recorded_at", None)
    return body
