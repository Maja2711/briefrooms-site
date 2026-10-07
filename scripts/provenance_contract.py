#!/usr/bin/env python3
"""BriefRooms Provenance Contract v1.

A small, engine-neutral provenance envelope shared by BriefRooms decision,
belief, trading and shadow systems.

P1 scope is intentionally non-invasive:
- define one canonical provenance envelope,
- hash the source artifact without changing its economic/decision payload,
- provide validation and optional prospective attachment helpers,
- keep compatibility adapters read-only until individual engines migrate.

This module has no decision, scoring, sizing, execution or promotion authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "briefrooms-provenance-contract-v1"
HASH_ALGORITHM = "sha256"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_FIELDS = (
    "schema_version",
    "artifact_id",
    "artifact_type",
    "engine_id",
    "engine_version",
    "created_at",
    "parent_artifact_ids",
    "source_ids",
    "evidence_ids",
    "belief_ids",
    "decision_id",
    "forecast_id",
    "verification_id",
    "payload_hash",
    "authority",
    "prospective",
    "domain_provenance",
)


class ProvenanceContractError(ValueError):
    """Raised when a provenance envelope violates the canonical contract."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def economic_payload(value: Any) -> Any:
    """Return a deep copy with only canonical `provenance` envelopes removed.

    Legacy fields such as `execution_provenance` remain untouched because they
    are part of the source engine's existing artifact and compatibility history.
    """
    if isinstance(value, list):
        return [economic_payload(row) for row in value]
    if isinstance(value, tuple):
        return [economic_payload(row) for row in value]
    if isinstance(value, Mapping):
        return {
            str(key): economic_payload(row)
            for key, row in value.items()
            if str(key) != "provenance"
        }
    return copy.deepcopy(value)


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(economic_payload(value)).encode("utf-8")).hexdigest()


def _ids(values: Sequence[Any] | None) -> list[str]:
    return sorted({str(value).strip() for value in (values or ()) if str(value).strip()})


def _optional_id(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _validate_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ProvenanceContractError("created_at is required")
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ProvenanceContractError(f"created_at is not ISO-8601: {text}") from exc
    if parsed.tzinfo is None:
        raise ProvenanceContractError("created_at must include an explicit timezone")
    return text


def build_envelope(
    *,
    artifact_id: str,
    artifact_type: str,
    engine_id: str,
    created_at: str,
    authority: str,
    source_payload: Any,
    engine_version: str | None = None,
    parent_artifact_ids: Sequence[Any] | None = None,
    source_ids: Sequence[Any] | None = None,
    evidence_ids: Sequence[Any] | None = None,
    belief_ids: Sequence[Any] | None = None,
    decision_id: Any = None,
    forecast_id: Any = None,
    verification_id: Any = None,
    prospective: bool = True,
    domain_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_id = str(artifact_id or "").strip()
    artifact_type = str(artifact_type or "").strip()
    engine_id = str(engine_id or "").strip()
    authority = str(authority or "").strip()
    if not artifact_id:
        raise ProvenanceContractError("artifact_id is required")
    if not artifact_type:
        raise ProvenanceContractError("artifact_type is required")
    if not engine_id:
        raise ProvenanceContractError("engine_id is required")
    if not authority:
        raise ProvenanceContractError("authority is required")

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "engine_id": engine_id,
        "engine_version": _optional_id(engine_version),
        "created_at": _validate_timestamp(created_at),
        "parent_artifact_ids": _ids(parent_artifact_ids),
        "source_ids": _ids(source_ids),
        "evidence_ids": _ids(evidence_ids),
        "belief_ids": _ids(belief_ids),
        "decision_id": _optional_id(decision_id),
        "forecast_id": _optional_id(forecast_id),
        "verification_id": _optional_id(verification_id),
        "payload_hash": payload_hash(source_payload),
        "authority": authority,
        "prospective": bool(prospective),
        "domain_provenance": copy.deepcopy(dict(domain_provenance or {})),
    }
    validate_envelope(envelope, source_payload=source_payload)
    return envelope


def validate_envelope(
    envelope: Mapping[str, Any],
    *,
    source_payload: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(envelope, Mapping):
        raise ProvenanceContractError("provenance envelope must be an object")
    missing = [field for field in REQUIRED_FIELDS if field not in envelope]
    if missing:
        raise ProvenanceContractError("missing provenance fields: " + ", ".join(missing))
    if envelope.get("schema_version") != SCHEMA_VERSION:
        raise ProvenanceContractError("unsupported provenance schema_version")

    for field in ("artifact_id", "artifact_type", "engine_id", "authority"):
        if not str(envelope.get(field) or "").strip():
            raise ProvenanceContractError(f"{field} must be non-empty")
    _validate_timestamp(envelope.get("created_at"))

    engine_version = envelope.get("engine_version")
    if engine_version is not None and not str(engine_version).strip():
        raise ProvenanceContractError("engine_version must be null or non-empty")

    for field in ("parent_artifact_ids", "source_ids", "evidence_ids", "belief_ids"):
        values = envelope.get(field)
        if not isinstance(values, list):
            raise ProvenanceContractError(f"{field} must be a list")
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ProvenanceContractError(f"{field} contains an empty/non-string id")
        if values != sorted(set(values)):
            raise ProvenanceContractError(f"{field} must be sorted and unique")

    for field in ("decision_id", "forecast_id", "verification_id"):
        value = envelope.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ProvenanceContractError(f"{field} must be null or non-empty string")

    digest = str(envelope.get("payload_hash") or "")
    if not _HEX_64.fullmatch(digest):
        raise ProvenanceContractError("payload_hash must be a lowercase SHA-256 hex digest")
    if source_payload is not None and digest != payload_hash(source_payload):
        raise ProvenanceContractError("payload_hash does not match source artifact")

    if not isinstance(envelope.get("prospective"), bool):
        raise ProvenanceContractError("prospective must be boolean")
    if not isinstance(envelope.get("domain_provenance"), Mapping):
        raise ProvenanceContractError("domain_provenance must be an object")

    return dict(envelope)


def attach_provenance(
    payload: Mapping[str, Any],
    envelope: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach v1 provenance to a prospective artifact without changing its payload."""
    validate_envelope(envelope, source_payload=payload)
    current = payload.get("provenance")
    if current is not None and current != envelope:
        raise ProvenanceContractError("artifact already contains different provenance")
    out = copy.deepcopy(dict(payload))
    out["provenance"] = copy.deepcopy(dict(envelope))
    if economic_payload(out) != economic_payload(payload):
        raise ProvenanceContractError("provenance attachment changed economic payload")
    return out


def verify_attached(payload: Mapping[str, Any]) -> dict[str, Any]:
    envelope = payload.get("provenance")
    if not isinstance(envelope, Mapping):
        raise ProvenanceContractError("artifact does not contain provenance")
    return validate_envelope(envelope, source_payload=payload)
