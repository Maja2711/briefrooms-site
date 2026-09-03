#!/usr/bin/env python3
"""Canonical, deterministic EpistemicState contract for BriefRooms.

PR32A does not create a second belief engine. It freezes an already-computed
Belief Core Epistemic State projection into one canonical, hash-addressed state
while preserving the reversible path state -> belief -> evidence -> observation
-> source.

The contract has no decision, risk, execution, calibration or writeback
authority.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

CONTRACT_VERSION = "briefrooms-epistemic-state-v1"
UPSTREAM_CONTRACT_VERSION = "belief-epistemic-state-v1"
BUILDER_ID = "briefrooms-canonical-epistemic-state-builder"
BUILDER_VERSION = "pr32a-v1"
PROVENANCE_PATH = "state->belief->evidence->observation->source"


class CanonicalEpistemicStateError(ValueError):
    """Invalid canonical epistemic state or provenance lineage."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_aware(value: str | datetime, *, field: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise CanonicalEpistemicStateError(f"{field} is required")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise CanonicalEpistemicStateError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if dt.tzinfo is None:
        raise CanonicalEpistemicStateError(f"{field} must include an explicit timezone")
    return dt.astimezone(timezone.utc)


def iso_z(value: str | datetime, *, field: str = "timestamp") -> str:
    return parse_aware(value, field=field).isoformat(timespec="microseconds").replace("+00:00", "Z")


def bounded_probability(value: Any, *, field: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise CanonicalEpistemicStateError(f"{field} must be numeric") from exc
    if not math.isfinite(out) or out < 0.0 or out > 1.0:
        raise CanonicalEpistemicStateError(f"{field} must be in [0,1]")
    return round(out, 6)


def optional_probability(value: Any, *, field: str) -> Optional[float]:
    if value is None:
        return None
    return bounded_probability(value, field=field)


def optional_delta(value: Any, *, field: str) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise CanonicalEpistemicStateError(f"{field} must be numeric") from exc
    if not math.isfinite(out) or out < -1.0 or out > 1.0:
        raise CanonicalEpistemicStateError(f"{field} must be in [-1,1]")
    return round(out, 6)


@dataclass(frozen=True)
class CanonicalObservationRef:
    observation_id: str
    observation_hash: str
    source: str
    source_ref: str
    observed_at: str
    metric: Optional[str] = None
    entity: Optional[str] = None
    value: Any = None
    unit: Optional[str] = None
    content_hash: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CanonicalEvidenceRef:
    evidence_id: str
    evidence_hash: str
    belief_id: str
    source: str
    source_ref: str
    observed_at: str
    direction: int
    strength: float
    reliability: float
    observation_ids: Tuple[str, ...]
    derived_from: Tuple[str, ...] = ()
    evidence_type: Optional[str] = None
    independence_cluster: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CanonicalContribution:
    contributor_type: str
    contributor_id: str
    signed_probability_delta: float
    direction: int
    source_ref: Optional[str] = None
    observation_id: Optional[str] = None


@dataclass(frozen=True)
class CanonicalBeliefState:
    belief_id: str
    belief_hash: str
    projection_state_id: str
    topic: str
    entity: str
    domain: str
    probability: float
    confidence: float
    previous_probability: Optional[float]
    delta_probability: Optional[float]
    contradiction: float
    freshness: float
    audit_status: str
    as_of: str
    member_belief_ids: Tuple[str, ...]
    evidence_ids: Tuple[str, ...]
    contributions: Tuple[CanonicalContribution, ...]
    dominant_support_evidence_ids: Tuple[str, ...]
    dominant_opposition_evidence_ids: Tuple[str, ...]
    drilldown_required: bool
    drilldown_reasons: Tuple[str, ...]
    importance: Optional[float] = None
    verify_later: bool = False
    research_required: bool = False
    expected_outcome: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EpistemicAuthority:
    authoritative_source: str = "BELIEF_CORE_EPISTEMIC_PROJECTION"
    aggregate_authority_policy: str = "aggregate-authority-v1"
    decision_authority: bool = False
    risk_limit_authority: bool = False
    trade_execution_authority: bool = False
    belief_core_writeback_enabled: bool = False
    llm_override_enabled: bool = False
    automatic_tuning_enabled: bool = False


@dataclass(frozen=True)
class CanonicalEpistemicState:
    state_id: str
    state_hash: str
    as_of: str
    source_projection_hash: str
    belief_core_state_hash: str
    observations_source_hash: str
    beliefs: Tuple[CanonicalBeliefState, ...]
    evidence: Tuple[CanonicalEvidenceRef, ...]
    observations: Tuple[CanonicalObservationRef, ...]
    builder_id: str = BUILDER_ID
    builder_version: str = BUILDER_VERSION
    provenance_path: str = PROVENANCE_PATH
    availability_basis: str = "OBSERVED_AT"
    authority: EpistemicAuthority = field(default_factory=EpistemicAuthority)
    source_contract_version: str = UPSTREAM_CONTRACT_VERSION
    contract_version: str = CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def observation_facts(observation: CanonicalObservationRef) -> dict[str, Any]:
    return {
        "observation_id": observation.observation_id,
        "source": observation.source,
        "source_ref": observation.source_ref,
        "observed_at": observation.observed_at,
        "metric": observation.metric,
        "entity": observation.entity,
        "value": observation.value,
        "unit": observation.unit,
        "content_hash": observation.content_hash,
    }


def evidence_facts(evidence: CanonicalEvidenceRef) -> dict[str, Any]:
    return {
        "evidence_id": evidence.evidence_id,
        "belief_id": evidence.belief_id,
        "source": evidence.source,
        "source_ref": evidence.source_ref,
        "observed_at": evidence.observed_at,
        "direction": evidence.direction,
        "strength": evidence.strength,
        "reliability": evidence.reliability,
        "observation_ids": list(evidence.observation_ids),
        "derived_from": list(evidence.derived_from),
        "evidence_type": evidence.evidence_type,
        "independence_cluster": evidence.independence_cluster,
    }


def belief_facts(belief: CanonicalBeliefState) -> dict[str, Any]:
    return {
        "belief_id": belief.belief_id,
        "projection_state_id": belief.projection_state_id,
        "topic": belief.topic,
        "entity": belief.entity,
        "domain": belief.domain,
        "probability": belief.probability,
        "confidence": belief.confidence,
        "previous_probability": belief.previous_probability,
        "delta_probability": belief.delta_probability,
        "contradiction": belief.contradiction,
        "freshness": belief.freshness,
        "audit_status": belief.audit_status,
        "as_of": belief.as_of,
        "member_belief_ids": list(belief.member_belief_ids),
        "evidence_ids": list(belief.evidence_ids),
        "contributions": [asdict(row) for row in belief.contributions],
        "dominant_support_evidence_ids": list(belief.dominant_support_evidence_ids),
        "dominant_opposition_evidence_ids": list(belief.dominant_opposition_evidence_ids),
        "drilldown_required": belief.drilldown_required,
        "drilldown_reasons": list(belief.drilldown_reasons),
        "importance": belief.importance,
        "verify_later": belief.verify_later,
        "research_required": belief.research_required,
        "expected_outcome": belief.expected_outcome,
    }


def state_facts(state: CanonicalEpistemicState) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "source_contract_version": state.source_contract_version,
        "as_of": state.as_of,
        "source_projection_hash": state.source_projection_hash,
        "belief_core_state_hash": state.belief_core_state_hash,
        "observations_source_hash": state.observations_source_hash,
        "builder_id": state.builder_id,
        "builder_version": state.builder_version,
        "beliefs": [belief.to_dict() for belief in state.beliefs],
        "evidence": [row.to_dict() for row in state.evidence],
        "observations": [row.to_dict() for row in state.observations],
        "provenance_path": state.provenance_path,
        "availability_basis": state.availability_basis,
        "authority": asdict(state.authority),
    }


def verify_state(state: CanonicalEpistemicState) -> None:
    if state.contract_version != CONTRACT_VERSION:
        raise CanonicalEpistemicStateError("unsupported CanonicalEpistemicState contract version")
    if state.source_contract_version != UPSTREAM_CONTRACT_VERSION:
        raise CanonicalEpistemicStateError("unsupported upstream EpistemicState contract version")
    if state.provenance_path != PROVENANCE_PATH:
        raise CanonicalEpistemicStateError("invalid provenance path")
    if state.builder_id != BUILDER_ID or state.builder_version != BUILDER_VERSION:
        raise CanonicalEpistemicStateError("unsupported CanonicalEpistemicState builder lineage")
    cutoff = parse_aware(state.as_of, field="as_of")
    if state.availability_basis != "OBSERVED_AT":
        raise CanonicalEpistemicStateError("unsupported availability basis")
    authority = state.authority
    if any((authority.decision_authority, authority.risk_limit_authority, authority.trade_execution_authority,
            authority.belief_core_writeback_enabled, authority.llm_override_enabled, authority.automatic_tuning_enabled)):
        raise CanonicalEpistemicStateError("canonical EpistemicState must remain read-only and non-authoritative for actions")

    observation_by_id: dict[str, CanonicalObservationRef] = {}
    for observation in state.observations:
        if observation.observation_id in observation_by_id:
            raise CanonicalEpistemicStateError(f"duplicate observation_id: {observation.observation_id}")
        if sha256_digest(observation_facts(observation)) != observation.observation_hash:
            raise CanonicalEpistemicStateError(f"observation hash mismatch: {observation.observation_id}")
        if parse_aware(observation.observed_at, field=f"observation[{observation.observation_id}].observed_at") > cutoff:
            raise CanonicalEpistemicStateError("future observation exceeds EpistemicState as_of")
        observation_by_id[observation.observation_id] = observation

    evidence_by_id: dict[str, CanonicalEvidenceRef] = {}
    for row in state.evidence:
        if row.evidence_id in evidence_by_id:
            raise CanonicalEpistemicStateError(f"duplicate evidence_id: {row.evidence_id}")
        if row.direction not in {-1, 1}:
            raise CanonicalEpistemicStateError(f"evidence direction must be -1 or 1: {row.evidence_id}")
        bounded_probability(row.strength, field=f"evidence[{row.evidence_id}].strength")
        bounded_probability(row.reliability, field=f"evidence[{row.evidence_id}].reliability")
        if sha256_digest(evidence_facts(row)) != row.evidence_hash:
            raise CanonicalEpistemicStateError(f"evidence hash mismatch: {row.evidence_id}")
        evidence_time = parse_aware(row.observed_at, field=f"evidence[{row.evidence_id}].observed_at")
        if evidence_time > cutoff:
            raise CanonicalEpistemicStateError("future evidence exceeds EpistemicState as_of")
        if not row.observation_ids:
            raise CanonicalEpistemicStateError(f"evidence has no reversible observation lineage: {row.evidence_id}")
        for observation_id in row.observation_ids:
            if observation_id not in observation_by_id:
                raise CanonicalEpistemicStateError(f"missing observation lineage: {observation_id}")
            observation_time = parse_aware(observation_by_id[observation_id].observed_at, field="observation.observed_at")
            if observation_time > evidence_time:
                raise CanonicalEpistemicStateError("observation occurs after evidence that references it")
        evidence_by_id[row.evidence_id] = row

    belief_ids: set[str] = set()
    for belief in state.beliefs:
        if belief.belief_id in belief_ids:
            raise CanonicalEpistemicStateError(f"duplicate belief_id: {belief.belief_id}")
        bounded_probability(belief.probability, field=f"belief[{belief.belief_id}].probability")
        bounded_probability(belief.confidence, field=f"belief[{belief.belief_id}].confidence")
        bounded_probability(belief.contradiction, field=f"belief[{belief.belief_id}].contradiction")
        bounded_probability(belief.freshness, field=f"belief[{belief.belief_id}].freshness")
        optional_probability(belief.previous_probability, field=f"belief[{belief.belief_id}].previous_probability")
        optional_delta(belief.delta_probability, field=f"belief[{belief.belief_id}].delta_probability")
        if belief.importance is not None:
            bounded_probability(belief.importance, field=f"belief[{belief.belief_id}].importance")
        belief_time = parse_aware(belief.as_of, field=f"belief[{belief.belief_id}].as_of")
        if belief_time > cutoff:
            raise CanonicalEpistemicStateError("future belief state exceeds EpistemicState as_of")
        if sha256_digest(belief_facts(belief)) != belief.belief_hash:
            raise CanonicalEpistemicStateError(f"belief hash mismatch: {belief.belief_id}")
        for evidence_id in belief.evidence_ids:
            if evidence_id not in evidence_by_id:
                raise CanonicalEpistemicStateError(f"missing evidence lineage: {evidence_id}")
            if evidence_by_id[evidence_id].belief_id not in belief.member_belief_ids:
                raise CanonicalEpistemicStateError(f"evidence/belief lineage mismatch: {evidence_id}")
            evidence_time = parse_aware(evidence_by_id[evidence_id].observed_at, field="evidence.observed_at")
            if evidence_time > belief_time:
                raise CanonicalEpistemicStateError("evidence occurs after belief as_of")
        for contribution in belief.contributions:
            if contribution.contributor_type != "evidence":
                raise CanonicalEpistemicStateError("canonical v1 supports evidence contributions only")
            if contribution.contributor_id not in evidence_by_id:
                raise CanonicalEpistemicStateError(f"contribution references missing evidence: {contribution.contributor_id}")
            if contribution.direction not in {-1, 1}:
                raise CanonicalEpistemicStateError("contribution direction must be -1 or 1")
            if not math.isfinite(float(contribution.signed_probability_delta)):
                raise CanonicalEpistemicStateError("contribution delta must be finite")
            if contribution.observation_id and contribution.observation_id not in observation_by_id:
                raise CanonicalEpistemicStateError("contribution references missing observation")
        belief_ids.add(belief.belief_id)

    digest = sha256_digest(state_facts(state))
    if state.state_hash != digest:
        raise CanonicalEpistemicStateError("CanonicalEpistemicState hash mismatch")
    if state.state_id != "eps-" + digest[:24]:
        raise CanonicalEpistemicStateError("CanonicalEpistemicState id mismatch")
