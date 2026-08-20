#!/usr/bin/env python3
"""PR #16.1 — Investment Semantics & World State Foundation.

This module creates two pieces of shared investment infrastructure without
changing any engine or Belief decision:

1. a canonical semantic registry so fields named ``confidence`` or
   ``probability`` cannot be consumed as if they meant the same thing; and
2. an append-only World State ledger plus prospective Entity-forecast context
   bindings.

The anti-hindsight boundary is strict. Existing PR15 forecasts present when
PR16.1 activates are cursor-only and are never regime-tagged retroactively.
A new forecast may bind only to a World State snapshot that was already created
and whose source cutoff was already known at or before ``forecast_at``.

World State v1 deliberately freezes the already-produced Broad-Market and
Sector/Factor Belief context. It does not re-fetch raw market data, because an
asynchronous re-fetch would create a different information set from the one
that actually existed before the forecast. Direct market observables can be
added later under a separately reviewed, timestamped adapter contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

MODE = "research_shadow"
SCHEMA_VERSION = "investment-semantics-world-state-v1"
REPORT_VERSION = "investment-semantics-world-state-report-v1"
CONTRACT_VERSION = "investment-semantics-world-state-contract-v1"
SEMANTIC_CONTRACT_VERSION = "investment-metric-semantics-v1"
WORLD_STATE_CONTRACT_VERSION = "investment-world-state-v1"
FORECAST_BINDING_CONTRACT_VERSION = "entity-forecast-world-state-binding-v1"

STATE_FILENAME = "INVESTMENT_WORLD_STATE_RUNTIME_STATE.json"
REPORT_FILENAME = "INVESTMENT_SEMANTICS_WORLD_STATE_REPORT.json"

BROAD_MARKET_IDS: Tuple[str, ...] = (
    "market.rates.supportive",
    "market.liquidity.supportive",
    "market.macro_regime.supportive",
    "market.risk_regime.supportive",
)

SECTOR_FACTOR_IDS: Tuple[str, ...] = (
    "sector.technology.leadership",
    "sector.financials.leadership",
    "sector.health_care.leadership",
    "sector.consumer_discretionary.leadership",
    "sector.consumer_staples.leadership",
    "sector.communication_services.leadership",
    "sector.semiconductors.leadership",
    "factor.growth.leadership",
    "factor.quality.leadership",
    "factor.momentum.leadership",
    "factor.small_cap.leadership",
)

SEMANTIC_TYPES: Mapping[str, Mapping[str, Any]] = {
    "heuristic_signal_strength": {
        "probability_like": False,
        "meaning": "Bounded heuristic strength/conviction of a signal; not a probability of success.",
        "allowed_calibration_status": ("not_applicable",),
    },
    "model_probability": {
        "probability_like": True,
        "meaning": "Probability emitted by a model; calibration must be stated separately and is not implied.",
        "allowed_calibration_status": ("uncalibrated", "prospective_under_calibration", "calibrated"),
    },
    "calibrated_probability": {
        "probability_like": True,
        "meaning": "Probability that has passed an explicit prospective calibration contract for its stated horizon/domain.",
        "allowed_calibration_status": ("calibrated",),
    },
    "belief_probability": {
        "probability_like": True,
        "meaning": "Current Belief probability; calibration status is explicit and never inferred from the field name.",
        "allowed_calibration_status": ("uncalibrated", "prospective_under_calibration", "calibrated"),
    },
    "data_quality_confidence": {
        "probability_like": False,
        "meaning": "Confidence in data completeness/freshness/estimation quality; not outcome probability.",
        "allowed_calibration_status": ("not_applicable",),
    },
    "belief_confidence": {
        "probability_like": False,
        "meaning": "Belief evidence quality/coverage confidence; distinct from the Belief probability itself.",
        "allowed_calibration_status": ("not_applicable",),
    },
    "conviction_score": {
        "probability_like": False,
        "meaning": "Ordinal/bounded decision conviction score; not a probability unless separately transformed and calibrated.",
        "allowed_calibration_status": ("not_applicable",),
    },
}

# This registry is descriptive only. It does not rewrite engine outputs.
SOURCE_FIELD_SEMANTICS: Mapping[str, Mapping[str, str]] = {
    "investments_weekly_v2.signal_strength": {
        "semantic_type": "heuristic_signal_strength",
        "calibration_status": "not_applicable",
    },
    "investments_weekly_v2.confidence": {
        "semantic_type": "heuristic_signal_strength",
        "calibration_status": "not_applicable",
    },
    "brace_portfolio.confidence_score": {
        "semantic_type": "data_quality_confidence",
        "calibration_status": "not_applicable",
    },
    "brace_portfolio.probability_of_reaching_target": {
        "semantic_type": "model_probability",
        "calibration_status": "uncalibrated",
    },
    "belief_core.belief_state.probability": {
        "semantic_type": "belief_probability",
        "calibration_status": "prospective_under_calibration",
    },
    "belief_core.belief_state.confidence": {
        "semantic_type": "belief_confidence",
        "calibration_status": "not_applicable",
    },
    "belief_core.forecast.predicted_probability": {
        "semantic_type": "model_probability",
        "calibration_status": "prospective_under_calibration",
    },
}


def safety_controls() -> Dict[str, bool]:
    return {
        "active_decision_influence": False,
        "score_change": False,
        "candidate_ranking_change": False,
        "target_exposure_change": False,
        "sizing_change": False,
        "veto": False,
        "direction_reversal": False,
        "forced_exit": False,
        "trade_execution": False,
        "policy_output": False,
        "automatic_tuning": False,
        "bounded_influence": False,
        "historical_world_state_backfill": False,
        "historical_forecast_context_backfill": False,
        "retroactive_forecast_mutation": False,
        "with_without_bridge": False,
        "promotion": False,
    }


def capabilities() -> Dict[str, bool]:
    return {
        "canonical_metric_semantics_registry_enabled": True,
        "canonical_world_state_snapshot_enabled": True,
        "broad_market_belief_context_enabled": True,
        "sector_factor_belief_context_enabled": True,
        "prospective_entity_forecast_context_binding_enabled": True,
        "append_only_world_state_ledger_enabled": True,
        "append_only_forecast_binding_ledger_enabled": True,
        "direct_raw_market_observables_in_world_state_enabled": False,
        "engine_output_rewrite_enabled": False,
        "belief_probability_update_enabled": False,
        "with_without_bridge_enabled": False,
        "promotion_gate_enabled": False,
    }


def _assert_safety() -> None:
    bad = [key for key, value in safety_controls().items() if value is not False]
    if bad:
        raise RuntimeError("PR16.1 zero-influence invariant violated: " + ",".join(bad))


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("empty timestamp")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Optional[Path], default: Any) -> Any:
    if path is None:
        return deepcopy(default)
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}-{_sha(payload)[:20]}"


def semantic_envelope(
    value: Optional[float],
    *,
    semantic_type: str,
    calibration_status: str,
    source_system: str,
    source_field: str,
    as_of: str,
    horizon: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    spec = SEMANTIC_TYPES.get(semantic_type)
    if spec is None:
        raise ValueError(f"unknown semantic_type: {semantic_type}")
    allowed = tuple(spec["allowed_calibration_status"])
    if calibration_status not in allowed:
        raise ValueError(
            f"calibration_status={calibration_status} invalid for semantic_type={semantic_type}"
        )
    if value is not None:
        number = float(value)
        if not 0.0 <= number <= 1.0:
            raise ValueError("canonical semantic envelope values must be in [0,1]")
        value = number
    parse_time(as_of)
    return {
        "value": value,
        "semantic_type": semantic_type,
        "probability_like": bool(spec["probability_like"]),
        "calibration_status": calibration_status,
        "source_system": str(source_system),
        "source_field": str(source_field),
        "as_of": iso_z(parse_time(as_of)),
        "horizon": horizon,
        "metadata": dict(metadata or {}),
    }


def semantic_contract() -> Dict[str, Any]:
    return {
        "contract_version": SEMANTIC_CONTRACT_VERSION,
        "types": {
            key: {
                "probability_like": bool(value["probability_like"]),
                "meaning": str(value["meaning"]),
                "allowed_calibration_status": list(value["allowed_calibration_status"]),
            }
            for key, value in sorted(SEMANTIC_TYPES.items())
        },
        "source_field_mappings": {
            key: dict(value) for key, value in sorted(SOURCE_FIELD_SEMANTICS.items())
        },
        "rules": {
            "field_name_confidence_does_not_define_semantics": True,
            "probability_like_must_be_explicit": True,
            "calibrated_label_requires_explicit_calibration_status": True,
            "legacy_engine_fields_are_not_rewritten_in_pr16_1": True,
        },
    }


def _validate_broad_market(report: Mapping[str, Any], now: datetime) -> datetime:
    if str(report.get("mode") or "") != MODE:
        raise ValueError("World State requires broad-market research_shadow report")
    if report.get("active_decision_influence") is not False:
        raise ValueError("World State refuses broad-market report with active influence")
    current = report.get("current_beliefs") or {}
    if not isinstance(current, Mapping):
        raise ValueError("broad-market current_beliefs must be a mapping")
    missing = [belief_id for belief_id in BROAD_MARKET_IDS if belief_id not in current]
    if missing:
        raise ValueError("broad-market taxonomy missing: " + ",".join(missing))
    generated = parse_time(str(report.get("generated_at") or ""))
    if generated > now:
        raise ValueError("future-dated broad-market report rejected")
    return generated


def _validate_sector_factor(report: Mapping[str, Any], now: datetime) -> datetime:
    if str(report.get("mode") or "") != MODE:
        raise ValueError("World State requires sector-factor research_shadow report")
    if report.get("active_decision_influence") is not False:
        raise ValueError("World State refuses sector-factor report with active influence")
    rows = report.get("current_beliefs") or []
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("sector-factor current_beliefs must be a sequence")
    ids = {str(row.get("belief_id") or "") for row in rows if isinstance(row, Mapping)}
    missing = [belief_id for belief_id in SECTOR_FACTOR_IDS if belief_id not in ids]
    if missing:
        raise ValueError("sector-factor taxonomy missing: " + ",".join(missing))
    generated = parse_time(str(report.get("generated_at") or ""))
    if generated > now:
        raise ValueError("future-dated sector-factor report rejected")
    return generated


def _belief_pair(
    *,
    belief_id: str,
    probability: Any,
    confidence: Any,
    as_of: datetime,
    source_system: str,
    audit_status: Any,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "belief_id": belief_id,
        "probability": semantic_envelope(
            None if probability is None else float(probability),
            semantic_type="belief_probability",
            calibration_status="prospective_under_calibration",
            source_system=source_system,
            source_field="probability",
            as_of=iso_z(as_of),
            metadata=metadata,
        ),
        "confidence": semantic_envelope(
            None if confidence is None else float(confidence),
            semantic_type="belief_confidence",
            calibration_status="not_applicable",
            source_system=source_system,
            source_field="confidence",
            as_of=iso_z(as_of),
            metadata=metadata,
        ),
        "audit_status": audit_status,
    }


def build_world_state_snapshot(
    broad_report: Mapping[str, Any],
    sector_report: Mapping[str, Any],
    *,
    created_at: datetime,
) -> Dict[str, Any]:
    now = created_at.astimezone(timezone.utc)
    broad_at = _validate_broad_market(broad_report, now)
    sector_at = _validate_sector_factor(sector_report, now)

    broad_current = broad_report.get("current_beliefs") or {}
    broad_rows: Dict[str, Any] = {}
    for belief_id in BROAD_MARKET_IDS:
        row = broad_current.get(belief_id) or {}
        broad_rows[belief_id] = _belief_pair(
            belief_id=belief_id,
            probability=row.get("probability"),
            confidence=row.get("confidence"),
            as_of=broad_at,
            source_system="brace_broad_market_belief",
            audit_status=row.get("audit_status"),
            metadata={"layer": "broad_market"},
        )

    sector_current = {
        str(row.get("belief_id") or ""): row
        for row in (sector_report.get("current_beliefs") or [])
        if isinstance(row, Mapping)
    }
    sector_rows: Dict[str, Any] = {}
    for belief_id in SECTOR_FACTOR_IDS:
        row = sector_current.get(belief_id) or {}
        sector_rows[belief_id] = _belief_pair(
            belief_id=belief_id,
            probability=row.get("probability"),
            confidence=row.get("confidence"),
            as_of=sector_at,
            source_system="brace_sector_factor_belief",
            audit_status=row.get("audit_status"),
            metadata={
                "layer": row.get("layer"),
                "label": row.get("label"),
                "numerator": row.get("numerator"),
                "denominator": row.get("denominator"),
                "data_available": row.get("data_available"),
            },
        )

    context_as_of = max(broad_at, sector_at)
    source_skew_seconds = abs((broad_at - sector_at).total_seconds())
    missing_probabilities = [
        belief_id
        for belief_id, row in {**broad_rows, **sector_rows}.items()
        if row["probability"]["value"] is None
    ]
    unavailable_sector_proxies = [
        belief_id
        for belief_id, row in sector_rows.items()
        if row["probability"]["metadata"].get("data_available") is False
    ]

    source_contract = {
        "broad_market": {
            "generated_at": iso_z(broad_at),
            "schema_version": broad_report.get("schema_version"),
            "report_version": broad_report.get("report_version") or broad_report.get("schema_version"),
            "content_sha256": _sha(broad_report),
        },
        "sector_factor": {
            "generated_at": iso_z(sector_at),
            "schema_version": sector_report.get("schema_version"),
            "report_version": sector_report.get("report_version"),
            "content_sha256": _sha(sector_report),
        },
    }
    identity_payload = {
        "contract_version": WORLD_STATE_CONTRACT_VERSION,
        "context_as_of": iso_z(context_as_of),
        "source_contract": source_contract,
        "broad_market": broad_rows,
        "sector_factor": sector_rows,
    }
    world_state_id = _stable_id("world", identity_payload)
    snapshot = {
        "world_state_id": world_state_id,
        "contract_version": WORLD_STATE_CONTRACT_VERSION,
        "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
        "created_at": iso_z(now),
        "context_as_of": iso_z(context_as_of),
        "source_cutoff_at": iso_z(context_as_of),
        "source_time_skew_seconds": source_skew_seconds,
        "sources": source_contract,
        "belief_context": {
            "broad_market": broad_rows,
            "sector_factor": sector_rows,
        },
        "direct_market_observables": {
            "included": False,
            "reason": "v1 avoids asynchronous market re-fetch; only already-produced timestamped Belief context is frozen",
        },
        "data_quality": {
            "missing_probability_beliefs": sorted(missing_probabilities),
            "unavailable_sector_factor_proxies": sorted(unavailable_sector_proxies),
            "complete_taxonomy": True,
            "source_time_skew_seconds": source_skew_seconds,
        },
        "provenance": {
            "broad_market_source_sha256": source_contract["broad_market"]["content_sha256"],
            "sector_factor_source_sha256": source_contract["sector_factor"]["content_sha256"],
            "historical_backfill": False,
            "pnl_tuned": False,
            "promotion_authority": False,
        },
    }
    snapshot["immutable_sha256"] = _sha(snapshot)
    return snapshot


def empty_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "contract_version": CONTRACT_VERSION,
        "activated_at": None,
        "last_run_at": None,
        "last_source_fingerprint": None,
        "snapshots": [],
        "seen_pr15_forecast_ids": [],
        "pre_activation_pr15_forecast_ids": [],
        "forecast_context_bindings": {},
        "unbound_forecasts": {},
    }


def _forecast_rows(core_state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = [
        dict(row)
        for row in (core_state.get("forecasts") or [])
        if isinstance(row, Mapping) and row.get("forecast_id")
    ]
    rows.sort(key=lambda row: (str(row.get("forecast_at") or ""), str(row.get("forecast_id") or "")))
    return rows


def _snapshot_by_id(state: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {
        str(row.get("world_state_id")): row
        for row in (state.get("snapshots") or [])
        if isinstance(row, Mapping) and row.get("world_state_id")
    }


def _eligible_snapshot(
    snapshots: Iterable[Mapping[str, Any]],
    *,
    forecast_at: datetime,
) -> Optional[Mapping[str, Any]]:
    eligible: List[Mapping[str, Any]] = []
    for row in snapshots:
        try:
            created = parse_time(str(row.get("created_at") or ""))
            cutoff = parse_time(str(row.get("source_cutoff_at") or row.get("context_as_of") or ""))
        except Exception:
            continue
        if created <= forecast_at and cutoff <= forecast_at:
            eligible.append(row)
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda row: (
            parse_time(str(row.get("source_cutoff_at") or row.get("context_as_of"))),
            parse_time(str(row.get("created_at"))),
            str(row.get("world_state_id")),
        ),
    )


def _bind_new_forecasts(
    state: MutableMapping[str, Any],
    core_state: Mapping[str, Any],
    *,
    now: datetime,
    first_run: bool,
) -> Tuple[int, int]:
    forecasts = _forecast_rows(core_state)
    seen = set(str(x) for x in state.get("seen_pr15_forecast_ids") or [])
    pre_activation = set(str(x) for x in state.get("pre_activation_pr15_forecast_ids") or [])
    bindings: MutableMapping[str, Any] = state.setdefault("forecast_context_bindings", {})
    unbound: MutableMapping[str, Any] = state.setdefault("unbound_forecasts", {})
    activated_at = parse_time(str(state.get("activated_at")))

    if first_run:
        # PR16.1 does not reconstruct historical regimes for already-existing
        # forecasts. They are permanently cursor-only for this contract.
        for row in forecasts:
            forecast_id = str(row["forecast_id"])
            seen.add(forecast_id)
            pre_activation.add(forecast_id)
        state["seen_pr15_forecast_ids"] = sorted(seen)
        state["pre_activation_pr15_forecast_ids"] = sorted(pre_activation)
        return 0, 0

    bound_now = 0
    unbound_now = 0
    snapshots = list(state.get("snapshots") or [])
    for row in forecasts:
        forecast_id = str(row["forecast_id"])
        if forecast_id in seen:
            continue
        seen.add(forecast_id)
        try:
            forecast_at = parse_time(str(row.get("forecast_at") or ""))
        except Exception:
            unbound[forecast_id] = {
                "forecast_id": forecast_id,
                "status": "invalid_forecast_at",
                "recorded_at": iso_z(now),
                "terminal_no_retroactive_binding": True,
            }
            unbound_now += 1
            continue
        if forecast_at > now:
            unbound[forecast_id] = {
                "forecast_id": forecast_id,
                "forecast_at": iso_z(forecast_at),
                "status": "future_dated_forecast_rejected",
                "recorded_at": iso_z(now),
                "terminal_no_retroactive_binding": True,
            }
            unbound_now += 1
            continue
        if forecast_at < activated_at:
            unbound[forecast_id] = {
                "forecast_id": forecast_id,
                "forecast_at": iso_z(forecast_at),
                "status": "forecast_predates_pr16_1_activation",
                "recorded_at": iso_z(now),
                "terminal_no_retroactive_binding": True,
            }
            unbound_now += 1
            continue
        snapshot = _eligible_snapshot(snapshots, forecast_at=forecast_at)
        if snapshot is None:
            unbound[forecast_id] = {
                "forecast_id": forecast_id,
                "forecast_at": iso_z(forecast_at),
                "status": "no_pre_forecast_world_state_snapshot",
                "recorded_at": iso_z(now),
                "terminal_no_retroactive_binding": True,
            }
            unbound_now += 1
            continue
        world_state_id = str(snapshot["world_state_id"])
        binding_payload = {
            "contract_version": FORECAST_BINDING_CONTRACT_VERSION,
            "forecast_id": forecast_id,
            "belief_id": row.get("belief_id"),
            "forecast_at": iso_z(forecast_at),
            "world_state_id": world_state_id,
            "world_state_created_at": snapshot.get("created_at"),
            "world_state_context_as_of": snapshot.get("context_as_of"),
            "world_state_source_cutoff_at": snapshot.get("source_cutoff_at"),
        }
        binding = {
            **binding_payload,
            "binding_id": _stable_id("forecast-world", binding_payload),
            "bound_at": iso_z(now),
            "prospective": True,
            "retroactive": False,
            "forecast_mutated": False,
            "historical_backfill": False,
            "promotion_authority": False,
        }
        bindings[forecast_id] = binding
        bound_now += 1

    state["seen_pr15_forecast_ids"] = sorted(seen)
    state["pre_activation_pr15_forecast_ids"] = sorted(pre_activation)
    return bound_now, unbound_now


def _assert_append_only(previous: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    previous_snapshots = _snapshot_by_id(previous)
    current_snapshots = _snapshot_by_id(current)
    for key, value in previous_snapshots.items():
        if key not in current_snapshots or current_snapshots[key] != value:
            raise RuntimeError(f"World State snapshot history mutation detected: {key}")
    for field in ("forecast_context_bindings", "unbound_forecasts"):
        before = previous.get(field) or {}
        after = current.get(field) or {}
        for key, value in before.items():
            if key not in after or after[key] != value:
                raise RuntimeError(f"PR16.1 append-only mutation detected in {field}: {key}")


def run(
    state_dir: Path,
    *,
    broad_market_report_path: Path,
    sector_factor_report_path: Path,
    pr15_core_state_path: Optional[Path] = None,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    _assert_safety()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_z = iso_z(now)
    state_dir = Path(state_dir)
    state_path = state_dir / STATE_FILENAME
    report_path = state_dir / REPORT_FILENAME

    broad = _read_json(broad_market_report_path, {})
    sector = _read_json(sector_factor_report_path, {})
    core_state = _read_json(pr15_core_state_path, {})

    previous = _read_json(state_path, empty_state())
    state = deepcopy(previous)
    if str(state.get("schema_version") or "") != SCHEMA_VERSION:
        raise ValueError("PR16.1 state schema mismatch")
    if str(state.get("contract_version") or "") != CONTRACT_VERSION:
        raise ValueError("PR16.1 state contract mismatch")

    first_run = not bool(state.get("activated_at"))
    if first_run:
        state["activated_at"] = now_z

    snapshot = build_world_state_snapshot(broad, sector, created_at=now)
    source_fingerprint = _sha({
        "broad_market": snapshot["sources"]["broad_market"]["content_sha256"],
        "sector_factor": snapshot["sources"]["sector_factor"]["content_sha256"],
    })
    existing_ids = {str(row.get("world_state_id")) for row in (state.get("snapshots") or []) if isinstance(row, Mapping)}
    new_snapshot = snapshot["world_state_id"] not in existing_ids
    if new_snapshot:
        state.setdefault("snapshots", []).append(snapshot)

    bound_now, unbound_now = _bind_new_forecasts(
        state,
        core_state,
        now=now,
        first_run=first_run,
    )
    state["last_source_fingerprint"] = source_fingerprint
    state["last_run_at"] = now_z
    _assert_append_only(previous, state)
    _write_json(state_path, state)

    bindings = state.get("forecast_context_bindings") or {}
    unbound = state.get("unbound_forecasts") or {}
    pre_activation = state.get("pre_activation_pr15_forecast_ids") or []
    latest = state.get("snapshots", [])[-1] if state.get("snapshots") else None

    report = {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "mode": MODE,
        "generated_at": now_z,
        "purpose": "Canonical investment metric semantics and prospective World State context only.",
        "active_decision_influence": False,
        "semantic_contract": semantic_contract(),
        "world_state_contract": {
            "contract_version": WORLD_STATE_CONTRACT_VERSION,
            "latest_world_state_id": None if latest is None else latest.get("world_state_id"),
            "snapshot_count": len(state.get("snapshots") or []),
            "new_snapshot_this_run": new_snapshot,
            "broad_market_context_included": True,
            "sector_factor_context_included": True,
            "direct_raw_market_observables_included": False,
            "world_state_must_preexist_forecast_for_binding": True,
        },
        "forecast_context_contract": {
            "contract_version": FORECAST_BINDING_CONTRACT_VERSION,
            "first_run_activation_only_for_existing_forecasts": True,
            "pre_activation_forecasts_cursor_only": True,
            "binding_requires_world_state_created_at_lte_forecast_at": True,
            "binding_requires_source_cutoff_at_lte_forecast_at": True,
            "terminal_unbound_records_are_never_filled_later": True,
            "forecast_core_record_mutation": False,
            "bindings_total": len(bindings),
            "unbound_total": len(unbound),
            "pre_activation_forecasts_total": len(pre_activation),
            "new_bindings_this_run": bound_now,
            "new_unbound_this_run": unbound_now,
        },
        "latest_world_state": latest,
        "forecast_context_bindings": [bindings[key] for key in sorted(bindings)],
        "unbound_forecasts": [unbound[key] for key in sorted(unbound)],
        "anti_hindsight": {
            "historical_world_state_backfill": False,
            "historical_forecast_context_backfill": False,
            "pre_activation_forecasts_are_not_regime_tagged": True,
            "world_state_must_exist_before_forecast": True,
            "source_cutoff_must_not_exceed_forecast_at": True,
            "retroactive_forecast_mutation": False,
        },
        "research_boundary": {
            "world_state_is_context_not_alpha": True,
            "semantic_registry_is_metadata_not_engine_logic": True,
            "causal_graph_enabled": False,
            "marginal_information_value_enabled": False,
            "engine_specific_trust_enabled": False,
            "disagreement_topology_enabled": False,
            "next_stage": "PR17 BRACE-Entity prospective shadow WITH/WITHOUT bridge",
        },
        "promotion": {
            "with_without_required": True,
            "automatic_promotion": False,
            "promotion_authority": False,
            "status": "NOT_ELIGIBLE_FOR_PROMOTION_REVIEW",
        },
        "capabilities": capabilities(),
        "safety_controls": safety_controls(),
    }
    _write_json(report_path, report)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--broad-market-report", type=Path, required=True)
    parser.add_argument("--sector-factor-report", type=Path, required=True)
    parser.add_argument("--pr15-core-state", type=Path)
    parser.add_argument("--now", help="Optional ISO timestamp for deterministic validation")
    args = parser.parse_args(argv)
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    report = run(
        args.state_dir,
        broad_market_report_path=args.broad_market_report,
        sector_factor_report_path=args.sector_factor_report,
        pr15_core_state_path=args.pr15_core_state,
        as_of=now,
    )
    print(json.dumps({
        "mode": report["mode"],
        "world_state": report["world_state_contract"],
        "forecast_context": report["forecast_context_contract"],
        "promotion": report["promotion"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
