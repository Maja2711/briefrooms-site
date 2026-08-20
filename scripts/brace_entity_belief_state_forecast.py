#!/usr/bin/env python3
"""PR #15 — Entity Belief State & Forecast Foundation.

This layer is the first Company/Entity runtime allowed to update Belief Core
probabilities and freeze prospective Entity forecasts. It consumes only PR14's
reviewed deterministic Entity Evidence.

Hard boundaries:
- first PR15 run is activation-only: existing PR14 Evidence seeds cursors only;
- no historical Evidence ingest and no historical forecast backfill;
- only PR14 v1 support/oppose Evidence for enabled dimensions can update state;
- one live prospective forecast per belief to limit overlap/dependence;
- forecast outcome is the first future comparable PR14 interpretation inside a
  frozen 120-day window: support=True, oppose=False, neutral=censored;
- neutral/no-outcome cases never become fake binary calibration observations;
- no BRACE score/ranking/exposure/sizing/veto/execution influence;
- no WITH/WITHOUT bridge and no promotion authority.

Fundamental Entity forecasts are explicitly quarterly/reporting-horizon objects;
they are not calibrated against next-day stock returns.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

try:
    from belief_core import BeliefCore, BeliefDefinition, Evidence, ForecastSnapshot, iso_z, parse_time
    from brace_entity_evidence_interpretation import (
        CONTRACT_VERSION as PR14_CONTRACT_VERSION,
        DIRECT_CONTRACTS,
        MARGIN_CONTRACT,
        SCHEMA_VERSION as PR14_SCHEMA_VERSION,
    )
except ModuleNotFoundError:  # package imports in unit tests
    from scripts.belief_core import BeliefCore, BeliefDefinition, Evidence, ForecastSnapshot, iso_z, parse_time
    from scripts.brace_entity_evidence_interpretation import (
        CONTRACT_VERSION as PR14_CONTRACT_VERSION,
        DIRECT_CONTRACTS,
        MARGIN_CONTRACT,
        SCHEMA_VERSION as PR14_SCHEMA_VERSION,
    )

MODE = "research_shadow"
SCHEMA_VERSION = "brace-entity-belief-state-forecast-v1"
REPORT_VERSION = "brace-entity-belief-state-forecast-report-v1"
CONTRACT_VERSION = "entity-belief-forecast-contract-v1"
STATE_FILENAME = "ENTITY_BELIEF_FORECAST_RUNTIME_STATE.json"
REPORT_FILENAME = "BRACE_ENTITY_BELIEF_STATE_FORECAST_REPORT.json"
INTERPRETATION_STATE_FILENAME = "ENTITY_EVIDENCE_INTERPRETATION_STATE.json"
BELIEF_CORE_DIRNAME = "belief_core"

# Frozen semantic choices. They are not tuned against PnL.
FORECAST_HORIZON_DAYS = 120
FORECAST_HORIZON_HOURS = float(FORECAST_HORIZON_DAYS * 24)
EVIDENCE_HALF_LIFE_DAYS = 180
EVIDENCE_HALF_LIFE_HOURS = float(EVIDENCE_HALF_LIFE_DAYS * 24)
FORECAST_REGIME = "entity_fundamental_reporting_v1"

DIMENSION_CONFIG: Mapping[str, Mapping[str, Any]] = {
    "revenue_durability": {
        "claim": "{entity} reported revenue durability will remain supportive at the next comparable reporting outcome.",
        "outcome_rule": "next_comparable_pr14_revenue_durability_support_vs_oppose_within_120d_v1",
        "contract_id": str(DIRECT_CONTRACTS["revenue_durability"]["contract_id"]),
        "sector": None,
    },
    "earnings_momentum": {
        "claim": "{entity} reported earnings momentum will remain supportive at the next comparable reporting outcome.",
        "outcome_rule": "next_comparable_pr14_earnings_momentum_support_vs_oppose_within_120d_v1",
        "contract_id": str(DIRECT_CONTRACTS["earnings_momentum"]["contract_id"]),
        "sector": None,
    },
    "margin_trajectory": {
        "claim": "{entity} reported operating-margin trajectory will remain supportive at the next comparable reporting outcome.",
        "outcome_rule": "next_comparable_pr14_margin_trajectory_support_vs_oppose_within_120d_v1",
        "contract_id": str(MARGIN_CONTRACT["contract_id"]),
        "sector": None,
    },
    "net_interest_income_durability": {
        "claim": "{entity} reported net-interest-income durability will remain supportive at the next comparable reporting outcome.",
        "outcome_rule": "next_comparable_pr14_nii_durability_support_vs_oppose_within_120d_v1",
        "contract_id": str(DIRECT_CONTRACTS["net_interest_income_durability"]["contract_id"]),
        "sector": "Financials",
    },
}


def safety_controls() -> Dict[str, bool]:
    """Controls that must remain false in PR15.

    Shadow Belief-state update and forecast capture are capabilities, not safety
    violations, so they are intentionally not represented as false controls here.
    """
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
        "historical_evidence_backfill": False,
        "historical_forecast_backfill": False,
        "next_day_stock_return_outcome": False,
        "overlapping_live_forecasts_per_belief": False,
        "neutral_as_binary_outcome": False,
        "entity_promotion": False,
    }


def capabilities() -> Dict[str, bool]:
    return {
        "entity_belief_core_state_update_enabled": True,
        "pr14_directional_evidence_ingestion_enabled": True,
        "prospective_entity_forecast_capture_enabled": True,
        "deterministic_forecast_outcome_resolution_enabled": True,
        "entity_calibration_memory_enabled": True,
        "one_live_forecast_per_belief_enabled": True,
        "llm_interpretation_enabled": False,
        "brace_entity_bridge_enabled": False,
        "with_without_bridge_enabled": False,
        "promotion_gate_enabled": False,
    }


def promotion_evidence_standard() -> Dict[str, Any]:
    return {
        "with_without_required": True,
        "paired_prospective_counterfactual_required": True,
        "effective_n_required": True,
        "effective_n_threshold_defined_here": False,
        "stable_uplift_required": True,
        "multi_regime_robustness_required": True,
        "concentration_check_required": True,
        "drawdown_not_materially_worse_required": True,
        "tail_risk_not_materially_worse_required": True,
        "belief_calibration_required": True,
        "drift_check_required": True,
        "data_quality_and_provenance_required": True,
        "anti_hindsight_required": True,
        "automatic_promotion": False,
        "review_output_only": "ELIGIBLE_FOR_PROMOTION_REVIEW",
    }


def _assert_safety() -> None:
    bad = [key for key, value in safety_controls().items() if value is not False]
    if bad:
        raise RuntimeError("PR15 zero-BRACE-influence invariant violated: " + ",".join(bad))


def _read_json(path: Path, default: Any) -> Any:
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


def _canonical_sha256(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def empty_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "contract_version": CONTRACT_VERSION,
        "first_run_at": None,
        "last_run_at": None,
        "seen_pr14_evidence_ids": [],
        "forecast_closures": {},
        "source_window_lineage": {},
        "last_source_fingerprint": None,
    }


def _belief_parts(belief_id: str) -> Optional[Tuple[str, str]]:
    prefix = "entity."
    text = str(belief_id or "")
    if not text.startswith(prefix) or "." not in text[len(prefix):]:
        return None
    entity_id, dimension = text[len(prefix):].rsplit(".", 1)
    if not entity_id or not dimension:
        return None
    return entity_id, dimension


def _eligible_dimensions(entity_state: Mapping[str, Any]) -> Tuple[str, ...]:
    sector = str(entity_state.get("sector") or "")
    rows = []
    for dimension, config in DIMENSION_CONFIG.items():
        required_sector = config.get("sector")
        if required_sector and str(required_sector) != sector:
            continue
        rows.append(dimension)
    return tuple(sorted(rows))


def _definition(entity_id: str, entity_state: Mapping[str, Any], dimension: str) -> BeliefDefinition:
    config = DIMENSION_CONFIG[dimension]
    return BeliefDefinition(
        belief_id=f"entity.{entity_id}.{dimension}",
        claim=str(config["claim"]).format(entity=entity_id.upper()),
        prior_probability=0.50,
        half_life_hours=EVIDENCE_HALF_LIFE_HOURS,
        entity=entity_id,
        domain="entity_fundamentals",
        alternative_group=None,
        tags=("BRACE", "entity", "company_fundamental", dimension, CONTRACT_VERSION),
        horizon_hours=FORECAST_HORIZON_HOURS,
        outcome_rule=str(config["outcome_rule"]),
    )


def _register_definitions(core: BeliefCore, entity_states: Mapping[str, Any]) -> int:
    definitions: List[BeliefDefinition] = []
    for entity_id, raw in sorted(entity_states.items()):
        if not isinstance(raw, Mapping):
            continue
        for dimension in _eligible_dimensions(raw):
            definitions.append(_definition(str(entity_id), raw, dimension))
    before = len(core.definitions)
    core.register_beliefs(definitions)
    return len(core.definitions) - before


def _validate_source_state(source: Mapping[str, Any]) -> None:
    if not source:
        raise ValueError("PR15 requires a non-empty PR14 interpretation state")
    if str(source.get("schema_version") or "") != PR14_SCHEMA_VERSION:
        raise ValueError("PR15 requires the reviewed PR14 schema version")
    if str(source.get("contract_version") or "") != PR14_CONTRACT_VERSION:
        raise ValueError("PR15 requires the reviewed PR14 interpretation contract version")
    if str(source.get("mode") or "") != MODE:
        raise ValueError("PR15 accepts PR14 research_shadow state only")


def _source_entities(source: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(key): dict(value)
        for key, value in (source.get("entities") or {}).items()
        if isinstance(value, Mapping)
    }


def _evidence_contract_matches(row: Mapping[str, Any], entity_states: Mapping[str, Any]) -> Tuple[bool, str]:
    parts = _belief_parts(str(row.get("belief_id") or ""))
    if parts is None:
        return False, "belief_id_not_entity_dimension"
    entity_id, dimension = parts
    entity_state = entity_states.get(entity_id)
    if not isinstance(entity_state, Mapping):
        return False, "entity_missing_from_pr14_state"
    if dimension not in _eligible_dimensions(entity_state):
        return False, "dimension_not_enabled_for_entity"
    config = DIMENSION_CONFIG.get(dimension)
    if config is None:
        return False, "dimension_not_enabled_in_pr15"
    metadata = dict(row.get("metadata") or {})
    if str(metadata.get("contract_version") or "") != PR14_CONTRACT_VERSION:
        return False, "pr14_contract_version_mismatch"
    if str(metadata.get("contract_id") or "") != str(config["contract_id"]):
        return False, "pr14_contract_id_mismatch"
    if metadata.get("pnl_tuned") is not False:
        return False, "pnl_tuned_contract_not_allowed"
    if metadata.get("promotion_authority") is not False:
        return False, "promotion_authority_not_allowed"
    if str(row.get("source_type") or "") != "derived":
        return False, "source_type_must_be_derived"
    if str(row.get("evidence_type") or "") != "entity_fundamental_yoy":
        return False, "unsupported_evidence_type"
    if int(row.get("direction") or 0) not in (-1, 1):
        return False, "direction_invalid"
    if not (row.get("derived_from") or []):
        return False, "derived_lineage_missing"
    return True, "ok"


def _eligible_evidence(
    source: Mapping[str, Any], entity_states: Mapping[str, Any], now: datetime
) -> Tuple[List[Evidence], List[Dict[str, Any]]]:
    out: List[Evidence] = []
    issues: List[Dict[str, Any]] = []
    for raw in source.get("evidence", []) or []:
        if not isinstance(raw, Mapping):
            continue
        ok, reason = _evidence_contract_matches(raw, entity_states)
        if not ok:
            issues.append({
                "code": "pr14_evidence_rejected",
                "evidence_id": str(raw.get("evidence_id") or ""),
                "belief_id": str(raw.get("belief_id") or ""),
                "reason": reason,
            })
            continue
        try:
            evidence = Evidence.from_dict(raw)
        except Exception as exc:
            issues.append({
                "code": "invalid_pr14_evidence",
                "evidence_id": str(raw.get("evidence_id") or ""),
                "message": f"{type(exc).__name__}: {str(exc)[:240]}",
            })
            continue
        if parse_time(evidence.observed_at) > now:
            issues.append({
                "code": "future_dated_pr14_evidence",
                "evidence_id": evidence.evidence_id,
                "belief_id": evidence.belief_id,
            })
            continue
        out.append(evidence)
    out.sort(key=lambda row: (row.observed_at, row.belief_id, row.evidence_id))
    return out, issues


def _terminal_interpretations(
    source: Mapping[str, Any], entity_states: Mapping[str, Any], now: datetime
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    for raw in source.get("interpretations", []) or []:
        if not isinstance(raw, Mapping):
            continue
        status = str(raw.get("status") or "")
        if status not in {"support", "oppose", "neutral"}:
            continue
        belief_id = str(raw.get("belief_id") or "")
        parts = _belief_parts(belief_id)
        if parts is None:
            continue
        entity_id, dimension = parts
        entity_state = entity_states.get(entity_id)
        config = DIMENSION_CONFIG.get(dimension)
        if not isinstance(entity_state, Mapping) or config is None or dimension not in _eligible_dimensions(entity_state):
            continue
        if str(raw.get("contract_id") or "") != str(config["contract_id"]):
            issues.append({
                "code": "terminal_interpretation_contract_mismatch",
                "interpretation_id": str(raw.get("interpretation_id") or ""),
                "belief_id": belief_id,
            })
            continue
        if raw.get("pnl_tuned") is True:
            issues.append({
                "code": "terminal_interpretation_pnl_tuned_rejected",
                "interpretation_id": str(raw.get("interpretation_id") or ""),
                "belief_id": belief_id,
            })
            continue
        try:
            computed = parse_time(str(raw.get("computed_at") or ""))
        except Exception:
            issues.append({
                "code": "terminal_interpretation_time_invalid",
                "interpretation_id": str(raw.get("interpretation_id") or ""),
                "belief_id": belief_id,
            })
            continue
        if computed > now:
            issues.append({
                "code": "future_dated_terminal_interpretation",
                "interpretation_id": str(raw.get("interpretation_id") or ""),
                "belief_id": belief_id,
            })
            continue
        row = dict(raw)
        row["computed_at"] = iso_z(computed)
        rows.append(row)
    rows.sort(key=lambda row: (str(row.get("computed_at")), str(row.get("belief_id")), str(row.get("interpretation_id"))))
    return rows, issues


def _source_fingerprint(source: Mapping[str, Any]) -> str:
    return _canonical_sha256({
        "last_run_at": source.get("last_run_at"),
        "contract_version": source.get("contract_version"),
        "evidence_ids": sorted(
            str(row.get("evidence_id"))
            for row in source.get("evidence", []) or []
            if isinstance(row, Mapping)
        ),
        "interpretation_ids": sorted(
            str(row.get("interpretation_id"))
            for row in source.get("interpretations", []) or []
            if isinstance(row, Mapping)
        ),
        "entities": {
            key: {
                "current_status": value.get("current_status"),
                "source_window_opened_at": value.get("source_window_opened_at"),
                "sector": value.get("sector"),
            }
            for key, value in sorted(_source_entities(source).items())
        },
    })


def _sync_source_windows(runtime: MutableMapping[str, Any], entity_states: Mapping[str, Any], now_z: str) -> None:
    lineage = runtime.setdefault("source_window_lineage", {})
    for entity_id, entity in sorted(entity_states.items()):
        window = entity.get("source_window_opened_at")
        previous = dict(lineage.get(entity_id) or {})
        if previous.get("current_window_opened_at") != window:
            previous["prior_window_opened_at"] = previous.get("current_window_opened_at")
            previous["current_window_opened_at"] = window
            previous["window_change_observed_at"] = now_z
            previous["window_change_count"] = int(previous.get("window_change_count") or 0) + 1
        previous["current_status"] = entity.get("current_status")
        previous["sector"] = entity.get("sector")
        previous["last_synced_at"] = now_z
        lineage[entity_id] = previous


def _closed_forecast_ids(runtime: Mapping[str, Any]) -> set[str]:
    return set(str(x) for x in (runtime.get("forecast_closures") or {}).keys())


def _verified_forecast_ids(core: BeliefCore) -> set[str]:
    return {str(v.forecast_id) for v in core.verifications.values() if v.forecast_id}


def _outcome_candidate(
    forecast: ForecastSnapshot,
    interpretations: Sequence[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    forecast_at = parse_time(forecast.forecast_at)
    target_at = parse_time(forecast.target_at)
    source_window = forecast.metadata.get("source_window_opened_at")
    lower = forecast_at
    if source_window:
        try:
            lower = max(lower, parse_time(str(source_window)))
        except Exception:
            pass
    candidates = [
        row for row in interpretations
        if str(row.get("belief_id") or "") == forecast.belief_id
        and parse_time(str(row.get("computed_at"))) > lower
        and parse_time(str(row.get("computed_at"))) <= target_at
        and str(row.get("status") or "") in {"support", "oppose", "neutral"}
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: (parse_time(str(row.get("computed_at"))), str(row.get("interpretation_id"))))


def _resolve_due_forecasts(
    core: BeliefCore,
    runtime: MutableMapping[str, Any],
    interpretations: Sequence[Mapping[str, Any]],
    now: datetime,
) -> Tuple[int, int, int]:
    closures: MutableMapping[str, Any] = runtime.setdefault("forecast_closures", {})
    verified_ids = _verified_forecast_ids(core)
    new_verifications = 0
    new_censored = 0
    new_no_outcome = 0
    for forecast in sorted(core.forecasts.values(), key=lambda f: (f.target_at, f.forecast_id)):
        if forecast.forecast_id in closures:
            continue
        if forecast.forecast_id in verified_ids:
            verification = next(v for v in core.verifications.values() if v.forecast_id == forecast.forecast_id)
            closures[forecast.forecast_id] = {
                "forecast_id": forecast.forecast_id,
                "belief_id": forecast.belief_id,
                "status": "verified_existing",
                "closed_at": verification.verified_at,
                "verification_id": verification.verification_id,
            }
            continue
        if parse_time(forecast.target_at) > now:
            continue
        candidate = _outcome_candidate(forecast, interpretations)
        if candidate is None:
            closures[forecast.forecast_id] = {
                "forecast_id": forecast.forecast_id,
                "belief_id": forecast.belief_id,
                "status": "no_comparable_outcome_by_target",
                "closed_at": iso_z(now),
                "target_at": forecast.target_at,
                "calibration_eligible": False,
            }
            new_no_outcome += 1
            continue
        status = str(candidate.get("status"))
        if status == "neutral":
            closures[forecast.forecast_id] = {
                "forecast_id": forecast.forecast_id,
                "belief_id": forecast.belief_id,
                "status": "censored_neutral",
                "closed_at": iso_z(now),
                "target_at": forecast.target_at,
                "outcome_interpretation_id": candidate.get("interpretation_id"),
                "outcome_observed_at": candidate.get("computed_at"),
                "calibration_eligible": False,
            }
            new_censored += 1
            continue
        outcome = status == "support"
        verification = core.verify_forecast(
            forecast.forecast_id,
            outcome,
            verified_at=now,
            note="PR15 deterministic next-comparable-report outcome from PR14.",
            outcome_source="PR14 deterministic entity interpretation",
            outcome_ref=str(candidate.get("interpretation_id") or ""),
        )
        closures[forecast.forecast_id] = {
            "forecast_id": forecast.forecast_id,
            "belief_id": forecast.belief_id,
            "status": "verified_support" if outcome else "verified_oppose",
            "closed_at": verification.verified_at,
            "target_at": forecast.target_at,
            "outcome_interpretation_id": candidate.get("interpretation_id"),
            "outcome_observed_at": candidate.get("computed_at"),
            "verification_id": verification.verification_id,
            "calibration_eligible": True,
        }
        new_verifications += 1
    return new_verifications, new_censored, new_no_outcome


def _has_live_forecast(core: BeliefCore, runtime: Mapping[str, Any], belief_id: str, now: datetime) -> bool:
    closed = _closed_forecast_ids(runtime)
    verified = _verified_forecast_ids(core)
    for forecast in core.forecasts.values():
        if forecast.belief_id != belief_id:
            continue
        if forecast.forecast_id in closed or forecast.forecast_id in verified:
            continue
        if parse_time(forecast.target_at) > now:
            return True
    return False


def _capture_for_affected(
    core: BeliefCore,
    runtime: Mapping[str, Any],
    entity_states: Mapping[str, Any],
    affected_beliefs: Iterable[str],
    now: datetime,
    new_evidence_ids_by_belief: Mapping[str, Sequence[str]],
) -> List[ForecastSnapshot]:
    out: List[ForecastSnapshot] = []
    target = now + timedelta(days=FORECAST_HORIZON_DAYS)
    for belief_id in sorted(set(affected_beliefs)):
        parts = _belief_parts(belief_id)
        if parts is None:
            continue
        entity_id, dimension = parts
        entity = entity_states.get(entity_id)
        if not isinstance(entity, Mapping) or entity.get("current_status") != "active":
            continue
        if _has_live_forecast(core, runtime, belief_id, now):
            continue
        state = core.beliefs.get(belief_id)
        if state is None:
            continue
        source_window = entity.get("source_window_opened_at")
        snap = core.capture_forecast(
            belief_id,
            as_of=now,
            target_at=target,
            regime=FORECAST_REGIME,
            metadata={
                "contract_version": CONTRACT_VERSION,
                "pr14_contract_version": PR14_CONTRACT_VERSION,
                "dimension": dimension,
                "sector": entity.get("sector"),
                "reporting_regime": entity.get("reporting_regime"),
                "source_window_opened_at": source_window,
                "origin_new_evidence_ids": list(new_evidence_ids_by_belief.get(belief_id) or ()),
                "outcome_semantics": "first future comparable PR14 support/oppose/neutral inside frozen window",
                "support_outcome": True,
                "oppose_outcome": False,
                "neutral_outcome": "censored_not_binary",
                "pnl_tuned": False,
                "historical_backfill": False,
                "engine_influence": False,
                "promotion_authority": False,
            },
        )
        out.append(snap)
    return out


def _closure_status_counts(runtime: Mapping[str, Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in (runtime.get("forecast_closures") or {}).values():
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "unknown")
        out[status] = out.get(status, 0) + 1
    return out


def _active_core_forecasts(core: BeliefCore, runtime: Mapping[str, Any], now: datetime) -> List[Dict[str, Any]]:
    closed = _closed_forecast_ids(runtime)
    verified = _verified_forecast_ids(core)
    rows = []
    for forecast in core.forecasts.values():
        if forecast.forecast_id in closed or forecast.forecast_id in verified:
            continue
        payload = forecast.to_dict()
        payload["due"] = parse_time(forecast.target_at) <= now
        rows.append(payload)
    return sorted(rows, key=lambda row: (row["target_at"], row["forecast_id"]))


def run(
    state_dir: Path,
    *,
    interpretation_state_path: Path,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    _assert_safety()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_z = iso_z(now)
    state_dir = Path(state_dir)
    runtime_path = state_dir / STATE_FILENAME
    report_path = state_dir / REPORT_FILENAME
    core_dir = state_dir / BELIEF_CORE_DIRNAME

    source = _read_json(interpretation_state_path, {})
    _validate_source_state(source)
    entity_states = _source_entities(source)

    runtime = _read_json(runtime_path, empty_state())
    runtime["schema_version"] = SCHEMA_VERSION
    runtime["mode"] = MODE
    if runtime.get("contract_version") not in {None, CONTRACT_VERSION}:
        raise ValueError("PR15 contract version changed; explicit migration/review is required")
    runtime["contract_version"] = CONTRACT_VERSION
    runtime.setdefault("seen_pr14_evidence_ids", [])
    runtime.setdefault("forecast_closures", {})
    runtime.setdefault("source_window_lineage", {})
    first_run = not bool(runtime.get("first_run_at"))
    if first_run:
        runtime["first_run_at"] = now_z
    _sync_source_windows(runtime, entity_states, now_z)

    core = BeliefCore(core_dir)
    new_definitions = _register_definitions(core, entity_states)
    eligible, evidence_issues = _eligible_evidence(source, entity_states, now)
    terminal, interpretation_issues = _terminal_interpretations(source, entity_states, now)
    source_issues = evidence_issues + interpretation_issues

    seen = set(str(x) for x in runtime.get("seen_pr14_evidence_ids") or [])
    new_evidence: List[Evidence] = []
    new_evidence_ids_by_belief: Dict[str, List[str]] = {}
    new_verifications = new_censored = new_no_outcome = 0
    new_forecasts: List[ForecastSnapshot] = []

    if first_run:
        # Global PR15 activation boundary: PR14 Evidence that already exists is not
        # retroactively ingested. Belief states are initialized from prior only.
        seen.update(row.evidence_id for row in eligible)
        core.recompute(now)
    else:
        # Close due forecasts using only outcomes whose interpretation timestamps
        # fall inside the forecast's frozen future window.
        new_verifications, new_censored, new_no_outcome = _resolve_due_forecasts(
            core, runtime, terminal, now
        )
        for evidence in eligible:
            if evidence.evidence_id in seen:
                continue
            new_evidence.append(evidence)
            new_evidence_ids_by_belief.setdefault(evidence.belief_id, []).append(evidence.evidence_id)
            seen.add(evidence.evidence_id)
        if new_evidence:
            core.ingest(new_evidence)
        core.recompute(now)
        new_forecasts = _capture_for_affected(
            core,
            runtime,
            entity_states,
            (row.belief_id for row in new_evidence),
            now,
            new_evidence_ids_by_belief,
        )

    # Ensure definitions/priors are durable even when no evidence was ingested.
    core.save()
    core.write_dashboard(now)

    runtime["seen_pr14_evidence_ids"] = sorted(seen)
    runtime["last_run_at"] = now_z
    runtime["last_source_fingerprint"] = _source_fingerprint(source)
    runtime["source_last_run_at"] = source.get("last_run_at")
    _write_json(runtime_path, runtime)

    active_entities = sum(1 for row in entity_states.values() if row.get("current_status") == "active")
    dormant_entities = sum(1 for row in entity_states.values() if row.get("current_status") == "dormant")
    active_forecasts = _active_core_forecasts(core, runtime, now)
    closure_counts = _closure_status_counts(runtime)
    calibration = core.calibration_summary()
    belief_states = [core.beliefs[key].to_dict() for key in sorted(core.beliefs)]
    forecasts = [core.forecasts[key].to_dict() for key in sorted(core.forecasts)]
    verifications = [core.verifications[key].to_dict() for key in sorted(core.verifications)]

    report = {
        "report_version": REPORT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "generated_at": now_z,
        "mode": MODE,
        "active_decision_influence": False,
        "purpose": "Prospective Company/Entity Belief state, reporting-horizon forecasts and calibration memory from PR14 evidence; no BRACE influence.",
        "source_contract": {
            "input": "PR14 ENTITY_EVIDENCE_INTERPRETATION_STATE.json",
            "required_pr14_schema_version": PR14_SCHEMA_VERSION,
            "required_pr14_contract_version": PR14_CONTRACT_VERSION,
            "directional_evidence_type": "entity_fundamental_yoy",
            "secondary_news_used": False,
            "llm_used": False,
        },
        "forecast_contract": {
            "version": CONTRACT_VERSION,
            "horizon_days": FORECAST_HORIZON_DAYS,
            "belief_half_life_days": EVIDENCE_HALF_LIFE_DAYS,
            "one_live_forecast_per_belief": True,
            "outcome_event": "first_future_comparable_PR14_interpretation_inside_frozen_window",
            "support": True,
            "oppose": False,
            "neutral": "censored_not_binary",
            "no_comparable_outcome_by_target": "closed_without_calibration_observation",
            "next_day_stock_return_used": False,
            "pnl_tuned": False,
        },
        "anti_hindsight": {
            "historical_evidence_backfill": False,
            "historical_forecast_backfill": False,
            "first_pr15_run_activation_only": True,
            "existing_pr14_evidence_cursor_only_on_activation": True,
            "forecast_created_only_after_new_post_activation_pr14_directional_evidence": True,
            "future_dated_pr14_evidence_fails_closed": True,
            "forecast_outcome_must_be_after_forecast_at": True,
            "forecast_outcome_must_be_at_or_before_frozen_target_at": True,
            "neutral_interpretation_not_forced_to_binary": True,
            "dormant_entity_new_forecast_capture": False,
        },
        "state_boundary": {
            "belief_core_state_update_enabled": True,
            "prospective_entity_forecast_capture_enabled": True,
            "prospective_binary_verification_enabled": True,
            "calibration_memory_enabled": True,
            "brace_score_or_ranking_change": False,
            "brace_sizing_or_exposure_change": False,
            "entity_bridge_enabled": False,
            "with_without_bridge_enabled": False,
            "promotion_authority": False,
        },
        "capabilities": capabilities(),
        "safety_controls": safety_controls(),
        "promotion_evidence_standard": promotion_evidence_standard(),
        "sample": {
            "activation_only_this_run": first_run,
            "active_entities": active_entities,
            "dormant_entities": dormant_entities,
            "registered_beliefs": len(core.definitions),
            "belief_states": len(core.beliefs),
            "ingested_evidence_total": len(core.evidence),
            "new_definitions_this_run": new_definitions,
            "new_evidence_this_run": len(new_evidence),
            "forecasts_total": len(core.forecasts),
            "active_forecasts": len(active_forecasts),
            "new_forecasts_this_run": len(new_forecasts),
            "verifications_total": len(core.verifications),
            "new_verifications_this_run": new_verifications,
            "new_neutral_censors_this_run": new_censored,
            "new_no_outcome_closures_this_run": new_no_outcome,
            "source_issues_this_run": len(source_issues),
        },
        "forecast_closure_status_counts": closure_counts,
        "active_forecasts": active_forecasts,
        "belief_states": belief_states,
        "forecasts": forecasts,
        "verifications": verifications,
        "calibration": calibration,
        "effective_n_governance": {
            "raw_verified_n": len(core.verifications),
            "promotion_effective_n_threshold_defined_here": False,
            "reason": "Promotion effective N remains bridge-specific and must account for temporal dependence, overlapping windows, regime clustering and entity concentration.",
        },
        "source_issues": source_issues,
        "next_stage_not_enabled": {
            "brace_entity_bridge": True,
            "paired_with_without_economic_test": True,
            "belief_specific_promotion_gate": True,
            "bounded_entity_modifier": True,
        },
    }
    _write_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="PR15 Entity Belief State & Forecast Foundation")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--interpretation-state", required=True)
    args = parser.parse_args()
    report = run(
        Path(args.state_dir),
        interpretation_state_path=Path(args.interpretation_state),
    )
    print(json.dumps({
        "mode": report["mode"],
        "sample": report["sample"],
        "forecast_closure_status_counts": report["forecast_closure_status_counts"],
        "active_decision_influence": report["active_decision_influence"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
