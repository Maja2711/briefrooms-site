#!/usr/bin/env python3
"""Canonical point-in-time market-data contract for BriefRooms.

P0.2 separates immutable market facts from policy-dependent data-quality
assessment. A CanonicalMarketSnapshot records what the engine could have seen;
FreshnessPolicy decides whether that snapshot is admissible for a specific
consumer at a specific decision time.

The module is deliberately fail-closed and has no trading, sizing, policy or
promotion authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

CONTRACT_VERSION = "briefrooms-market-snapshot-v1"
QUALITY_CONTRACT_VERSION = "briefrooms-market-data-quality-v1"
DATA_QUALITY_BLOCKED = "DATA_QUALITY_BLOCKED"
DECISION_ALLOWED = "ALLOWED"

STATUS_OK = "OK"
STATUS_STALE = "STALE"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
STATUS_SOURCE_ERROR = "SOURCE_ERROR"
QUALITY_STATUSES = {
    STATUS_OK,
    STATUS_STALE,
    STATUS_INCOMPLETE,
    STATUS_UNAVAILABLE,
    STATUS_INVALID_TIMESTAMP,
    STATUS_SOURCE_ERROR,
}

_INSTRUMENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_PRICE_FIELDS = ("bid", "ask", "last", "open", "high", "low", "close")


class MarketSnapshotError(ValueError):
    """Invalid canonical market-data fact or lineage."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse_aware(value: str | datetime, *, field: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise MarketSnapshotError(f"{field} is required")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise MarketSnapshotError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if dt.tzinfo is None:
        raise MarketSnapshotError(f"{field} must include an explicit timezone")
    return dt.astimezone(timezone.utc)


def iso_z(value: str | datetime, *, field: str = "timestamp") -> str:
    dt = _parse_aware(value, field=field)
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _number(value: Any, *, field: str, positive: bool = False, nonnegative: bool = False) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketSnapshotError(f"{field} must be numeric") from exc
    if not math.isfinite(out):
        raise MarketSnapshotError(f"{field} must be finite")
    if positive and out <= 0:
        raise MarketSnapshotError(f"{field} must be positive")
    if nonnegative and out < 0:
        raise MarketSnapshotError(f"{field} must be non-negative")
    return out


@dataclass(frozen=True)
class CanonicalMarketSnapshot:
    snapshot_id: str
    snapshot_hash: str
    instrument_id: str
    provider: str
    provider_symbol: str
    source_ref: str
    observed_at: str
    received_at: str
    created_at: str
    market_status: str
    quote_kind: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    source_schema: Optional[str] = None
    contract_version: str = CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FreshnessPolicy:
    policy_id: str
    max_age_seconds: float
    required_price_fields: tuple[str, ...] = ("last",)
    max_future_skew_seconds: float = 120.0
    allowed_market_statuses: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise MarketSnapshotError("FreshnessPolicy.policy_id is required")
        if self.max_age_seconds < 0:
            raise MarketSnapshotError("FreshnessPolicy.max_age_seconds must be non-negative")
        if self.max_future_skew_seconds < 0:
            raise MarketSnapshotError("FreshnessPolicy.max_future_skew_seconds must be non-negative")
        unknown = [field for field in self.required_price_fields if field not in _PRICE_FIELDS]
        if unknown:
            raise MarketSnapshotError("unknown required price fields: " + ", ".join(unknown))


@dataclass(frozen=True)
class DataQualityAssessment:
    status: str
    decision_status: str
    policy_id: str
    assessed_at: str
    age_seconds: Optional[float]
    stale: bool
    missing_fields: tuple[str, ...]
    reasons: tuple[str, ...]
    timestamp_lineage_valid: bool
    contract_version: str = QUALITY_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_snapshot(
    *,
    instrument_id: str,
    provider: str,
    provider_symbol: str,
    source_ref: str,
    observed_at: str | datetime,
    received_at: str | datetime,
    created_at: str | datetime,
    market_status: str,
    quote_kind: str,
    bid: Any = None,
    ask: Any = None,
    last: Any = None,
    open: Any = None,
    high: Any = None,
    low: Any = None,
    close: Any = None,
    volume: Any = None,
    source_schema: Optional[str] = None,
    max_lineage_clock_skew_seconds: float = 120.0,
) -> CanonicalMarketSnapshot:
    """Create an immutable deterministic snapshot from observed market facts.

    ``received_at`` is when BriefRooms received/finished the source request;
    ``created_at`` is when the canonical snapshot was constructed. Neither may
    be silently inferred from publication time by legacy adapters.
    """
    instrument_id = str(instrument_id or "").strip().lower()
    if not _INSTRUMENT_ID_RE.fullmatch(instrument_id):
        raise MarketSnapshotError(f"invalid canonical instrument_id: {instrument_id!r}")
    provider = str(provider or "").strip()
    provider_symbol = str(provider_symbol or "").strip()
    source_ref = str(source_ref or "").strip()
    market_status = str(market_status or "UNKNOWN").strip().upper()
    quote_kind = str(quote_kind or "UNKNOWN").strip().upper()
    if not provider or not provider_symbol or not source_ref:
        raise MarketSnapshotError("provider, provider_symbol and source_ref are required")

    observed_dt = _parse_aware(observed_at, field="observed_at")
    received_dt = _parse_aware(received_at, field="received_at")
    created_dt = _parse_aware(created_at, field="created_at")
    skew = timedelta(seconds=max(0.0, float(max_lineage_clock_skew_seconds)))
    if observed_dt > received_dt + skew:
        raise MarketSnapshotError("observed_at is later than received_at beyond clock-skew tolerance")
    if received_dt > created_dt + skew:
        raise MarketSnapshotError("received_at is later than created_at beyond clock-skew tolerance")

    numbers = {
        "bid": _number(bid, field="bid", positive=True),
        "ask": _number(ask, field="ask", positive=True),
        "last": _number(last, field="last", positive=True),
        "open": _number(open, field="open", positive=True),
        "high": _number(high, field="high", positive=True),
        "low": _number(low, field="low", positive=True),
        "close": _number(close, field="close", positive=True),
        "volume": _number(volume, field="volume", nonnegative=True),
    }
    if numbers["bid"] is not None and numbers["ask"] is not None and numbers["bid"] > numbers["ask"]:
        raise MarketSnapshotError("bid may not exceed ask")
    if numbers["high"] is not None:
        comparable = [numbers[key] for key in ("open", "low", "last", "close") if numbers[key] is not None]
        if comparable and numbers["high"] < max(comparable):
            raise MarketSnapshotError("high is inconsistent with observed price fields")
    if numbers["low"] is not None:
        comparable = [numbers[key] for key in ("open", "high", "last", "close") if numbers[key] is not None]
        if comparable and numbers["low"] > min(comparable):
            raise MarketSnapshotError("low is inconsistent with observed price fields")

    facts = {
        "contract_version": CONTRACT_VERSION,
        "instrument_id": instrument_id,
        "provider": provider,
        "provider_symbol": provider_symbol,
        "source_ref": source_ref,
        "observed_at": iso_z(observed_dt, field="observed_at"),
        "received_at": iso_z(received_dt, field="received_at"),
        "created_at": iso_z(created_dt, field="created_at"),
        "market_status": market_status,
        "quote_kind": quote_kind,
        **numbers,
        "source_schema": str(source_schema).strip() if source_schema else None,
    }
    digest = _sha(facts)
    return CanonicalMarketSnapshot(
        snapshot_id="mkt-" + digest[:24],
        snapshot_hash=digest,
        instrument_id=instrument_id,
        provider=provider,
        provider_symbol=provider_symbol,
        source_ref=source_ref,
        observed_at=facts["observed_at"],
        received_at=facts["received_at"],
        created_at=facts["created_at"],
        market_status=market_status,
        quote_kind=quote_kind,
        bid=numbers["bid"], ask=numbers["ask"], last=numbers["last"],
        open=numbers["open"], high=numbers["high"], low=numbers["low"],
        close=numbers["close"], volume=numbers["volume"],
        source_schema=facts["source_schema"],
    )


def assess_snapshot(
    snapshot: CanonicalMarketSnapshot,
    *,
    as_of: str | datetime,
    policy: FreshnessPolicy,
) -> DataQualityAssessment:
    """Evaluate a snapshot for one consumer/time without mutating the facts."""
    as_of_dt = _parse_aware(as_of, field="as_of")
    observed_dt = _parse_aware(snapshot.observed_at, field="snapshot.observed_at")
    received_dt = _parse_aware(snapshot.received_at, field="snapshot.received_at")
    created_dt = _parse_aware(snapshot.created_at, field="snapshot.created_at")
    future = timedelta(seconds=float(policy.max_future_skew_seconds))
    reasons: list[str] = []

    lineage_valid = True
    if observed_dt > received_dt + future:
        lineage_valid = False
        reasons.append("observed_after_received")
    if received_dt > created_dt + future:
        lineage_valid = False
        reasons.append("received_after_created")
    if observed_dt > as_of_dt + future:
        lineage_valid = False
        reasons.append("observed_after_consumption_time")
    if created_dt > as_of_dt + future:
        lineage_valid = False
        reasons.append("snapshot_created_after_consumption_time")

    age_seconds = (as_of_dt - observed_dt).total_seconds()
    usable_prices = [getattr(snapshot, field) for field in _PRICE_FIELDS if getattr(snapshot, field) is not None]
    missing = tuple(field for field in policy.required_price_fields if getattr(snapshot, field) is None)

    if not lineage_valid:
        status = STATUS_INVALID_TIMESTAMP
    elif not usable_prices:
        status = STATUS_UNAVAILABLE
        reasons.append("no_usable_price")
    elif missing:
        status = STATUS_INCOMPLETE
        reasons.append("required_price_fields_missing")
    elif policy.allowed_market_statuses and snapshot.market_status not in {
        str(value).upper() for value in policy.allowed_market_statuses
    }:
        status = STATUS_INCOMPLETE
        reasons.append("market_status_not_allowed")
    elif age_seconds > float(policy.max_age_seconds):
        status = STATUS_STALE
        reasons.append("snapshot_too_old")
    else:
        status = STATUS_OK

    stale = status == STATUS_STALE
    decision_status = DECISION_ALLOWED if status == STATUS_OK else DATA_QUALITY_BLOCKED
    return DataQualityAssessment(
        status=status,
        decision_status=decision_status,
        policy_id=policy.policy_id,
        assessed_at=iso_z(as_of_dt, field="as_of"),
        age_seconds=round(max(age_seconds, 0.0), 6) if lineage_valid else None,
        stale=stale,
        missing_fields=missing,
        reasons=tuple(reasons),
        timestamp_lineage_valid=lineage_valid,
    )


def require_usable(assessment: DataQualityAssessment) -> None:
    if assessment.status != STATUS_OK or assessment.decision_status != DECISION_ALLOWED:
        detail = ",".join(assessment.reasons) or assessment.status
        raise MarketSnapshotError(f"{DATA_QUALITY_BLOCKED}: {detail}")


def validate_decision_lineage(
    snapshot: CanonicalMarketSnapshot,
    *,
    decision_at: str | datetime,
    policy: FreshnessPolicy,
) -> DataQualityAssessment:
    """PR-C-ready hook: enforce observed <= received <= created <= decision."""
    result = assess_snapshot(snapshot, as_of=decision_at, policy=policy)
    require_usable(result)
    return result
