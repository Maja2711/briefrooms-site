#!/usr/bin/env python3
"""Canonical contracts for the BriefRooms Evolution Controller.

These contracts unify candidate lifecycle, promotion evidence, production
version lineage and rollback events across BriefRooms learning systems.

The controller is governance/orchestration authority only. A ProductionVersion
does not grant trade execution. Materialization is delegated to the owning
engine writer declared by promotion_route.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

SCHEMA_VERSION = "briefrooms-evolution-contracts-v1"

CANDIDATE_STATUSES = {
    "DISCOVERED", "COLLECTING", "READY_FOR_OOS", "OOS_RUNNING",
    "PROMOTION_ELIGIBLE", "PROMOTION_DELEGATED", "PROMOTED",
    "REJECTED", "PARKED", "RETIRED", "ROLLED_BACK",
}
GATE_STATUSES = {"COLLECTING", "PASS", "FAIL", "HOLD"}
VERSION_STATUSES = {"ACTIVE", "RETIRED", "ROLLED_BACK", "SUPERSEDED"}
ROLLBACK_REASONS = {
    "POST_PROMOTION_DEGRADATION", "SEGMENT_SAFETY_FAILURE",
    "DATA_QUALITY_FAILURE", "INVARIANT_FAILURE",
    "OWNER_HEALTHCHECK_FAILURE", "MANUAL_GOVERNANCE",
}


def utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}-{sha256(payload)[:24]}"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _iso(value: str) -> str:
    text = str(value or "").strip()
    _require(bool(text), "timestamp is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class EvolutionCandidate:
    candidate_id: str
    candidate_type: str
    source_module: str
    target_module: str
    component_id: str
    created_at: str
    activation_boundary: str
    status: str
    evaluator_profile: str
    promotion_route: str
    baseline_version: str | None
    challenger_version: str
    proposed_change: Mapping[str, Any]
    source_ref: str
    source_sha256: str
    metrics: Mapping[str, Any] = field(default_factory=dict)
    protected_slices: tuple[Mapping[str, Any], ...] = ()
    automatic_promotion_allowed: bool = False
    trade_execution_authority: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> "EvolutionCandidate":
        _require(self.status in CANDIDATE_STATUSES, f"invalid candidate status: {self.status}")
        for key, value in (
            ("candidate_id", self.candidate_id), ("candidate_type", self.candidate_type),
            ("source_module", self.source_module), ("target_module", self.target_module),
            ("component_id", self.component_id), ("evaluator_profile", self.evaluator_profile),
            ("promotion_route", self.promotion_route), ("challenger_version", self.challenger_version),
            ("source_ref", self.source_ref), ("source_sha256", self.source_sha256),
        ):
            _require(bool(str(value).strip()), f"{key} is required")
        _iso(self.created_at)
        _iso(self.activation_boundary)
        _require(self.trade_execution_authority is False, "EvolutionCandidate cannot carry trade execution authority")
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["protected_slices"] = [dict(x) for x in self.protected_slices]
        return payload


@dataclass(frozen=True)
class PromotionGate:
    gate_id: str
    candidate_id: str
    evaluated_at: str
    status: str
    evaluator_profile: str
    prospective_only: bool
    minimum_sample: int
    observed_sample: int
    criteria: Mapping[str, Any]
    metrics: Mapping[str, Any]
    segment_checks: tuple[Mapping[str, Any], ...] = ()
    blockers: tuple[str, ...] = ()
    source_sha256: str = ""

    def validate(self) -> "PromotionGate":
        _require(self.status in GATE_STATUSES, f"invalid gate status: {self.status}")
        _require(self.prospective_only is True, "promotion gate must be prospective-only")
        _require(self.minimum_sample > 0, "minimum_sample must be positive")
        _require(self.observed_sample >= 0, "observed_sample cannot be negative")
        _iso(self.evaluated_at)
        if self.status == "PASS":
            _require(self.observed_sample >= self.minimum_sample, "PASS cannot occur below minimum sample")
            _require(not self.blockers, "PASS cannot contain blockers")
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["segment_checks"] = [dict(x) for x in self.segment_checks]
        payload["blockers"] = list(self.blockers)
        return payload


@dataclass(frozen=True)
class ProductionVersion:
    version_id: str
    component_id: str
    candidate_id: str
    activated_at: str
    parent_version_id: str | None
    owner_module: str
    materialization_route: str
    payload_sha256: str
    status: str = "ACTIVE"
    trade_execution_authority: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> "ProductionVersion":
        _require(self.status in VERSION_STATUSES, f"invalid production version status: {self.status}")
        _iso(self.activated_at)
        _require(self.trade_execution_authority is False, "ProductionVersion contract does not grant execution authority")
        for value in (
            self.version_id, self.component_id, self.candidate_id,
            self.owner_module, self.materialization_route, self.payload_sha256,
        ):
            _require(bool(str(value).strip()), "production version fields cannot be empty")
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class RollbackEvent:
    rollback_id: str
    component_id: str
    from_version_id: str
    to_version_id: str | None
    candidate_id: str
    detected_at: str
    effective_at: str
    reason: str
    metrics: Mapping[str, Any]
    owner_module: str
    materialization_route: str
    automatic: bool
    trade_execution_authority: bool = False

    def validate(self) -> "RollbackEvent":
        _require(self.reason in ROLLBACK_REASONS, f"invalid rollback reason: {self.reason}")
        _iso(self.detected_at)
        _iso(self.effective_at)
        _require(self.trade_execution_authority is False, "RollbackEvent cannot grant execution authority")
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


def candidate_from_payload(payload: Mapping[str, Any]) -> EvolutionCandidate:
    return EvolutionCandidate(
        candidate_id=str(payload["candidate_id"]),
        candidate_type=str(payload["candidate_type"]),
        source_module=str(payload["source_module"]),
        target_module=str(payload["target_module"]),
        component_id=str(payload["component_id"]),
        created_at=str(payload["created_at"]),
        activation_boundary=str(payload["activation_boundary"]),
        status=str(payload["status"]),
        evaluator_profile=str(payload["evaluator_profile"]),
        promotion_route=str(payload["promotion_route"]),
        baseline_version=None if payload.get("baseline_version") is None else str(payload["baseline_version"]),
        challenger_version=str(payload["challenger_version"]),
        proposed_change=dict(payload.get("proposed_change") or {}),
        source_ref=str(payload["source_ref"]),
        source_sha256=str(payload["source_sha256"]),
        metrics=dict(payload.get("metrics") or {}),
        protected_slices=tuple(dict(x) for x in payload.get("protected_slices") or []),
        automatic_promotion_allowed=bool(payload.get("automatic_promotion_allowed", False)),
        trade_execution_authority=bool(payload.get("trade_execution_authority", False)),
        metadata=dict(payload.get("metadata") or {}),
    ).validate()
