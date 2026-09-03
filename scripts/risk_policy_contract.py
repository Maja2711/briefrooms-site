#!/usr/bin/env python3
"""Shared *shape* for engine-owned BriefRooms risk policies.

PR-C deliberately standardizes only the assessment contract. It does not own
any trading limit. Every engine keeps its own policy values and evaluator;
this module merely gives those evaluators one deterministic, auditable output.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

CONTRACT_VERSION = "briefrooms-risk-assessment-v1"
STATUS_APPROVED = "APPROVED"
STATUS_BLOCKED = "BLOCKED"
STATUS_NO_POSITION_RISK = "NO_POSITION_RISK"
APPROVED_STATUSES = {STATUS_APPROVED, STATUS_NO_POSITION_RISK}


class RiskPolicyError(ValueError):
    """Invalid risk-policy input, output or blocked decision."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _iso_z(value: str | datetime) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise RiskPolicyError("assessed_at is required")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise RiskPolicyError("assessed_at must be ISO-8601") from exc
    if dt.tzinfo is None:
        raise RiskPolicyError("assessed_at must include an explicit timezone")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class RiskCheck:
    check_id: str
    passed: bool
    observed: Any = None
    limit: Any = None
    operator: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RiskAssessment:
    risk_assessment_id: str
    risk_assessment_hash: str
    engine_id: str
    policy_id: str
    policy_version: str
    policy_fingerprint: str
    assessed_at: str
    action: str
    status: str
    checks: tuple[RiskCheck, ...]
    contract_version: str = CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["checks"] = [check.to_dict() for check in self.checks]
        return value


def _facts(
    *,
    engine_id: str,
    policy_id: str,
    policy_version: str,
    policy_fingerprint: str,
    assessed_at: str,
    action: str,
    status: str,
    checks: Iterable[RiskCheck],
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "engine_id": engine_id,
        "policy_id": policy_id,
        "policy_version": policy_version,
        "policy_fingerprint": policy_fingerprint,
        "assessed_at": assessed_at,
        "action": action,
        "status": status,
        "checks": [check.to_dict() for check in checks],
    }


def build_assessment(
    *,
    engine_id: str,
    policy_id: str,
    policy_version: str,
    policy_inputs: Mapping[str, Any],
    assessed_at: str | datetime,
    action: str,
    checks: Iterable[RiskCheck] = (),
) -> RiskAssessment:
    engine_id = str(engine_id or "").strip()
    policy_id = str(policy_id or "").strip()
    policy_version = str(policy_version or "").strip()
    action = str(action or "").strip().upper()
    if not engine_id or not policy_id or not policy_version:
        raise RiskPolicyError("engine_id, policy_id and policy_version are required")
    if not action:
        raise RiskPolicyError("action is required")

    normalized_checks = tuple(checks)
    if action == "FLAT":
        status = STATUS_NO_POSITION_RISK
    else:
        status = STATUS_APPROVED if all(check.passed for check in normalized_checks) else STATUS_BLOCKED

    assessed = _iso_z(assessed_at)
    policy_fingerprint = _sha(dict(policy_inputs))
    facts = _facts(
        engine_id=engine_id,
        policy_id=policy_id,
        policy_version=policy_version,
        policy_fingerprint=policy_fingerprint,
        assessed_at=assessed,
        action=action,
        status=status,
        checks=normalized_checks,
    )
    digest = _sha(facts)
    return RiskAssessment(
        risk_assessment_id="risk-" + digest[:24],
        risk_assessment_hash=digest,
        engine_id=engine_id,
        policy_id=policy_id,
        policy_version=policy_version,
        policy_fingerprint=policy_fingerprint,
        assessed_at=assessed,
        action=action,
        status=status,
        checks=normalized_checks,
    )


def verify_assessment(assessment: RiskAssessment) -> None:
    if assessment.contract_version != CONTRACT_VERSION:
        raise RiskPolicyError("unsupported risk-assessment contract version")
    facts = _facts(
        engine_id=assessment.engine_id,
        policy_id=assessment.policy_id,
        policy_version=assessment.policy_version,
        policy_fingerprint=assessment.policy_fingerprint,
        assessed_at=assessment.assessed_at,
        action=assessment.action,
        status=assessment.status,
        checks=assessment.checks,
    )
    digest = _sha(facts)
    if assessment.risk_assessment_hash != digest:
        raise RiskPolicyError("risk assessment hash mismatch")
    if assessment.risk_assessment_id != "risk-" + digest[:24]:
        raise RiskPolicyError("risk assessment id mismatch")


def require_approved(assessment: RiskAssessment) -> None:
    verify_assessment(assessment)
    if assessment.status not in APPROVED_STATUSES:
        failed = [check.check_id for check in assessment.checks if not check.passed]
        raise RiskPolicyError("RISK_POLICY_BLOCKED: " + (",".join(failed) or assessment.status))
