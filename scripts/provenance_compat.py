#!/usr/bin/env python3
"""Read-only compatibility adapters for BriefRooms Provenance Contract v1.

Adapters project existing engine artifacts into one canonical provenance
envelope. They never mutate the source object and have zero decision authority.

Covered P1 families:
Belief/L3-A -> WES -> Daily -> BRACE -> Stock Trading -> Shadow Engines.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Mapping, Sequence

try:
    import provenance_contract as pc
except ImportError:  # unit tests imported from repository root
    from scripts import provenance_contract as pc


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _created_at(payload: Mapping[str, Any], explicit: str | None) -> str:
    value = explicit or _first(
        payload,
        "created_at",
        "generated_at",
        "last_updated_at",
        "last_updated",
        "forecast_at",
        "verified_at",
        "observed_at",
        "opened_at",
        "data_at",
        "last_run_at",
    )
    if value is None:
        raise pc.ProvenanceContractError(
            "compatibility adapter needs created_at when source artifact has no point-in-time timestamp"
        )
    return str(value)


def _version(payload: Mapping[str, Any], explicit: str | None = None) -> str | None:
    value = explicit or _first(
        payload,
        "method_version",
        "engine_version",
        "model_version",
        "report_version",
        "schema_version",
        "version",
    )
    return None if value is None else str(value)


def _many(*values: Any) -> list[str]:
    out: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            out.extend(str(item) for item in value if item not in (None, ""))
        else:
            out.append(str(value))
    return sorted(set(out))


def _synthetic_id(prefix: str, payload: Mapping[str, Any]) -> str:
    return f"{prefix}:{pc.payload_hash(payload)[:24]}"


def _common_domain(adapter: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "compatibility_adapter": adapter,
        "compatibility_mode": "read_only_sidecar",
        "source_schema": payload.get("schema_version"),
        "source_payload_preserved": True,
    }


def adapt_belief_l3a(
    payload: Mapping[str, Any],
    *,
    created_at: str | None = None,
    engine_version: str | None = None,
) -> dict[str, Any]:
    """Project Belief Core or L3-A artifacts into the common envelope."""
    evidence_ids = _many(
        payload.get("evidence_ids"),
        payload.get("representative_evidence_ids"),
        payload.get("support_evidence_ids"),
        payload.get("opposing_evidence_ids"),
    )
    belief_ids = _many(payload.get("belief_id"), payload.get("belief_ids"))
    parents = _many(
        payload.get("parent_artifact_ids"),
        payload.get("derived_from"),
        payload.get("question_id"),
        payload.get("intent_id"),
        payload.get("attribution_id") if payload.get("verification_id") else None,
    )
    source_ids = _many(
        payload.get("source"),
        payload.get("source_ref"),
        payload.get("outcome_source"),
        payload.get("outcome_ref"),
    )

    if payload.get("verification_id") or payload.get("verified_at"):
        artifact_type, authority = "verification", "verification"
        artifact_id = str(payload.get("verification_id") or _synthetic_id("belief-verification", payload))
    elif payload.get("attribution_id"):
        artifact_type, authority = "l3a_attribution", "research"
        artifact_id = str(payload["attribution_id"])
    elif payload.get("forecast_id") or payload.get("forecast_at"):
        artifact_type, authority = "frozen_forecast", "forecast"
        artifact_id = str(payload.get("forecast_id") or _synthetic_id("belief-forecast", payload))
    elif payload.get("evidence_id"):
        artifact_type, authority = "evidence", "evidence"
        artifact_id = str(payload["evidence_id"])
    elif payload.get("belief_id"):
        artifact_type, authority = "belief_state", "belief"
        artifact_id = _synthetic_id(f"belief:{payload['belief_id']}", payload)
    else:
        artifact_type, authority = "belief_l3a_artifact", "research"
        artifact_id = _synthetic_id("belief-l3a", payload)

    return pc.build_envelope(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        engine_id="belief_l3a",
        engine_version=_version(payload, engine_version),
        created_at=_created_at(payload, created_at),
        parent_artifact_ids=parents,
        source_ids=source_ids,
        evidence_ids=evidence_ids,
        belief_ids=belief_ids,
        decision_id=payload.get("decision_id"),
        forecast_id=payload.get("forecast_id"),
        verification_id=payload.get("verification_id"),
        authority=authority,
        prospective=not bool(payload.get("legacy", False)),
        source_payload=payload,
        domain_provenance=_common_domain("belief_l3a", payload),
    )


def adapt_wes(
    payload: Mapping[str, Any],
    *,
    week_id: str | None = None,
    instrument_id: str | None = None,
    created_at: str | None = None,
    engine_version: str | None = None,
) -> dict[str, Any]:
    """Project WES week/forecast/position_leg/settlement artifacts without resealing them."""
    week = str(week_id or payload.get("week_id") or "").strip() or None
    instrument = str(instrument_id or payload.get("instrument_id") or "").strip() or None
    schema = str(payload.get("schema_version") or "")
    seal = None
    for key in ("position_leg_seal", "settlement_seal", "frozen_forecast_seal", "wes_history_seal"):
        if isinstance(payload.get(key), Mapping):
            seal = payload[key]
            break

    if "settlement" in schema or (
        payload.get("leg_id") and payload.get("exit_price") is not None and payload.get("entry_price") is not None
        and not payload.get("position_leg_seal")
    ):
        artifact_type, authority = "settlement", "settlement"
    elif payload.get("leg_id"):
        artifact_type, authority = "position_leg", "execution"
    elif "frozen-forecast" in schema or payload.get("frozen_forecast") is not None:
        artifact_type, authority = "frozen_forecast", "forecast"
    else:
        artifact_type, authority = "wes_artifact", "decision"

    artifact_id = str((seal or {}).get("artifact_id") or "")
    if not artifact_id:
        parts = [artifact_type, week, instrument, payload.get("leg_id")]
        stable = ":".join(str(part) for part in parts if part not in (None, ""))
        artifact_id = f"wes:{stable}" if stable else _synthetic_id("wes", payload)

    evidence_ids = _many(
        payload.get("evidence_ids"),
        payload.get("representative_evidence_ids"),
        payload.get("belief_evidence_ids"),
    )
    belief_ids = _many(payload.get("belief_ids"), payload.get("belief_id"))
    parent_ids = _many(
        payload.get("parent_artifact_ids"),
        payload.get("forecast_id"),
        payload.get("decision_id"),
        payload.get("leg_id") if artifact_type == "settlement" else None,
    )
    source_ids = _many(payload.get("source_ids"), payload.get("source"), payload.get("decision_source"))

    domain = _common_domain("wes", payload)
    domain.update({
        "week_id": week,
        "instrument_id": instrument,
        "existing_seal_artifact_id": (seal or {}).get("artifact_id"),
        "existing_seal_payload_hash": (seal or {}).get("payload_hash"),
        "reseal_required": False,
    })

    return pc.build_envelope(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        engine_id="wes",
        engine_version=_version(payload, engine_version),
        created_at=_created_at(payload, created_at),
        parent_artifact_ids=parent_ids,
        source_ids=source_ids,
        evidence_ids=evidence_ids,
        belief_ids=belief_ids,
        decision_id=payload.get("decision_id"),
        forecast_id=payload.get("forecast_id"),
        verification_id=payload.get("verification_id"),
        authority=authority,
        prospective=True,
        source_payload=payload,
        domain_provenance=domain,
    )


def adapt_daily(
    payload: Mapping[str, Any],
    *,
    created_at: str | None = None,
    engine_version: str | None = None,
) -> dict[str, Any]:
    """Project Daily EURUSD / Daily decision artifacts."""
    used = payload.get("used_beliefs") if isinstance(payload.get("used_beliefs"), list) else []
    belief_ids = _many(
        payload.get("belief_ids"),
        [row.get("belief_id") for row in used if isinstance(row, Mapping)],
    )
    evidence_ids = _many(
        payload.get("evidence_ids"),
        [
            evidence_id
            for row in used if isinstance(row, Mapping)
            for evidence_id in (row.get("representative_evidence_ids") or [])
        ],
    )
    source_ids = _many(
        payload.get("source_ids"),
        payload.get("decision_source"),
        payload.get("epistemic_source"),
    )
    decision_id = payload.get("decision_id")
    artifact_id = str(decision_id or _synthetic_id("daily-decision", payload))

    return pc.build_envelope(
        artifact_id=artifact_id,
        artifact_type="decision",
        engine_id=str(payload.get("engine_id") or "daily_eurusd"),
        engine_version=_version(payload, engine_version),
        created_at=_created_at(payload, created_at),
        parent_artifact_ids=_many(payload.get("parent_artifact_ids")),
        source_ids=source_ids,
        evidence_ids=evidence_ids,
        belief_ids=belief_ids,
        decision_id=decision_id or artifact_id,
        forecast_id=payload.get("forecast_id"),
        verification_id=payload.get("verification_id"),
        authority="decision",
        prospective=True,
        source_payload=payload,
        domain_provenance=_common_domain("daily", payload),
    )


def adapt_brace(
    payload: Mapping[str, Any],
    *,
    created_at: str | None = None,
    engine_version: str | None = None,
) -> dict[str, Any]:
    """Project BRACE reports/entity artifacts into the common lineage vocabulary."""
    definitions = payload.get("materialized_belief_definitions")
    belief_ids = _many(
        payload.get("belief_ids"),
        [
            row.get("belief_id")
            for row in definitions
            if isinstance(definitions, list) and isinstance(row, Mapping)
        ] if isinstance(definitions, list) else [],
    )
    activation_policy = payload.get("activation_policy") if isinstance(payload.get("activation_policy"), Mapping) else {}
    source_ids = _many(
        payload.get("source_ids"),
        activation_policy.get("candidate_source"),
        payload.get("source"),
    )
    entities = payload.get("entities") if isinstance(payload.get("entities"), Mapping) else {}
    entity_ids = [
        row.get("entity_id")
        for bucket in ("active", "dormant")
        for row in (entities.get(bucket) or [])
        if isinstance(row, Mapping)
    ]
    artifact_id = str(payload.get("artifact_id") or _synthetic_id("brace", payload))
    artifact_type = "brace_report" if payload.get("report_version") is not None else "brace_artifact"

    return pc.build_envelope(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        engine_id=str(payload.get("engine_id") or "brace"),
        engine_version=_version(payload, engine_version),
        created_at=_created_at(payload, created_at),
        parent_artifact_ids=_many(payload.get("parent_artifact_ids"), entity_ids),
        source_ids=source_ids,
        evidence_ids=_many(payload.get("evidence_ids")),
        belief_ids=belief_ids,
        decision_id=payload.get("decision_id"),
        forecast_id=payload.get("forecast_id"),
        verification_id=payload.get("verification_id"),
        authority="report",
        prospective=not bool((payload.get("anti_hindsight") or {}).get("historical_backfill", False)),
        source_payload=payload,
        domain_provenance=_common_domain("brace", payload),
    )


def adapt_stock_trading(
    payload: Mapping[str, Any],
    *,
    created_at: str | None = None,
    engine_version: str | None = None,
) -> dict[str, Any]:
    """Project Stock Trading audit/action artifacts, preserving execution_provenance."""
    execution = payload.get("execution_provenance") if isinstance(payload.get("execution_provenance"), Mapping) else {}
    action = str(payload.get("action") or "artifact")
    position_id = payload.get("position_id")
    artifact_id = str(payload.get("artifact_id") or (
        f"stock:{action}:{position_id}" if position_id else _synthetic_id(f"stock:{action}", payload)
    ))
    authority = "execution" if action in {"open", "close"} else "decision"
    source_ids = _many(
        payload.get("source_ids"),
        execution.get("source"),
        payload.get("source_engine"),
    )

    return pc.build_envelope(
        artifact_id=artifact_id,
        artifact_type=f"stock_{action}",
        engine_id=str(payload.get("engine_id") or "stock_trading_v2"),
        engine_version=_version(payload, engine_version) or str(payload.get("source_engine") or "v2"),
        created_at=_created_at(payload, created_at),
        parent_artifact_ids=_many(payload.get("parent_artifact_ids"), payload.get("candidate_id")),
        source_ids=source_ids,
        evidence_ids=_many(payload.get("evidence_ids")),
        belief_ids=_many(payload.get("belief_ids")),
        decision_id=payload.get("decision_id") or position_id,
        forecast_id=payload.get("forecast_id"),
        verification_id=payload.get("verification_id"),
        authority=authority,
        prospective=True,
        source_payload=payload,
        domain_provenance={
            **_common_domain("stock_trading", payload),
            "legacy_execution_provenance_preserved": bool(execution),
        },
    )


def adapt_shadow(
    payload: Mapping[str, Any],
    *,
    created_at: str | None = None,
    engine_version: str | None = None,
) -> dict[str, Any]:
    """Project one Shadow Engine observatory row or shadow artifact."""
    engine_key = str(payload.get("id") or payload.get("engine_id") or payload.get("name") or "unknown")
    run_id = payload.get("last_run_id") or payload.get("run_id")
    artifact_id = str(
        payload.get("artifact_id")
        or (f"shadow:{engine_key}:run:{run_id}" if run_id is not None else _synthetic_id(f"shadow:{engine_key}", payload))
    )
    return pc.build_envelope(
        artifact_id=artifact_id,
        artifact_type="shadow_observation",
        engine_id=f"shadow:{engine_key}",
        engine_version=_version(payload, engine_version),
        created_at=_created_at(payload, created_at),
        parent_artifact_ids=_many(payload.get("parent_artifact_ids")),
        source_ids=_many(payload.get("source_ids"), payload.get("source"), payload.get("workflow")),
        evidence_ids=_many(payload.get("evidence_ids")),
        belief_ids=_many(payload.get("belief_ids")),
        decision_id=payload.get("decision_id"),
        forecast_id=payload.get("forecast_id"),
        verification_id=payload.get("verification_id"),
        authority="shadow",
        prospective=True,
        source_payload=payload,
        domain_provenance={
            **_common_domain("shadow", payload),
            "production_authority": False,
            "observatory_status": payload.get("status"),
        },
    )


ADAPTERS: dict[str, Callable[..., dict[str, Any]]] = {
    "belief_l3a": adapt_belief_l3a,
    "wes": adapt_wes,
    "daily": adapt_daily,
    "brace": adapt_brace,
    "stock_trading": adapt_stock_trading,
    "shadow": adapt_shadow,
}


def adapt(family: str, payload: Mapping[str, Any], **context: Any) -> dict[str, Any]:
    key = str(family or "").strip().lower()
    try:
        adapter = ADAPTERS[key]
    except KeyError as exc:
        raise pc.ProvenanceContractError(f"unsupported provenance compatibility family: {family}") from exc
    source_before = copy.deepcopy(dict(payload))
    envelope = adapter(payload, **context)
    if dict(payload) != source_before:
        raise pc.ProvenanceContractError(f"{key} compatibility adapter mutated source payload")
    pc.validate_envelope(envelope, source_payload=payload)
    return envelope


def project(family: str, payload: Mapping[str, Any], **context: Any) -> dict[str, Any]:
    """Return sidecar projection for migration/testing; source artifact stays byte-semantically unchanged."""
    return {
        "provenance": adapt(family, payload, **context),
        "artifact": copy.deepcopy(dict(payload)),
    }
