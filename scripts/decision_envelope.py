#!/usr/bin/env python3
"""Canonical decision contract for BriefRooms economic engines.

PR-C binds a source engine decision to the exact market snapshot and the exact
engine-owned risk assessment that admitted it. The envelope is deterministic
and has no independent decision authority: the source engine still decides,
its own RiskPolicy still owns limits, and the envelope only proves lineage.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any, Mapping, Optional

try:
    from canonical_market_snapshot import (
        CanonicalMarketSnapshot,
        FreshnessPolicy,
        MarketSnapshotError,
        STATUS_OK,
        DECISION_ALLOWED,
        build_snapshot,
        iso_z,
        validate_decision_lineage,
    )
    from risk_policy_contract import RiskAssessment, RiskPolicyError, require_approved, verify_assessment
except ModuleNotFoundError:
    from scripts.canonical_market_snapshot import (
        CanonicalMarketSnapshot,
        FreshnessPolicy,
        MarketSnapshotError,
        STATUS_OK,
        DECISION_ALLOWED,
        build_snapshot,
        iso_z,
        validate_decision_lineage,
    )
    from scripts.risk_policy_contract import RiskAssessment, RiskPolicyError, require_approved, verify_assessment

CONTRACT_VERSION = "briefrooms-decision-envelope-v1"
ECONOMIC_ACTIONS = {"LONG", "SHORT", "HOLD"}
ALLOWED_ACTIONS = ECONOMIC_ACTIONS | {"FLAT"}


class DecisionEnvelopeError(ValueError):
    """Invalid or non-admissible decision lineage."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _snapshot_from_mapping(value: Mapping[str, Any]) -> CanonicalMarketSnapshot:
    names = {field.name for field in fields(CanonicalMarketSnapshot)}
    payload = {name: value.get(name) for name in names if name in value}
    required = {
        "snapshot_id", "snapshot_hash", "instrument_id", "provider", "provider_symbol",
        "source_ref", "observed_at", "received_at", "created_at", "market_status", "quote_kind",
    }
    missing = sorted(name for name in required if not payload.get(name))
    if missing:
        raise DecisionEnvelopeError("canonical MarketSnapshot missing: " + ",".join(missing))
    try:
        snapshot = CanonicalMarketSnapshot(**payload)
    except TypeError as exc:
        raise DecisionEnvelopeError("invalid canonical MarketSnapshot shape") from exc

    # Rebuild from immutable facts. This independently verifies both snapshot_id
    # and snapshot_hash instead of trusting copied lineage fields.
    rebuilt = build_snapshot(
        instrument_id=snapshot.instrument_id,
        provider=snapshot.provider,
        provider_symbol=snapshot.provider_symbol,
        source_ref=snapshot.source_ref,
        observed_at=snapshot.observed_at,
        received_at=snapshot.received_at,
        created_at=snapshot.created_at,
        market_status=snapshot.market_status,
        quote_kind=snapshot.quote_kind,
        bid=snapshot.bid,
        ask=snapshot.ask,
        last=snapshot.last,
        open=snapshot.open,
        high=snapshot.high,
        low=snapshot.low,
        close=snapshot.close,
        volume=snapshot.volume,
        source_schema=snapshot.source_schema,
    )
    if rebuilt.snapshot_hash != snapshot.snapshot_hash or rebuilt.snapshot_id != snapshot.snapshot_id:
        raise DecisionEnvelopeError("canonical MarketSnapshot identity/hash mismatch")
    return snapshot


@dataclass(frozen=True)
class DecisionEnvelope:
    envelope_id: str
    envelope_hash: str
    engine_id: str
    engine_version: str
    decision_at: str
    native_decision: str
    action: str
    instrument_id: Optional[str]
    market_snapshot_id: Optional[str]
    market_snapshot_hash: Optional[str]
    market_data_policy_id: Optional[str]
    market_data_quality_status: Optional[str]
    risk_assessment_id: str
    risk_assessment_hash: str
    risk_policy_id: str
    risk_policy_version: str
    epistemic_state_id: Optional[str]
    horizon: Optional[str]
    confidence: Any
    position_plan: dict[str, Any]
    authority: dict[str, Any]
    contract_version: str = CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _facts(
    *,
    engine_id: str,
    engine_version: str,
    decision_at: str,
    native_decision: str,
    action: str,
    instrument_id: Optional[str],
    market_snapshot_id: Optional[str],
    market_snapshot_hash: Optional[str],
    market_data_policy_id: Optional[str],
    market_data_quality_status: Optional[str],
    risk_assessment: RiskAssessment,
    epistemic_state_id: Optional[str],
    horizon: Optional[str],
    confidence: Any,
    position_plan: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "engine_id": engine_id,
        "engine_version": engine_version,
        "decision_at": decision_at,
        "native_decision": native_decision,
        "action": action,
        "instrument_id": instrument_id,
        "market_snapshot_id": market_snapshot_id,
        "market_snapshot_hash": market_snapshot_hash,
        "market_data_policy_id": market_data_policy_id,
        "market_data_quality_status": market_data_quality_status,
        "risk_assessment_id": risk_assessment.risk_assessment_id,
        "risk_assessment_hash": risk_assessment.risk_assessment_hash,
        "risk_policy_id": risk_assessment.policy_id,
        "risk_policy_version": risk_assessment.policy_version,
        "epistemic_state_id": epistemic_state_id,
        "horizon": horizon,
        "confidence": confidence,
        "position_plan": dict(position_plan),
        "authority": {
            "decision_authority": "SOURCE_ENGINE",
            "risk_limit_authority": "SOURCE_ENGINE_RISK_POLICY",
            "envelope_decision_authority": False,
            "trade_execution": False,
        },
    }


def build_envelope(
    *,
    engine_id: str,
    engine_version: str,
    decision_at: str | datetime,
    native_decision: str,
    action: str,
    risk_assessment: RiskAssessment,
    position_plan: Mapping[str, Any],
    market_snapshot: Mapping[str, Any] | None = None,
    market_data_quality: Mapping[str, Any] | None = None,
    market_policy: FreshnessPolicy | None = None,
    epistemic_state_id: str | None = None,
    horizon: str | None = None,
    confidence: Any = None,
) -> DecisionEnvelope:
    engine_id = str(engine_id or "").strip()
    engine_version = str(engine_version or "").strip()
    native_decision = str(native_decision or "").strip()
    action = str(action or "").strip().upper()
    if not engine_id or not engine_version or not native_decision:
        raise DecisionEnvelopeError("engine_id, engine_version and native_decision are required")
    if action not in ALLOWED_ACTIONS:
        raise DecisionEnvelopeError(f"unsupported canonical action: {action!r}")

    decision_time = iso_z(decision_at, field="decision_at")
    verify_assessment(risk_assessment)
    require_approved(risk_assessment)
    if risk_assessment.engine_id != engine_id:
        raise DecisionEnvelopeError("RiskPolicy engine_id does not match DecisionEnvelope engine_id")
    if risk_assessment.action != action:
        raise DecisionEnvelopeError("RiskPolicy action does not match DecisionEnvelope action")
    if risk_assessment.assessed_at != decision_time:
        raise DecisionEnvelopeError("risk assessment must be frozen at decision_at")

    instrument_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    snapshot_hash: Optional[str] = None
    market_policy_id: Optional[str] = None
    quality_status: Optional[str] = None

    if action in ECONOMIC_ACTIONS:
        if not isinstance(market_snapshot, Mapping) or not isinstance(market_data_quality, Mapping):
            raise DecisionEnvelopeError("economic decision requires canonical MarketSnapshot and DataQuality")
        if market_policy is None:
            raise DecisionEnvelopeError("economic decision requires engine-specific MarketSnapshot policy")
        snapshot = _snapshot_from_mapping(market_snapshot)
        try:
            lineage = validate_decision_lineage(snapshot, decision_at=decision_time, policy=market_policy)
        except MarketSnapshotError as exc:
            raise DecisionEnvelopeError(str(exc)) from exc
        quality_status = str(market_data_quality.get("status") or "")
        quality_decision = str(market_data_quality.get("decision_status") or "")
        quality_policy = str(market_data_quality.get("policy_id") or "")
        if quality_status != STATUS_OK or quality_decision != DECISION_ALLOWED:
            raise DecisionEnvelopeError("DATA_QUALITY_BLOCKED: source canonical DataQuality is not OK")
        if quality_policy != market_policy.policy_id or lineage.policy_id != market_policy.policy_id:
            raise DecisionEnvelopeError("MarketSnapshot policy lineage mismatch")
        instrument_id = snapshot.instrument_id
        snapshot_id = snapshot.snapshot_id
        snapshot_hash = snapshot.snapshot_hash
        market_policy_id = market_policy.policy_id
    elif market_snapshot is not None or market_data_quality is not None:
        # FLAT may retain an informational snapshot, but PR-C does not require or
        # imply an instrument-specific economic exposure for abstention.
        if isinstance(market_snapshot, Mapping) and market_snapshot.get("instrument_id"):
            instrument_id = str(market_snapshot.get("instrument_id"))

    facts = _facts(
        engine_id=engine_id,
        engine_version=engine_version,
        decision_at=decision_time,
        native_decision=native_decision,
        action=action,
        instrument_id=instrument_id,
        market_snapshot_id=snapshot_id,
        market_snapshot_hash=snapshot_hash,
        market_data_policy_id=market_policy_id,
        market_data_quality_status=quality_status,
        risk_assessment=risk_assessment,
        epistemic_state_id=str(epistemic_state_id) if epistemic_state_id else None,
        horizon=str(horizon) if horizon else None,
        confidence=confidence,
        position_plan=position_plan,
    )
    digest = _sha(facts)
    return DecisionEnvelope(
        envelope_id="dec-" + digest[:24],
        envelope_hash=digest,
        engine_id=engine_id,
        engine_version=engine_version,
        decision_at=decision_time,
        native_decision=native_decision,
        action=action,
        instrument_id=instrument_id,
        market_snapshot_id=snapshot_id,
        market_snapshot_hash=snapshot_hash,
        market_data_policy_id=market_policy_id,
        market_data_quality_status=quality_status,
        risk_assessment_id=risk_assessment.risk_assessment_id,
        risk_assessment_hash=risk_assessment.risk_assessment_hash,
        risk_policy_id=risk_assessment.policy_id,
        risk_policy_version=risk_assessment.policy_version,
        epistemic_state_id=str(epistemic_state_id) if epistemic_state_id else None,
        horizon=str(horizon) if horizon else None,
        confidence=confidence,
        position_plan=dict(position_plan),
        authority=facts["authority"],
    )


def verify_envelope(envelope: DecisionEnvelope) -> None:
    if envelope.contract_version != CONTRACT_VERSION:
        raise DecisionEnvelopeError("unsupported DecisionEnvelope contract version")
    # Risk IDs/hashes are already bound into the envelope; full RiskAssessment
    # verification is performed before construction at the source persistence boundary.
    facts = {
        "contract_version": CONTRACT_VERSION,
        "engine_id": envelope.engine_id,
        "engine_version": envelope.engine_version,
        "decision_at": envelope.decision_at,
        "native_decision": envelope.native_decision,
        "action": envelope.action,
        "instrument_id": envelope.instrument_id,
        "market_snapshot_id": envelope.market_snapshot_id,
        "market_snapshot_hash": envelope.market_snapshot_hash,
        "market_data_policy_id": envelope.market_data_policy_id,
        "market_data_quality_status": envelope.market_data_quality_status,
        "risk_assessment_id": envelope.risk_assessment_id,
        "risk_assessment_hash": envelope.risk_assessment_hash,
        "risk_policy_id": envelope.risk_policy_id,
        "risk_policy_version": envelope.risk_policy_version,
        "epistemic_state_id": envelope.epistemic_state_id,
        "horizon": envelope.horizon,
        "confidence": envelope.confidence,
        "position_plan": dict(envelope.position_plan),
        "authority": dict(envelope.authority),
    }
    digest = _sha(facts)
    if envelope.envelope_hash != digest:
        raise DecisionEnvelopeError("DecisionEnvelope hash mismatch")
    if envelope.envelope_id != "dec-" + digest[:24]:
        raise DecisionEnvelopeError("DecisionEnvelope id mismatch")


def envelope_from_mapping(value: Mapping[str, Any]) -> DecisionEnvelope:
    names = {field.name for field in fields(DecisionEnvelope)}
    payload = {name: value.get(name) for name in names if name in value}
    try:
        envelope = DecisionEnvelope(**payload)
    except TypeError as exc:
        raise DecisionEnvelopeError("invalid DecisionEnvelope shape") from exc
    verify_envelope(envelope)
    return envelope
