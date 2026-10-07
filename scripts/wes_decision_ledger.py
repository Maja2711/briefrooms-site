#!/usr/bin/env python3
"""Append-only immutable WES forecast/decision ledger.

Every frozen WES entry decision is persisted as a full canonical payload plus:
- deterministic decision_id
- SHA-256 payload_hash
- predecessor_decision_id for successor decisions
- append-only hash-chain metadata

A frozen payload is never updated in place. Any change to an executable decision
(for example LIMIT -> MARKET promotion or time-only reaffirmation) must append a
new successor decision with a new decision_id.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    import provenance_contract as provenance
except ImportError:
    from scripts import provenance_contract as provenance

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "data" / "investments" / "wes_decision_ledger.json"
SCHEMA_VERSION = "briefrooms-wes-decision-ledger-v1"
RECORD_VERSION = "briefrooms-wes-frozen-decision-v1"

_METADATA_KEYS = {
    "decision_id",
    "payload_hash",
    "decision_kind",
    "predecessor_decision_id",
    "ledger_record_hash",
    "ledger_schema_version",
    "provenance",
}


class WesDecisionLedgerError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def payload_from_pending(pending: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): copy.deepcopy(value)
        for key, value in pending.items()
        if str(key) not in _METADATA_KEYS
    }


def payload_hash(pending_or_payload: Mapping[str, Any]) -> str:
    return sha256(payload_from_pending(pending_or_payload))


def _empty_ledger() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "records": [],
        "head_record_hash": None,
    }


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_ledger()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WesDecisionLedgerError(f"cannot read WES decision ledger: {path}") from exc
    if not isinstance(payload, dict):
        raise WesDecisionLedgerError("WES decision ledger must be a JSON object")
    return payload


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _decision_identity(
    *,
    week_id: str,
    instrument_id: str,
    decided_at: str,
    decision_kind: str,
    predecessor_decision_id: Optional[str],
    payload_hash_value: str,
) -> dict[str, Any]:
    return {
        "record_version": RECORD_VERSION,
        "week_id": str(week_id or ""),
        "instrument_id": str(instrument_id or ""),
        "decided_at": str(decided_at or ""),
        "decision_kind": str(decision_kind or "ENTRY_FREEZE"),
        "predecessor_decision_id": str(predecessor_decision_id) if predecessor_decision_id else None,
        "payload_hash": payload_hash_value,
    }


def decision_id_for(
    *,
    week_id: str,
    instrument_id: str,
    decided_at: str,
    decision_kind: str,
    predecessor_decision_id: Optional[str],
    payload_hash_value: str,
) -> str:
    digest = sha256(_decision_identity(
        week_id=week_id,
        instrument_id=instrument_id,
        decided_at=decided_at,
        decision_kind=decision_kind,
        predecessor_decision_id=predecessor_decision_id,
        payload_hash_value=payload_hash_value,
    ))
    return "wes-dec-" + digest[:28]


def _record_hash(record: Mapping[str, Any]) -> str:
    return sha256({k: copy.deepcopy(v) for k, v in record.items() if k != "record_hash"})


def verify_ledger_payload(ledger: Mapping[str, Any]) -> None:
    if ledger.get("schema_version") != SCHEMA_VERSION:
        raise WesDecisionLedgerError("unsupported WES decision ledger schema")
    records = ledger.get("records")
    if not isinstance(records, list):
        raise WesDecisionLedgerError("WES decision ledger records must be a list")

    previous_hash: Optional[str] = None
    seen_ids: set[str] = set()
    by_id: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise WesDecisionLedgerError(f"invalid WES decision record at index {index}")
        if record.get("record_version") != RECORD_VERSION:
            raise WesDecisionLedgerError(f"unsupported WES decision record version at index {index}")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise WesDecisionLedgerError(f"missing WES frozen payload at index {index}")
        actual_payload_hash = sha256(payload)
        if record.get("payload_hash") != actual_payload_hash:
            raise WesDecisionLedgerError(f"WES frozen payload hash mismatch at index {index}")

        expected_id = decision_id_for(
            week_id=str(record.get("week_id") or ""),
            instrument_id=str(record.get("instrument_id") or ""),
            decided_at=str(record.get("decided_at") or ""),
            decision_kind=str(record.get("decision_kind") or ""),
            predecessor_decision_id=record.get("predecessor_decision_id"),
            payload_hash_value=actual_payload_hash,
        )
        if record.get("decision_id") != expected_id:
            raise WesDecisionLedgerError(f"WES decision_id mismatch at index {index}")
        if expected_id in seen_ids:
            raise WesDecisionLedgerError(f"duplicate WES decision_id: {expected_id}")

        predecessor = record.get("predecessor_decision_id")
        if predecessor and predecessor not in by_id:
            raise WesDecisionLedgerError(
                f"WES predecessor must already exist in ledger: {predecessor}"
            )
        if record.get("previous_record_hash") != previous_hash:
            raise WesDecisionLedgerError(f"WES decision hash-chain mismatch at index {index}")
        expected_record_hash = _record_hash(record)
        if record.get("record_hash") != expected_record_hash:
            raise WesDecisionLedgerError(f"WES decision record hash mismatch at index {index}")

        previous_hash = expected_record_hash
        seen_ids.add(expected_id)
        by_id.add(expected_id)

    if ledger.get("head_record_hash") != previous_hash:
        raise WesDecisionLedgerError("WES decision ledger head hash mismatch")


def verify_ledger(path: Path = LEDGER_PATH) -> dict[str, Any]:
    ledger = _read(path)
    verify_ledger_payload(ledger)
    return ledger


def assert_pending_integrity(pending: Mapping[str, Any]) -> bool:
    """Fail closed if a sealed pending decision was mutated after freeze."""
    decision_id = str(pending.get("decision_id") or "")
    expected_hash = str(pending.get("payload_hash") or "")
    if not decision_id and not expected_hash:
        return True  # legacy/unsealed compatibility data
    if not decision_id or not expected_hash:
        raise WesDecisionLedgerError("partially sealed WES pending decision")
    actual_hash = payload_hash(pending)
    if actual_hash != expected_hash:
        raise WesDecisionLedgerError(
            f"immutable WES decision payload mutated after freeze: {decision_id}"
        )
    return True


def _with_native_provenance(
    pending: Mapping[str, Any],
    *,
    week_id: str,
    instrument_id: str,
) -> dict[str, Any]:
    raw=copy.deepcopy(dict(pending))
    raw.pop("provenance",None)
    decision=raw.get("decision") if isinstance(raw.get("decision"),Mapping) else {}
    artifact_id=str(raw.get("decision_id") or "")
    if not artifact_id:
        raise WesDecisionLedgerError("native provenance requires decision_id")
    source_ids=[
        str(value)
        for value in (
            decision.get("decision_source"),
            decision.get("execution_authority"),
            raw.get("decision_kind"),
        )
        if value
    ]
    return provenance.attach_native(
        raw,
        artifact_id=artifact_id,
        artifact_type="wes_frozen_decision",
        engine_id="wes",
        engine_version=str(decision.get("wes_methodology") or decision.get("method_version") or RECORD_VERSION),
        created_at=str(raw.get("decided_at") or ""),
        authority="decision",
        parent_artifact_ids=[str(raw["predecessor_decision_id"])] if raw.get("predecessor_decision_id") else [],
        source_ids=source_ids,
        decision_id=artifact_id,
        prospective=True,
        domain_provenance={
            "week_id":str(week_id or ""),
            "instrument_id":str(instrument_id or ""),
            "decision_kind":raw.get("decision_kind"),
            "ledger_payload_hash":raw.get("payload_hash"),
            "native_write_time":True,
        },
    )


def append_frozen_decision(
    payload: Mapping[str, Any],
    *,
    week_id: str,
    instrument_id: str,
    decision_kind: str = "ENTRY_FREEZE",
    predecessor_decision_id: Optional[str] = None,
    path: Path = LEDGER_PATH,
) -> dict[str, Any]:
    frozen_payload = copy.deepcopy(dict(payload))
    if any(key in frozen_payload for key in _METADATA_KEYS):
        raise WesDecisionLedgerError("frozen payload contains ledger metadata fields")
    decided_at = str(frozen_payload.get("decided_at") or "")
    if not decided_at:
        raise WesDecisionLedgerError("frozen WES decision requires decided_at")
    if not instrument_id:
        raise WesDecisionLedgerError("frozen WES decision requires instrument_id")

    digest = sha256(frozen_payload)
    decision_id = decision_id_for(
        week_id=week_id,
        instrument_id=instrument_id,
        decided_at=decided_at,
        decision_kind=decision_kind,
        predecessor_decision_id=predecessor_decision_id,
        payload_hash_value=digest,
    )

    ledger = _read(path)
    verify_ledger_payload(ledger)
    records = list(ledger.get("records") or [])

    for existing in records:
        if existing.get("decision_id") != decision_id:
            continue
        if (
            existing.get("payload_hash") != digest
            or existing.get("payload") != frozen_payload
            or existing.get("decision_kind") != decision_kind
            or existing.get("predecessor_decision_id") != predecessor_decision_id
        ):
            raise WesDecisionLedgerError(f"WES decision_id collision: {decision_id}")
        pending = {
            **copy.deepcopy(frozen_payload),
            "decision_id": decision_id,
            "payload_hash": digest,
            "decision_kind": decision_kind,
            "predecessor_decision_id": predecessor_decision_id,
            "ledger_record_hash": existing.get("record_hash"),
            "ledger_schema_version": SCHEMA_VERSION,
        }
        return _with_native_provenance(pending, week_id=week_id, instrument_id=instrument_id)

    if predecessor_decision_id and not any(
        row.get("decision_id") == predecessor_decision_id for row in records
    ):
        raise WesDecisionLedgerError(
            f"WES predecessor decision not found: {predecessor_decision_id}"
        )

    previous_hash = records[-1].get("record_hash") if records else None
    record = {
        "record_version": RECORD_VERSION,
        "decision_id": decision_id,
        "payload_hash": digest,
        "week_id": str(week_id or ""),
        "instrument_id": str(instrument_id),
        "decided_at": decided_at,
        "decision_kind": str(decision_kind),
        "predecessor_decision_id": predecessor_decision_id,
        "previous_record_hash": previous_hash,
        "payload": frozen_payload,
    }
    record["record_hash"] = _record_hash(record)
    records.append(record)
    updated = {
        "schema_version": SCHEMA_VERSION,
        "records": records,
        "head_record_hash": record["record_hash"],
    }
    verify_ledger_payload(updated)
    _atomic_write(path, updated)

    pending = {
        **copy.deepcopy(frozen_payload),
        "decision_id": decision_id,
        "payload_hash": digest,
        "decision_kind": decision_kind,
        "predecessor_decision_id": predecessor_decision_id,
        "ledger_record_hash": record["record_hash"],
        "ledger_schema_version": SCHEMA_VERSION,
    }
    return _with_native_provenance(pending, week_id=week_id, instrument_id=instrument_id)


def successor_decision(
    pending: Mapping[str, Any],
    updated_payload: Mapping[str, Any],
    *,
    week_id: str,
    instrument_id: str,
    decision_kind: str,
    path: Path = LEDGER_PATH,
) -> dict[str, Any]:
    assert_pending_integrity(pending)
    predecessor = str(pending.get("decision_id") or "")
    if not predecessor:
        raise WesDecisionLedgerError("successor requires a sealed predecessor decision")
    return append_frozen_decision(
        updated_payload,
        week_id=week_id,
        instrument_id=instrument_id,
        decision_kind=decision_kind,
        predecessor_decision_id=predecessor,
        path=path,
    )
