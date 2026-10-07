#!/usr/bin/env python3
"""Immutable history seals for governed WES v5 weekly artifacts.

The live week document is intentionally mutable while execution is in progress.
This module seals only historical/frozen artifacts:
- the frozen forecast snapshot,
- every archived closed position_leg,
- every position_leg settlement.

Each artifact has a canonical SHA-256 payload hash and an append-only manifest
record chained to the previous record hash. Re-sealing an existing artifact_id
with a different payload is forbidden.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "data" / "investments" / "wes_v5_history_seal_manifest.json"
TZ = ZoneInfo("Europe/Warsaw")

MANIFEST_SCHEMA = "briefrooms-wes-v5-history-seal-manifest-v1"
SEAL_SCHEMA = "briefrooms-wes-v5-history-seal-v1"
FORECAST_SCHEMA = "briefrooms-wes-v5-frozen-forecast-v1"
SETTLEMENT_SCHEMA = "briefrooms-wes-v5-settlement-v1"


class WesHistorySealError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


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


def _empty_manifest() -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA,
        "records": [],
        "head_record_hash": None,
    }


def read_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    if not path.exists():
        return _empty_manifest()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WesHistorySealError(f"cannot read WES v5 seal manifest: {path}") from exc
    if not isinstance(value, dict):
        raise WesHistorySealError("WES v5 seal manifest must be a JSON object")
    return value


def _record_hash(record: Mapping[str, Any]) -> str:
    return sha256({k: copy.deepcopy(v) for k, v in record.items() if k != "record_hash"})


def verify_manifest_payload(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise WesHistorySealError("unsupported WES v5 seal manifest schema")
    records = manifest.get("records")
    if not isinstance(records, list):
        raise WesHistorySealError("WES v5 seal manifest records must be a list")

    previous: Optional[str] = None
    ids: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise WesHistorySealError(f"invalid WES v5 seal record at index {index}")
        artifact_id = str(record.get("artifact_id") or "")
        payload_hash = str(record.get("payload_hash") or "")
        if not artifact_id or not payload_hash:
            raise WesHistorySealError(f"incomplete WES v5 seal record at index {index}")
        if artifact_id in ids:
            raise WesHistorySealError(f"duplicate WES v5 sealed artifact_id: {artifact_id}")
        if record.get("previous_record_hash") != previous:
            raise WesHistorySealError(f"WES v5 seal hash-chain mismatch at index {index}")
        expected = _record_hash(record)
        if record.get("record_hash") != expected:
            raise WesHistorySealError(f"WES v5 seal record hash mismatch at index {index}")
        previous = expected
        ids.add(artifact_id)

    if manifest.get("head_record_hash") != previous:
        raise WesHistorySealError("WES v5 seal manifest head hash mismatch")


def verify_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = read_manifest(path)
    verify_manifest_payload(manifest)
    return manifest


def _append_record(
    *,
    artifact_type: str,
    artifact_id: str,
    week_id: str,
    payload_hash: str,
    instrument_id: Optional[str] = None,
    sealed_at: Optional[str] = None,
    path: Path = MANIFEST_PATH,
) -> dict[str, Any]:
    manifest = read_manifest(path)
    verify_manifest_payload(manifest)
    records = list(manifest.get("records") or [])

    for existing in records:
        if existing.get("artifact_id") != artifact_id:
            continue
        if (
            existing.get("artifact_type") != artifact_type
            or existing.get("week_id") != week_id
            or existing.get("instrument_id") != instrument_id
            or existing.get("payload_hash") != payload_hash
        ):
            raise WesHistorySealError(
                f"historical WES v5 payload changed after seal: {artifact_id}"
            )
        return copy.deepcopy(existing)

    record = {
        "schema_version": SEAL_SCHEMA,
        "artifact_type": artifact_type,
        "artifact_id": artifact_id,
        "week_id": str(week_id or ""),
        "instrument_id": str(instrument_id) if instrument_id else None,
        "payload_hash": payload_hash,
        "sealed_at": sealed_at or datetime.now(TZ).isoformat(timespec="seconds"),
        "previous_record_hash": records[-1].get("record_hash") if records else None,
    }
    record["record_hash"] = _record_hash(record)
    records.append(record)
    updated = {
        "schema_version": MANIFEST_SCHEMA,
        "records": records,
        "head_record_hash": record["record_hash"],
    }
    verify_manifest_payload(updated)
    _atomic_write(path, updated)
    return copy.deepcopy(record)


def _seal_ref(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SEAL_SCHEMA,
        "artifact_type": record.get("artifact_type"),
        "artifact_id": record.get("artifact_id"),
        "payload_hash": record.get("payload_hash"),
        "manifest_record_hash": record.get("record_hash"),
        "sealed_at": record.get("sealed_at"),
    }


def _forecast_instrument_snapshot(item: Mapping[str, Any]) -> dict[str, Any]:
    direction = item.get("forecast_direction") if "forecast_direction" in item else item.get("direction")
    score = item.get("forecast_score") if "forecast_score" in item else item.get("score")
    return {
        "instrument_id": copy.deepcopy(item.get("instrument_id")),
        "symbol": copy.deepcopy(item.get("symbol")),
        "label_pl": copy.deepcopy(item.get("label_pl")),
        "label_en": copy.deepcopy(item.get("label_en")),
        "direction": copy.deepcopy(direction),
        "score": copy.deepcopy(score),
        "signal_strength": copy.deepcopy(item.get("signal_strength")),
        "confidence": copy.deepcopy(item.get("confidence")),
        "confidence_type": copy.deepcopy(item.get("confidence_type")),
        "data_quality": copy.deepcopy(item.get("data_quality")),
        "quality_reason": copy.deepcopy(item.get("quality_reason")),
        "signals": copy.deepcopy(item.get("signals")),
        "risk_distance": copy.deepcopy(item.get("risk_distance")),
        "entry_thresholds": copy.deepcopy(item.get("entry_thresholds")),
        "rationale_pl": copy.deepcopy(item.get("rationale_pl")),
        "rationale_en": copy.deepcopy(item.get("rationale_en")),
    }


def build_frozen_forecast(week: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": FORECAST_SCHEMA,
        "week_id": copy.deepcopy(week.get("week_id")),
        "base_method_version": copy.deepcopy(week.get("base_method_version") or week.get("method_version")),
        "forecast_created_at": copy.deepcopy(week.get("forecast_created_at")),
        "forecast_locked_at": copy.deepcopy(week.get("forecast_locked_at")),
        "forecast_for_week_start": copy.deepcopy(week.get("forecast_for_week_start")),
        "forecast_for_week_end": copy.deepcopy(week.get("forecast_for_week_end")),
        "timezone": copy.deepcopy(week.get("timezone")),
        "market_window": copy.deepcopy(week.get("market_window")),
        "execution_assumptions": copy.deepcopy(week.get("execution_assumptions")),
        "legacy_forecast_hash": copy.deepcopy(week.get("forecast_hash")),
        "instruments": [
            _forecast_instrument_snapshot(item)
            for item in week.get("instruments", [])
            if isinstance(item, dict)
        ],
    }


def seal_forecast(
    week: dict[str, Any],
    *,
    manifest_path: Path = MANIFEST_PATH,
    sealed_at: Optional[str] = None,
) -> dict[str, Any]:
    week_id = str(week.get("week_id") or "")
    if not week_id:
        raise WesHistorySealError("frozen forecast requires week_id")

    existing_snapshot = week.get("frozen_forecast")
    snapshot = copy.deepcopy(existing_snapshot) if isinstance(existing_snapshot, dict) else build_frozen_forecast(week)
    digest = sha256(snapshot)
    artifact_id = f"forecast:{week_id}"
    existing_seal = week.get("frozen_forecast_seal") if isinstance(week.get("frozen_forecast_seal"), dict) else {}

    if existing_seal:
        if existing_seal.get("artifact_id") != artifact_id or existing_seal.get("payload_hash") != digest:
            raise WesHistorySealError(f"frozen forecast changed after seal: {week_id}")

    record = _append_record(
        artifact_type="frozen_forecast",
        artifact_id=artifact_id,
        week_id=week_id,
        payload_hash=digest,
        sealed_at=sealed_at,
        path=manifest_path,
    )
    week["frozen_forecast"] = snapshot
    week["frozen_forecast_seal"] = _seal_ref(record)
    return week["frozen_forecast_seal"]


def position_leg_payload(leg: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): copy.deepcopy(value)
        for key, value in leg.items()
        if str(key) not in {"position_leg_seal", "settlement", "settlement_seal"}
    }


def build_settlement(leg: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SETTLEMENT_SCHEMA,
        "leg_id": copy.deepcopy(leg.get("leg_id")),
        "instrument_id": copy.deepcopy(leg.get("instrument_id")),
        "direction": copy.deepcopy(leg.get("direction")),
        "entry_price": copy.deepcopy(leg.get("entry_price")),
        "entry_captured_at": copy.deepcopy(leg.get("entry_captured_at")),
        "exit_price": copy.deepcopy(leg.get("exit_price")),
        "exit_captured_at": copy.deepcopy(leg.get("exit_captured_at")),
        "exit_reason": copy.deepcopy(leg.get("exit_reason")),
        "gross_result_percent": copy.deepcopy(leg.get("gross_result_percent")),
        "estimated_round_trip_cost_percent": copy.deepcopy(leg.get("estimated_round_trip_cost_percent")),
        "net_result_percent": copy.deepcopy(leg.get("net_result_percent")),
    }


def seal_closed_leg(
    week_id: str,
    leg: dict[str, Any],
    *,
    manifest_path: Path = MANIFEST_PATH,
    sealed_at: Optional[str] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    leg_id = str(leg.get("leg_id") or "")
    instrument_id = str(leg.get("instrument_id") or "")
    if not week_id or not leg_id or not instrument_id:
        raise WesHistorySealError("closed position_leg requires week_id, instrument_id and leg_id")
    if leg.get("entry_price") is None or leg.get("exit_price") is None:
        raise WesHistorySealError(f"cannot seal open position_leg: {leg_id}")

    derived_settlement = build_settlement(leg)
    existing_settlement = leg.get("settlement")
    if isinstance(existing_settlement, dict):
        if existing_settlement != derived_settlement:
            raise WesHistorySealError(f"settlement payload differs from closed leg: {week_id}:{leg_id}")
        settlement = copy.deepcopy(existing_settlement)
    else:
        settlement = derived_settlement

    settlement_hash = sha256(settlement)
    settlement_id = f"settlement:{week_id}:{instrument_id}:{leg_id}"
    existing_settlement_seal = leg.get("settlement_seal") if isinstance(leg.get("settlement_seal"), dict) else {}
    if existing_settlement_seal and (
        existing_settlement_seal.get("artifact_id") != settlement_id
        or existing_settlement_seal.get("payload_hash") != settlement_hash
    ):
        raise WesHistorySealError(f"settlement changed after seal: {settlement_id}")

    settlement_record = _append_record(
        artifact_type="settlement",
        artifact_id=settlement_id,
        week_id=week_id,
        instrument_id=instrument_id,
        payload_hash=settlement_hash,
        sealed_at=sealed_at,
        path=manifest_path,
    )
    leg["settlement"] = settlement
    leg["settlement_seal"] = _seal_ref(settlement_record)

    leg_hash = sha256(position_leg_payload(leg))
    leg_artifact_id = f"position_leg:{week_id}:{instrument_id}:{leg_id}"
    existing_leg_seal = leg.get("position_leg_seal") if isinstance(leg.get("position_leg_seal"), dict) else {}
    if existing_leg_seal and (
        existing_leg_seal.get("artifact_id") != leg_artifact_id
        or existing_leg_seal.get("payload_hash") != leg_hash
    ):
        raise WesHistorySealError(f"position_leg changed after seal: {leg_artifact_id}")

    leg_record = _append_record(
        artifact_type="position_leg",
        artifact_id=leg_artifact_id,
        week_id=week_id,
        instrument_id=instrument_id,
        payload_hash=leg_hash,
        sealed_at=sealed_at,
        path=manifest_path,
    )
    leg["position_leg_seal"] = _seal_ref(leg_record)
    return leg["position_leg_seal"], leg["settlement_seal"]


def is_wes_v5_week(week: Mapping[str, Any]) -> bool:
    candidates = [
        week.get("method_version"),
        (week.get("multi_instrument_exposure_layer") or {}).get("version") if isinstance(week.get("multi_instrument_exposure_layer"), dict) else None,
    ]
    for value in candidates:
        text = str(value or "")
        try:
            if int(text.split(".", 1)[0]) >= 5:
                return True
        except Exception:
            pass
    return False


def _record_index(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(record.get("artifact_id")): record
        for record in manifest.get("records", [])
        if isinstance(record, dict) and record.get("artifact_id")
    }


def _verify_seal_ref(
    seal: Mapping[str, Any],
    *,
    artifact_id: str,
    payload_hash: str,
    record_index: Mapping[str, Mapping[str, Any]],
) -> Optional[str]:
    if seal.get("artifact_id") != artifact_id:
        return "seal_artifact_id_mismatch"
    if seal.get("payload_hash") != payload_hash:
        return "sealed_payload_hash_mismatch"
    record = record_index.get(artifact_id)
    if not isinstance(record, Mapping):
        return "sealed_artifact_missing_from_manifest"
    if record.get("payload_hash") != payload_hash:
        return "manifest_payload_hash_mismatch"
    if seal.get("manifest_record_hash") != record.get("record_hash"):
        return "manifest_record_hash_mismatch"
    return None


def week_seal_violations(
    week: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not is_wes_v5_week(week):
        return issues

    week_id = str(week.get("week_id") or "")
    index = _record_index(manifest)
    snapshot = week.get("frozen_forecast")
    seal = week.get("frozen_forecast_seal")
    if not isinstance(snapshot, dict) or not isinstance(seal, dict):
        issues.append({"error": "missing_wes_v5_frozen_forecast_seal"})
    else:
        digest = sha256(snapshot)
        artifact_id = f"forecast:{week_id}"
        code = _verify_seal_ref(seal, artifact_id=artifact_id, payload_hash=digest, record_index=index)
        if code:
            issues.append({"error": code, "artifact_id": artifact_id})

    for item in week.get("instruments", []):
        if not isinstance(item, dict):
            continue
        for leg in item.get("position_legs", []) if isinstance(item.get("position_legs"), list) else []:
            if not isinstance(leg, dict):
                continue
            leg_id = str(leg.get("leg_id") or "")
            instrument_id = str(leg.get("instrument_id") or item.get("instrument_id") or "")
            if not leg_id:
                issues.append({"error": "position_leg_missing_leg_id", "instrument": instrument_id})
                continue

            settlement = leg.get("settlement")
            settlement_seal = leg.get("settlement_seal")
            settlement_id = f"settlement:{week_id}:{instrument_id}:{leg_id}"
            if not isinstance(settlement, dict) or not isinstance(settlement_seal, dict):
                issues.append({"error": "missing_settlement_seal", "artifact_id": settlement_id})
            else:
                derived = build_settlement(leg)
                if settlement != derived:
                    issues.append({"error": "settlement_payload_differs_from_leg", "artifact_id": settlement_id})
                digest = sha256(settlement)
                code = _verify_seal_ref(
                    settlement_seal,
                    artifact_id=settlement_id,
                    payload_hash=digest,
                    record_index=index,
                )
                if code:
                    issues.append({"error": code, "artifact_id": settlement_id})

            leg_seal = leg.get("position_leg_seal")
            leg_artifact_id = f"position_leg:{week_id}:{instrument_id}:{leg_id}"
            if not isinstance(leg_seal, dict):
                issues.append({"error": "missing_position_leg_seal", "artifact_id": leg_artifact_id})
            else:
                digest = sha256(position_leg_payload(leg))
                code = _verify_seal_ref(
                    leg_seal,
                    artifact_id=leg_artifact_id,
                    payload_hash=digest,
                    record_index=index,
                )
                if code:
                    issues.append({"error": code, "artifact_id": leg_artifact_id})
    return issues


_INTEGRITY_KEYS = {
    "frozen_forecast",
    "frozen_forecast_seal",
    "position_leg_seal",
    "settlement",
    "settlement_seal",
    "wes_history_seal",
}


def economic_payload(value: Any) -> Any:
    """Remove only P1 seal metadata, allowing migration to prove economics unchanged."""
    if isinstance(value, list):
        return [economic_payload(row) for row in value]
    if isinstance(value, dict):
        return {
            str(key): economic_payload(row)
            for key, row in value.items()
            if str(key) not in _INTEGRITY_KEYS
        }
    return copy.deepcopy(value)
