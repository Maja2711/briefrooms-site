#!/usr/bin/env python3
"""Canonical verification/outcome binding for BriefRooms EpistemicState.

PR32B binds a later outcome to the exact frozen epistemic lineage produced by
PR32A.  It is measurement-only: it cannot mutate Belief Core, decisions, risk,
execution, evidence weights or model parameters.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Tuple

STATE_CONTRACT_VERSION = "briefrooms-epistemic-state-v1"
TARGET_CONTRACT_VERSION = "briefrooms-epistemic-verification-target-v1"
VERIFICATION_CONTRACT_VERSION = "briefrooms-epistemic-verification-v1"
BUILDER_ID = "briefrooms-canonical-epistemic-verification-builder"
BUILDER_VERSION = "pr32b-v1"


class CanonicalEpistemicVerificationError(ValueError):
    """Invalid verification target, outcome, or immutable lineage binding."""


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
            raise CanonicalEpistemicVerificationError(f"{field} is required")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise CanonicalEpistemicVerificationError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if dt.tzinfo is None:
        raise CanonicalEpistemicVerificationError(f"{field} must include an explicit timezone")
    return dt.astimezone(timezone.utc)


def iso_z(value: str | datetime, *, field: str) -> str:
    return parse_aware(value, field=field).isoformat(timespec="microseconds").replace("+00:00", "Z")


def probability(value: Any, *, field: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise CanonicalEpistemicVerificationError(f"{field} must be numeric") from exc
    if not math.isfinite(out) or out < 0.0 or out > 1.0:
        raise CanonicalEpistemicVerificationError(f"{field} must be in [0,1]")
    return round(out, 6)


def _nonempty(value: Any, *, field: str) -> str:
    out = str(value or "").strip()
    if not out:
        raise CanonicalEpistemicVerificationError(f"{field} is required")
    return out


def _hash(value: Any, *, field: str) -> str:
    out = _nonempty(value, field=field)
    if len(out) != 64 or any(ch not in "0123456789abcdef" for ch in out.lower()):
        raise CanonicalEpistemicVerificationError(f"{field} must be a SHA-256 hex digest")
    return out.lower()


def _log_loss(p: float, outcome: bool) -> float:
    p = max(1e-9, min(1.0 - 1e-9, float(p)))
    y = 1.0 if outcome else 0.0
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def _horizon_bucket(hours: float) -> str:
    if hours <= 24:
        return "<=1d"
    if hours <= 72:
        return "1-3d"
    if hours <= 168:
        return "3-7d"
    if hours <= 720:
        return "1w-1m"
    if hours <= 2160:
        return "1-3m"
    return ">3m"


@dataclass(frozen=True)
class EvidenceBinding:
    evidence_id: str
    evidence_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VerificationAuthority:
    decision_authority: bool = False
    risk_limit_authority: bool = False
    trade_execution_authority: bool = False
    belief_core_writeback_enabled: bool = False
    evidence_weight_writeback_enabled: bool = False
    automatic_tuning_enabled: bool = False
    llm_override_enabled: bool = False


@dataclass(frozen=True)
class EpistemicVerificationTarget:
    target_id: str
    target_hash: str
    state_id: str
    state_hash: str
    state_as_of: str
    belief_id: str
    belief_hash: str
    belief_as_of: str
    predicted_probability: float
    forecast_confidence: float
    domain: str
    entity: str
    evidence_bindings: Tuple[EvidenceBinding, ...]
    expected_outcome: Optional[str] = None
    builder_id: str = BUILDER_ID
    builder_version: str = BUILDER_VERSION
    authority: VerificationAuthority = field(default_factory=VerificationAuthority)
    source_contract_version: str = STATE_CONTRACT_VERSION
    contract_version: str = TARGET_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CanonicalEpistemicVerification:
    verification_id: str
    verification_hash: str
    target_id: str
    target_hash: str
    state_id: str
    state_hash: str
    state_as_of: str
    belief_id: str
    belief_hash: str
    belief_as_of: str
    predicted_probability: float
    forecast_confidence: float
    domain: str
    entity: str
    evidence_bindings: Tuple[EvidenceBinding, ...]
    outcome: bool
    verified_at: str
    outcome_source: str
    outcome_ref: Optional[str]
    brier_score: float
    log_loss: float
    note: str = ""
    calibration_eligible: bool = True
    builder_id: str = BUILDER_ID
    builder_version: str = BUILDER_VERSION
    authority: VerificationAuthority = field(default_factory=VerificationAuthority)
    target_contract_version: str = TARGET_CONTRACT_VERSION
    contract_version: str = VERIFICATION_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _assert_read_only(authority: VerificationAuthority) -> None:
    if any(asdict(authority).values()):
        raise CanonicalEpistemicVerificationError("verification contract must remain read-only and non-authoritative")


def target_facts(target: EpistemicVerificationTarget) -> dict[str, Any]:
    return {
        "contract_version": TARGET_CONTRACT_VERSION,
        "source_contract_version": target.source_contract_version,
        "state_id": target.state_id,
        "state_hash": target.state_hash,
        "state_as_of": target.state_as_of,
        "belief_id": target.belief_id,
        "belief_hash": target.belief_hash,
        "belief_as_of": target.belief_as_of,
        "predicted_probability": target.predicted_probability,
        "forecast_confidence": target.forecast_confidence,
        "domain": target.domain,
        "entity": target.entity,
        "evidence_bindings": [row.to_dict() for row in target.evidence_bindings],
        "expected_outcome": target.expected_outcome,
        "builder_id": target.builder_id,
        "builder_version": target.builder_version,
        "authority": asdict(target.authority),
    }


def verification_facts(row: CanonicalEpistemicVerification) -> dict[str, Any]:
    return {
        "contract_version": VERIFICATION_CONTRACT_VERSION,
        "target_contract_version": row.target_contract_version,
        "target_id": row.target_id,
        "target_hash": row.target_hash,
        "state_id": row.state_id,
        "state_hash": row.state_hash,
        "state_as_of": row.state_as_of,
        "belief_id": row.belief_id,
        "belief_hash": row.belief_hash,
        "belief_as_of": row.belief_as_of,
        "predicted_probability": row.predicted_probability,
        "forecast_confidence": row.forecast_confidence,
        "domain": row.domain,
        "entity": row.entity,
        "evidence_bindings": [item.to_dict() for item in row.evidence_bindings],
        "outcome": row.outcome,
        "verified_at": row.verified_at,
        "outcome_source": row.outcome_source,
        "outcome_ref": row.outcome_ref,
        "brier_score": row.brier_score,
        "log_loss": row.log_loss,
        "note": row.note,
        "calibration_eligible": row.calibration_eligible,
        "builder_id": row.builder_id,
        "builder_version": row.builder_version,
        "authority": asdict(row.authority),
    }


def build_target(state: Mapping[str, Any], belief_id: str, *, require_verify_later: bool = True) -> EpistemicVerificationTarget:
    if state.get("contract_version") != STATE_CONTRACT_VERSION:
        raise CanonicalEpistemicVerificationError("unsupported CanonicalEpistemicState contract version")
    state_id = _nonempty(state.get("state_id"), field="state_id")
    state_hash = _hash(state.get("state_hash"), field="state_hash")
    state_as_of = iso_z(state.get("as_of"), field="state.as_of")

    beliefs = {str(row.get("belief_id")): row for row in (state.get("beliefs") or [])}
    if belief_id not in beliefs:
        raise CanonicalEpistemicVerificationError(f"belief not present in canonical state: {belief_id}")
    belief = beliefs[belief_id]
    if require_verify_later and not bool(belief.get("verify_later", False)):
        raise CanonicalEpistemicVerificationError(f"belief is not marked verify_later: {belief_id}")

    evidence_by_id = {str(row.get("evidence_id")): row for row in (state.get("evidence") or [])}
    bindings = []
    for evidence_id in sorted({str(x) for x in (belief.get("evidence_ids") or [])}):
        if evidence_id not in evidence_by_id:
            raise CanonicalEpistemicVerificationError(f"missing canonical evidence binding: {evidence_id}")
        bindings.append(EvidenceBinding(evidence_id, _hash(evidence_by_id[evidence_id].get("evidence_hash"), field=f"evidence[{evidence_id}].evidence_hash")))

    draft = EpistemicVerificationTarget(
        target_id="",
        target_hash="",
        state_id=state_id,
        state_hash=state_hash,
        state_as_of=state_as_of,
        belief_id=belief_id,
        belief_hash=_hash(belief.get("belief_hash"), field=f"belief[{belief_id}].belief_hash"),
        belief_as_of=iso_z(belief.get("as_of"), field=f"belief[{belief_id}].as_of"),
        predicted_probability=probability(belief.get("probability"), field=f"belief[{belief_id}].probability"),
        forecast_confidence=probability(belief.get("confidence"), field=f"belief[{belief_id}].confidence"),
        domain=_nonempty(belief.get("domain") or "general", field="domain"),
        entity=_nonempty(belief.get("entity") or "GLOBAL", field="entity"),
        evidence_bindings=tuple(bindings),
        expected_outcome=(str(belief.get("expected_outcome")) if belief.get("expected_outcome") is not None else None),
    )
    digest = sha256_digest(target_facts(draft))
    target = replace(draft, target_id=f"epvt-{digest[:24]}", target_hash=digest)
    verify_target(target)
    return target


def build_targets(state: Mapping[str, Any]) -> tuple[EpistemicVerificationTarget, ...]:
    ids = sorted(str(row.get("belief_id")) for row in (state.get("beliefs") or []) if bool(row.get("verify_later", False)))
    return tuple(build_target(state, belief_id) for belief_id in ids)


def verify_target(target: EpistemicVerificationTarget) -> None:
    if target.contract_version != TARGET_CONTRACT_VERSION or target.source_contract_version != STATE_CONTRACT_VERSION:
        raise CanonicalEpistemicVerificationError("unsupported verification target contract version")
    if target.builder_id != BUILDER_ID or target.builder_version != BUILDER_VERSION:
        raise CanonicalEpistemicVerificationError("unsupported verification target builder lineage")
    _assert_read_only(target.authority)
    _hash(target.state_hash, field="state_hash")
    _hash(target.belief_hash, field="belief_hash")
    parse_aware(target.state_as_of, field="state_as_of")
    parse_aware(target.belief_as_of, field="belief_as_of")
    probability(target.predicted_probability, field="predicted_probability")
    probability(target.forecast_confidence, field="forecast_confidence")
    if tuple(sorted(x.evidence_id for x in target.evidence_bindings)) != tuple(x.evidence_id for x in target.evidence_bindings):
        raise CanonicalEpistemicVerificationError("evidence bindings must be sorted deterministically")
    if len({x.evidence_id for x in target.evidence_bindings}) != len(target.evidence_bindings):
        raise CanonicalEpistemicVerificationError("duplicate evidence binding")
    for item in target.evidence_bindings:
        _nonempty(item.evidence_id, field="evidence_id")
        _hash(item.evidence_hash, field=f"evidence[{item.evidence_id}].evidence_hash")
    expected_hash = sha256_digest(target_facts(target))
    if target.target_hash != expected_hash:
        raise CanonicalEpistemicVerificationError("verification target hash mismatch")
    if target.target_id != f"epvt-{expected_hash[:24]}":
        raise CanonicalEpistemicVerificationError("verification target id mismatch")


def target_from_dict(payload: Mapping[str, Any]) -> EpistemicVerificationTarget:
    authority = VerificationAuthority(**dict(payload.get("authority") or {}))
    target = EpistemicVerificationTarget(
        target_id=str(payload["target_id"]), target_hash=str(payload["target_hash"]),
        state_id=str(payload["state_id"]), state_hash=str(payload["state_hash"]), state_as_of=str(payload["state_as_of"]),
        belief_id=str(payload["belief_id"]), belief_hash=str(payload["belief_hash"]), belief_as_of=str(payload["belief_as_of"]),
        predicted_probability=float(payload["predicted_probability"]), forecast_confidence=float(payload["forecast_confidence"]),
        domain=str(payload.get("domain") or "general"), entity=str(payload.get("entity") or "GLOBAL"),
        evidence_bindings=tuple(EvidenceBinding(str(x["evidence_id"]), str(x["evidence_hash"])) for x in (payload.get("evidence_bindings") or [])),
        expected_outcome=payload.get("expected_outcome"), builder_id=str(payload.get("builder_id") or BUILDER_ID),
        builder_version=str(payload.get("builder_version") or BUILDER_VERSION), authority=authority,
        source_contract_version=str(payload.get("source_contract_version") or STATE_CONTRACT_VERSION),
        contract_version=str(payload.get("contract_version") or TARGET_CONTRACT_VERSION),
    )
    verify_target(target)
    return target


def resolve_target(target: EpistemicVerificationTarget, *, outcome: bool, verified_at: str | datetime,
                   outcome_source: str, outcome_ref: Optional[str] = None, note: str = "") -> CanonicalEpistemicVerification:
    verify_target(target)
    verified = iso_z(verified_at, field="verified_at")
    if parse_aware(verified, field="verified_at") <= parse_aware(target.state_as_of, field="state_as_of"):
        raise CanonicalEpistemicVerificationError("verification must occur after the frozen EpistemicState as_of")
    source = _nonempty(outcome_source, field="outcome_source")
    p = target.predicted_probability
    y = 1.0 if bool(outcome) else 0.0
    draft = CanonicalEpistemicVerification(
        verification_id="", verification_hash="", target_id=target.target_id, target_hash=target.target_hash,
        state_id=target.state_id, state_hash=target.state_hash, state_as_of=target.state_as_of,
        belief_id=target.belief_id, belief_hash=target.belief_hash, belief_as_of=target.belief_as_of,
        predicted_probability=p, forecast_confidence=target.forecast_confidence, domain=target.domain, entity=target.entity,
        evidence_bindings=target.evidence_bindings, outcome=bool(outcome), verified_at=verified,
        outcome_source=source, outcome_ref=(str(outcome_ref) if outcome_ref is not None else None),
        brier_score=round((p - y) ** 2, 6), log_loss=round(_log_loss(p, bool(outcome)), 6), note=str(note or ""),
    )
    digest = sha256_digest(verification_facts(draft))
    row = replace(draft, verification_id=f"epvv-{digest[:24]}", verification_hash=digest)
    verify_verification(row)
    return row


def verify_verification(row: CanonicalEpistemicVerification) -> None:
    if row.contract_version != VERIFICATION_CONTRACT_VERSION or row.target_contract_version != TARGET_CONTRACT_VERSION:
        raise CanonicalEpistemicVerificationError("unsupported canonical verification contract version")
    if row.builder_id != BUILDER_ID or row.builder_version != BUILDER_VERSION:
        raise CanonicalEpistemicVerificationError("unsupported canonical verification builder lineage")
    _assert_read_only(row.authority)
    if parse_aware(row.verified_at, field="verified_at") <= parse_aware(row.state_as_of, field="state_as_of"):
        raise CanonicalEpistemicVerificationError("verification is not later than the frozen state")
    p = probability(row.predicted_probability, field="predicted_probability")
    y = 1.0 if row.outcome else 0.0
    expected_brier = round((p - y) ** 2, 6)
    expected_log = round(_log_loss(p, row.outcome), 6)
    if round(float(row.brier_score), 6) != expected_brier or round(float(row.log_loss), 6) != expected_log:
        raise CanonicalEpistemicVerificationError("verification scoring mismatch")
    for value, field_name in ((row.state_hash, "state_hash"), (row.belief_hash, "belief_hash"), (row.target_hash, "target_hash")):
        _hash(value, field=field_name)
    for item in row.evidence_bindings:
        _hash(item.evidence_hash, field=f"evidence[{item.evidence_id}].evidence_hash")
    expected_hash = sha256_digest(verification_facts(row))
    if row.verification_hash != expected_hash:
        raise CanonicalEpistemicVerificationError("canonical verification hash mismatch")
    if row.verification_id != f"epvv-{expected_hash[:24]}":
        raise CanonicalEpistemicVerificationError("canonical verification id mismatch")


def verification_from_dict(payload: Mapping[str, Any]) -> CanonicalEpistemicVerification:
    authority = VerificationAuthority(**dict(payload.get("authority") or {}))
    row = CanonicalEpistemicVerification(
        verification_id=str(payload["verification_id"]), verification_hash=str(payload["verification_hash"]),
        target_id=str(payload["target_id"]), target_hash=str(payload["target_hash"]), state_id=str(payload["state_id"]),
        state_hash=str(payload["state_hash"]), state_as_of=str(payload["state_as_of"]), belief_id=str(payload["belief_id"]),
        belief_hash=str(payload["belief_hash"]), belief_as_of=str(payload["belief_as_of"]),
        predicted_probability=float(payload["predicted_probability"]), forecast_confidence=float(payload["forecast_confidence"]),
        domain=str(payload.get("domain") or "general"), entity=str(payload.get("entity") or "GLOBAL"),
        evidence_bindings=tuple(EvidenceBinding(str(x["evidence_id"]), str(x["evidence_hash"])) for x in (payload.get("evidence_bindings") or [])),
        outcome=bool(payload["outcome"]), verified_at=str(payload["verified_at"]), outcome_source=str(payload["outcome_source"]),
        outcome_ref=payload.get("outcome_ref"), brier_score=float(payload["brier_score"]), log_loss=float(payload["log_loss"]),
        note=str(payload.get("note") or ""), calibration_eligible=bool(payload.get("calibration_eligible", True)),
        builder_id=str(payload.get("builder_id") or BUILDER_ID), builder_version=str(payload.get("builder_version") or BUILDER_VERSION),
        authority=authority, target_contract_version=str(payload.get("target_contract_version") or TARGET_CONTRACT_VERSION),
        contract_version=str(payload.get("contract_version") or VERIFICATION_CONTRACT_VERSION),
    )
    verify_verification(row)
    return row


def calibration_record(row: CanonicalEpistemicVerification) -> dict[str, Any]:
    """Adapt a verified canonical target to the existing Belief Calibration input shape."""
    verify_verification(row)
    start = parse_aware(row.belief_as_of, field="belief_as_of")
    end = parse_aware(row.verified_at, field="verified_at")
    hours = max(0.0, (end - start).total_seconds() / 3600.0)
    evidence_snapshot = [
        {"evidence_id": x.evidence_id, "evidence_hash": x.evidence_hash, "source": "canonical_epistemic_lineage",
         "evidence_type": "canonical_binding", "direction": 1, "effective_mass": 0.0, "reliability": 0.0}
        for x in row.evidence_bindings
    ]
    return {
        "verification_id": row.verification_id,
        "forecast_id": row.target_id,
        "forecast_set_id": row.state_id,
        "belief_id": row.belief_id,
        "predicted_probability": row.predicted_probability,
        "forecast_confidence": row.forecast_confidence,
        "outcome": row.outcome,
        "forecast_at": row.belief_as_of,
        "target_at": row.verified_at,
        "verified_at": row.verified_at,
        "horizon_hours": round(hours, 6),
        "horizon_bucket": _horizon_bucket(hours),
        "domain": row.domain,
        "entity": row.entity,
        "regime": "canonical_epistemic",
        "alternative_group": None,
        "outcome_rule": "canonical_later_verification",
        "outcome_source": row.outcome_source,
        "outcome_ref": row.outcome_ref,
        "brier_score": row.brier_score,
        "log_loss": row.log_loss,
        "evidence_snapshot": evidence_snapshot,
        "calibration_eligible": row.calibration_eligible,
        "legacy": False,
        "note": row.note,
        "canonical_state_id": row.state_id,
        "canonical_state_hash": row.state_hash,
        "canonical_belief_hash": row.belief_hash,
    }


def calibration_records(rows: Iterable[CanonicalEpistemicVerification]) -> list[dict[str, Any]]:
    return [calibration_record(row) for row in rows]
