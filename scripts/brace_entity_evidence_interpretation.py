#!/usr/bin/env python3
"""PR #14 — Entity Evidence Interpretation Foundation.

This layer converts selected PR13 primary-source observations into deterministic,
prospective Entity Belief evidence contracts.

Hard boundaries:
- first PR14 run is activation-only: existing PR13 observations seed cursors/baselines,
  but no historical Evidence is emitted;
- only frozen, deterministic, like-for-like comparison contracts can assign
  support / oppose / neutral;
- raw facts are never treated as bullish/bearish merely because they increased;
- unsupported/context-sensitive dimensions stay explicitly deferred;
- no LLM interpretation, no Entity forecast, no Belief Core state update,
  no BRACE score/ranking/sizing/veto/execution influence and no promotion.

Enabled v1 contracts are intentionally narrow:
- revenue_durability: same-fiscal-period YoY revenue change,
- earnings_momentum: same-fiscal-period YoY diluted EPS, falling back to net income,
- margin_trajectory: same-fiscal-period YoY operating-margin change,
- net_interest_income_durability: same-fiscal-period YoY NII for Financials.

All thresholds are frozen engineering materiality bands. They are not optimized
against PnL and do not authorize promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

try:
    from belief_adapter_contract import (
        EvidenceAssessment,
        Observation,
        observation_to_evidence,
        stable_id,
    )
    from belief_core import Evidence, iso_z, parse_time
except ModuleNotFoundError:  # package import in some test environments
    from scripts.belief_adapter_contract import (
        EvidenceAssessment,
        Observation,
        observation_to_evidence,
        stable_id,
    )
    from scripts.belief_core import Evidence, iso_z, parse_time

MODE = "research_shadow"
SCHEMA_VERSION = "brace-entity-evidence-interpretation-v1"
REPORT_VERSION = "brace-entity-evidence-interpretation-report-v1"
CONTRACT_VERSION = "entity-interpretation-contracts-v1"
STATE_FILENAME = "ENTITY_EVIDENCE_INTERPRETATION_STATE.json"
REPORT_FILENAME = "BRACE_ENTITY_EVIDENCE_INTERPRETATION_REPORT.json"
PRIMARY_STATE_FILENAME = "ENTITY_PRIMARY_SOURCE_EVIDENCE_STATE.json"

# Frozen materiality bands. These are semantic noise bands, not PnL-tuned values.
DIRECT_CONTRACTS: Mapping[str, Mapping[str, Any]] = {
    "revenue_durability": {
        "contract_id": "revenue_durability_yoy_v1",
        "metrics": ("revenue",),
        "comparison": "same_fiscal_period_yoy_relative_change",
        "materiality_band": 0.02,
        "sector": None,
    },
    "earnings_momentum": {
        "contract_id": "earnings_momentum_yoy_v1",
        "metrics": ("diluted_eps", "net_income"),
        "comparison": "same_fiscal_period_yoy_relative_change_eps_preferred",
        "materiality_band": 0.05,
        "sector": None,
    },
    "net_interest_income_durability": {
        "contract_id": "net_interest_income_durability_yoy_v1",
        "metrics": ("net_interest_income",),
        "comparison": "same_fiscal_period_yoy_relative_change",
        "materiality_band": 0.02,
        "sector": "Financials",
    },
}

MARGIN_CONTRACT: Mapping[str, Any] = {
    "dimension": "margin_trajectory",
    "contract_id": "operating_margin_yoy_v1",
    "metrics": ("revenue", "operating_income"),
    "comparison": "same_fiscal_period_yoy_operating_margin_change",
    "materiality_band": 0.005,  # 50 bp
    "sector": None,
}

DEFERRED_DIMENSIONS: Mapping[str, str] = {
    "earnings_quality": "requires reviewed cash-conversion/accrual context before polarity",
    "valuation": "requires point-in-time market valuation inputs outside PR13 filing facts",
    "balance_sheet_strength": "cash/assets/liabilities direction alone is not a sufficient strength contract",
    "competitive_position": "requires reviewed product/market-share primary evidence",
    "capital_allocation": "buybacks/dividends are not inherently positive or negative without valuation/opportunity-cost context",
    "capex_returns": "capex level is not return-on-capital evidence without future output/cashflow linkage",
    "regulatory_risk": "requires event-specific legal/regulatory interpretation contract",
    "credit_quality": "provision changes require denominator/portfolio-growth/charge-off context",
    "deposit_funding": "deposit growth alone does not establish funding quality or cost",
    "capital_strength": "requires explicit regulatory-capital metrics and reviewed thresholds",
    "pipeline_durability": "requires issuer pipeline/event evidence beyond current structured fact set",
    "product_concentration": "requires product-level revenue evidence",
    "cycle_position": "requires industry/capacity context beyond a single issuer fact",
    "capacity_utilization": "requires explicit utilization/capacity evidence",
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
        "historical_interpretation_backfill": False,
        "llm_interpretation": False,
        "belief_core_state_update": False,
        "entity_forecast_capture": False,
        "entity_promotion": False,
    }


def capabilities() -> Dict[str, bool]:
    return {
        "deterministic_entity_interpretation_enabled": True,
        "primary_observation_cursor_enabled": True,
        "prospective_like_for_like_comparison_enabled": True,
        "support_oppose_neutral_classification_enabled": True,
        "belief_compatible_evidence_materialization_enabled": True,
        "llm_interpretation_enabled": False,
        "belief_core_state_update_enabled": False,
        "entity_forecast_capture_enabled": False,
        "with_without_bridge_enabled": False,
        "promotion_gate_enabled": False,
    }


def promotion_evidence_standard() -> Dict[str, Any]:
    return {
        "with_without_required": True,
        "paired_prospective_counterfactual_required": True,
        "effective_n_required": True,
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
        raise RuntimeError("PR14 zero-influence invariant violated: " + ",".join(bad))


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


def _observation_from_dict(row: Mapping[str, Any]) -> Observation:
    return Observation(
        observation_id=str(row["observation_id"]),
        adapter=str(row["adapter"]),
        metric=str(row["metric"]),
        entity=str(row["entity"]),
        observed_at=str(row["observed_at"]),
        value=row.get("value"),
        unit=str(row.get("unit") or ""),
        source=str(row.get("source") or ""),
        source_type=str(row.get("source_type") or "primary"),
        source_ref=str(row.get("source_ref") or ""),
        reliability=float(row.get("reliability") or 0.0),
        independence_cluster=str(row.get("independence_cluster") or ""),
        status=str(row.get("status") or "ok"),
        tags=tuple(row.get("tags") or ()),
        metadata=dict(row.get("metadata") or {}),
    )


def _observation_to_dict(observation: Observation) -> Dict[str, Any]:
    payload = asdict(observation)
    payload["tags"] = list(observation.tags)
    payload["metadata"] = dict(observation.metadata)
    return payload


def _fact_metric(observation: Observation) -> Optional[str]:
    prefix = "entity_primary_fact."
    if not observation.metric.startswith(prefix):
        return None
    return observation.metric[len(prefix):]


def _is_eligible_primary_fact(observation: Observation) -> bool:
    return (
        observation.status == "ok"
        and observation.source_type == "primary"
        and observation.adapter == "entity_primary_source_sec"
        and _fact_metric(observation) is not None
        and str(observation.metadata.get("belief_polarity") or "").startswith("uninterpreted")
    )


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _date(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _period_days(observation: Observation) -> Optional[int]:
    start = _date(observation.metadata.get("period_start"))
    end = _date(observation.metadata.get("period_end"))
    if start is None or end is None or end < start:
        return None
    return (end - start).days + 1


def _duration_bucket(observation: Observation) -> str:
    days = _period_days(observation)
    if days is None:
        return "instant_or_unknown"
    if days <= 120:
        return "quarter_like"
    if days <= 230:
        return "half_year_like"
    if days <= 320:
        return "nine_month_like"
    return "annual_like"


def _fiscal_period(observation: Observation) -> str:
    return str(observation.metadata.get("fiscal_period") or "").strip().upper()


def _accession(observation: Observation) -> str:
    return str(observation.metadata.get("accession_number") or "").strip()


def _contract_registry_report() -> Dict[str, Any]:
    enabled = []
    for dimension, contract in DIRECT_CONTRACTS.items():
        enabled.append({"dimension": dimension, **dict(contract)})
    enabled.append(dict(MARGIN_CONTRACT))
    return {
        "version": CONTRACT_VERSION,
        "enabled": enabled,
        "deferred": [
            {"dimension": dimension, "status": "context_required", "reason": reason}
            for dimension, reason in sorted(DEFERRED_DIMENSIONS.items())
        ],
        "threshold_policy": "frozen_engineering_materiality_bands_not_pnl_tuned",
        "comparison_policy": "same_fiscal_period_yoy_only",
    }


def empty_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "contract_version": CONTRACT_VERSION,
        "first_run_at": None,
        "last_run_at": None,
        "seen_primary_observation_ids": [],
        "entities": {},
        "interpretations": [],
        "derived_observations": [],
        "evidence": [],
    }


def _entity_primary_status(primary_state: Mapping[str, Any], entity_id: str) -> Mapping[str, Any]:
    return dict(((primary_state.get("entities") or {}).get(entity_id) or {}))


def _primary_facts(primary_state: Mapping[str, Any], now: datetime) -> Tuple[List[Observation], List[Dict[str, Any]]]:
    rows: List[Observation] = []
    issues: List[Dict[str, Any]] = []
    for raw in primary_state.get("observations", []) or []:
        if not isinstance(raw, Mapping):
            continue
        try:
            observation = _observation_from_dict(raw)
        except Exception as exc:
            issues.append({
                "code": "invalid_primary_observation",
                "observation_id": str(raw.get("observation_id") or ""),
                "message": f"{type(exc).__name__}: {str(exc)[:240]}",
            })
            continue
        if not _is_eligible_primary_fact(observation):
            continue
        if parse_time(observation.observed_at) > now:
            issues.append({
                "code": "future_dated_primary_observation",
                "observation_id": observation.observation_id,
                "entity_id": observation.entity,
                "message": "Primary fact is after PR14 as_of and was not interpreted or cursor-seeded.",
            })
            continue
        rows.append(observation)
    rows.sort(key=lambda obs: (obs.observed_at, obs.entity, _accession(obs), obs.metric, obs.observation_id))
    return rows, issues


def _group_by_accession(observations: Sequence[Observation]) -> List[Tuple[Tuple[str, str], List[Observation]]]:
    groups: Dict[Tuple[str, str], List[Observation]] = {}
    for observation in observations:
        accession = _accession(observation)
        if not accession:
            continue
        groups.setdefault((observation.entity, accession), []).append(observation)
    return sorted(groups.items(), key=lambda item: (
        max(obs.observed_at for obs in item[1]), item[0][0], item[0][1]
    ))


def _candidate_score(observation: Observation, metric: str) -> Tuple[Any, ...]:
    fp = _fiscal_period(observation)
    days = _period_days(observation)
    frame = str(observation.metadata.get("frame") or "")
    if fp in {"Q1", "Q2", "Q3"}:
        duration_rank = days if days is not None else 99999
    elif fp in {"FY", "Q4"}:
        duration_rank = -(days or 0)
    else:
        duration_rank = days if days is not None else 99999
    return (
        0 if frame else 1,
        duration_rank,
        str(observation.metadata.get("taxonomy") or ""),
        str(observation.metadata.get("tag") or ""),
        observation.source_ref,
    )


def _select_fact(rows: Sequence[Observation], metric: str) -> Optional[Observation]:
    candidates = [
        row for row in rows
        if _fact_metric(row) == metric and _number(row.value) is not None and _fiscal_period(row)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda row: _candidate_score(row, metric))
    return candidates[0]


def _direct_baseline_key(contract_id: str, observation: Observation) -> str:
    return "|".join([
        contract_id,
        _fact_metric(observation) or "",
        _fiscal_period(observation),
        observation.unit,
        str(observation.metadata.get("taxonomy") or ""),
        str(observation.metadata.get("tag") or ""),
        _duration_bucket(observation),
    ])


def _snapshot(observation: Observation) -> Dict[str, Any]:
    return {
        "observation_id": observation.observation_id,
        "metric": _fact_metric(observation),
        "value": _number(observation.value),
        "unit": observation.unit,
        "source_ref": observation.source_ref,
        "observed_at": observation.observed_at,
        "independence_cluster": observation.independence_cluster,
        "reliability": float(observation.reliability),
        "accession_number": _accession(observation),
        "fiscal_period": _fiscal_period(observation),
        "fiscal_year": observation.metadata.get("fiscal_year"),
        "period_start": observation.metadata.get("period_start"),
        "period_end": observation.metadata.get("period_end"),
        "period_days": _period_days(observation),
        "taxonomy": observation.metadata.get("taxonomy"),
        "tag": observation.metadata.get("tag"),
        "frame": observation.metadata.get("frame"),
    }


def _snapshot_period_end(snapshot: Mapping[str, Any]) -> Optional[date]:
    return _date(snapshot.get("period_end"))


def _yoy_comparable(current: Observation, baseline: Mapping[str, Any]) -> Tuple[bool, str]:
    current_end = _date(current.metadata.get("period_end"))
    baseline_end = _snapshot_period_end(baseline)
    if current_end is None or baseline_end is None:
        return False, "period_end_missing"
    gap = (current_end - baseline_end).days
    if gap == 0:
        return False, "same_period_revision_or_amendment"
    if gap < 300 or gap > 430:
        return False, "not_one_year_comparable_period"
    if _fiscal_period(current) != str(baseline.get("fiscal_period") or "").upper():
        return False, "fiscal_period_mismatch"
    current_days = _period_days(current)
    baseline_days = baseline.get("period_days")
    if current_days is None or baseline_days is None:
        return False, "period_duration_missing"
    if abs(int(current_days) - int(baseline_days)) > 14:
        return False, "period_duration_mismatch"
    if current.unit != str(baseline.get("unit") or ""):
        return False, "unit_mismatch"
    return True, "same_fiscal_period_yoy"


def _strength(delta: float, band: float) -> float:
    multiple = abs(float(delta)) / max(float(band), 1e-12)
    if multiple <= 1.0:
        return 0.0
    if multiple < 2.0:
        return 0.20
    if multiple < 4.0:
        return 0.35
    if multiple < 8.0:
        return 0.50
    return 0.65


def _decision_id(
    *, contract_id: str, entity_id: str, accession: str, status: str,
    current_ids: Sequence[str], baseline_ids: Sequence[str], delta: Optional[float],
) -> str:
    return stable_id(
        "entity-int",
        CONTRACT_VERSION,
        contract_id,
        entity_id,
        accession,
        status,
        ",".join(sorted(current_ids)),
        ",".join(sorted(baseline_ids)),
        "" if delta is None else round(float(delta), 10),
    )


def _derived_and_evidence(
    *,
    entity_id: str,
    dimension: str,
    contract_id: str,
    current_ids: Sequence[str],
    baseline_ids: Sequence[str],
    current_value: float,
    baseline_value: float,
    delta: float,
    unit: str,
    current_cluster: str,
    reliability: float,
    materiality_band: float,
    computed_at: str,
    accession: str,
    comparison_basis: str,
) -> Tuple[Observation, Evidence]:
    direction = 1 if delta > 0 else -1
    strength = _strength(delta, materiality_band)
    derived = Observation.make(
        adapter="entity_evidence_interpretation",
        metric=f"entity_interpretation.{dimension}",
        entity=entity_id,
        observed_at=computed_at,
        value={
            "current": current_value,
            "baseline": baseline_value,
            "delta": delta,
            "comparison_basis": comparison_basis,
        },
        unit=unit,
        source="PR14 deterministic interpretation of SEC primary facts",
        source_type="derived",
        source_ref=f"pr14://{entity_id}/{accession}/{contract_id}",
        reliability=max(0.0, min(1.0, reliability)),
        independence_cluster=current_cluster,
        tags=("entity", "evidence_interpretation", dimension, CONTRACT_VERSION),
        metadata={
            "contract_id": contract_id,
            "contract_version": CONTRACT_VERSION,
            "current_primary_observation_ids": list(current_ids),
            "baseline_primary_observation_ids": list(baseline_ids),
            "materiality_band": materiality_band,
            "comparison_basis": comparison_basis,
            "pnl_tuned": False,
            "forecast_eligible": False,
            "belief_core_state_update": False,
        },
    )
    evidence = observation_to_evidence(
        derived,
        EvidenceAssessment(
            belief_id=f"entity.{entity_id}.{dimension}",
            direction=direction,
            strength=strength,
            evidence_type="entity_fundamental_yoy",
            note=(
                f"{dimension}: deterministic {comparison_basis}; "
                f"delta={delta:.6g}, frozen materiality band={materiality_band:.6g}."
            ),
            independence_cluster=current_cluster,
            metadata={
                "contract_id": contract_id,
                "contract_version": CONTRACT_VERSION,
                "current_primary_observation_ids": list(current_ids),
                "baseline_primary_observation_ids": list(baseline_ids),
                "pnl_tuned": False,
                "promotion_authority": False,
            },
        ),
    )
    return derived, evidence


def _append_unique_dicts(
    ledger: MutableMapping[str, Any], key: str, rows: Sequence[Mapping[str, Any]], id_field: str
) -> int:
    target = ledger.setdefault(key, [])
    by_id = {str(row.get(id_field)): row for row in target if isinstance(row, Mapping)}
    added = 0
    for raw in rows:
        row = dict(raw)
        row_id = str(row.get(id_field) or "")
        if not row_id:
            raise ValueError(f"missing {id_field} in {key}")
        existing = by_id.get(row_id)
        if existing is not None:
            if existing != row:
                raise ValueError(f"{key} identity collision with changed payload: {row_id}")
            continue
        target.append(row)
        by_id[row_id] = row
        added += 1
    target.sort(key=lambda row: (str(row.get("observed_at") or row.get("computed_at") or ""), str(row.get(id_field))))
    return added


def _direct_interpretation(
    *,
    entity_id: str,
    rows: Sequence[Observation],
    dimension: str,
    contract: Mapping[str, Any],
    baselines: MutableMapping[str, Any],
    computed_at: str,
) -> Tuple[Dict[str, Any], Optional[Observation], Optional[Evidence]]:
    metrics = tuple(contract["metrics"])
    selected: Optional[Observation] = None
    baseline: Optional[Mapping[str, Any]] = None
    baseline_key = ""

    # Earnings momentum prefers EPS, but falls back to net income only when a
    # comparable EPS pair is unavailable. Other direct contracts have one metric.
    for metric in metrics:
        candidate = _select_fact(rows, metric)
        if candidate is None:
            continue
        key = _direct_baseline_key(str(contract["contract_id"]), candidate)
        prior = baselines.get(key)
        if prior is not None:
            selected, baseline, baseline_key = candidate, prior, key
            break
        if selected is None:
            selected, baseline, baseline_key = candidate, None, key

    accession = _accession(rows[0]) if rows else ""
    if selected is None:
        decision = {
            "interpretation_id": _decision_id(
                contract_id=str(contract["contract_id"]), entity_id=entity_id, accession=accession,
                status="source_metric_unavailable", current_ids=(), baseline_ids=(), delta=None,
            ),
            "computed_at": computed_at,
            "entity_id": entity_id,
            "belief_id": f"entity.{entity_id}.{dimension}",
            "dimension": dimension,
            "contract_id": contract["contract_id"],
            "status": "source_metric_unavailable",
            "direction": None,
            "strength": 0.0,
            "accession_number": accession,
            "note": "No eligible structured primary fact for this contract in the accession.",
        }
        return decision, None, None

    current_snapshot = _snapshot(selected)
    current_ids = [selected.observation_id]
    if baseline is None:
        baselines[baseline_key] = current_snapshot
        decision = {
            "interpretation_id": _decision_id(
                contract_id=str(contract["contract_id"]), entity_id=entity_id, accession=accession,
                status="baseline_only", current_ids=current_ids, baseline_ids=(), delta=None,
            ),
            "computed_at": computed_at,
            "entity_id": entity_id,
            "belief_id": f"entity.{entity_id}.{dimension}",
            "dimension": dimension,
            "contract_id": contract["contract_id"],
            "status": "baseline_only",
            "direction": None,
            "strength": 0.0,
            "accession_number": accession,
            "current_primary_observation_ids": current_ids,
            "note": "First comparable prospective primary fact establishes the frozen-contract baseline only.",
        }
        return decision, None, None

    comparable, basis = _yoy_comparable(selected, baseline)
    baseline_ids = [str(baseline.get("observation_id"))]
    if not comparable:
        # Amendments of the same economic period refresh the baseline without
        # pretending a restatement is operating momentum.
        if basis == "same_period_revision_or_amendment":
            baselines[baseline_key] = current_snapshot
            status = "amendment_baseline_refresh"
        else:
            status = "context_required"
        decision = {
            "interpretation_id": _decision_id(
                contract_id=str(contract["contract_id"]), entity_id=entity_id, accession=accession,
                status=status, current_ids=current_ids, baseline_ids=baseline_ids, delta=None,
            ),
            "computed_at": computed_at,
            "entity_id": entity_id,
            "belief_id": f"entity.{entity_id}.{dimension}",
            "dimension": dimension,
            "contract_id": contract["contract_id"],
            "status": status,
            "direction": None,
            "strength": 0.0,
            "accession_number": accession,
            "current_primary_observation_ids": current_ids,
            "baseline_primary_observation_ids": baseline_ids,
            "note": basis,
        }
        return decision, None, None

    current_value = _number(selected.value)
    baseline_value = _number(baseline.get("value"))
    if current_value is None or baseline_value is None or baseline_value <= 0:
        baselines[baseline_key] = current_snapshot
        decision = {
            "interpretation_id": _decision_id(
                contract_id=str(contract["contract_id"]), entity_id=entity_id, accession=accession,
                status="context_required", current_ids=current_ids, baseline_ids=baseline_ids, delta=None,
            ),
            "computed_at": computed_at,
            "entity_id": entity_id,
            "belief_id": f"entity.{entity_id}.{dimension}",
            "dimension": dimension,
            "contract_id": contract["contract_id"],
            "status": "context_required",
            "direction": None,
            "strength": 0.0,
            "accession_number": accession,
            "current_primary_observation_ids": current_ids,
            "baseline_primary_observation_ids": baseline_ids,
            "note": "Relative-change contract requires a positive comparable baseline; sign-crossing/nonpositive cases are deferred.",
        }
        return decision, None, None

    delta = current_value / baseline_value - 1.0
    band = float(contract["materiality_band"])
    strength = _strength(delta, band)
    if strength == 0.0:
        status, direction = "neutral", None
        derived = evidence = None
    else:
        status, direction = ("support", 1) if delta > 0 else ("oppose", -1)
        derived, evidence = _derived_and_evidence(
            entity_id=entity_id,
            dimension=dimension,
            contract_id=str(contract["contract_id"]),
            current_ids=current_ids,
            baseline_ids=baseline_ids,
            current_value=current_value,
            baseline_value=baseline_value,
            delta=delta,
            unit="relative_change",
            current_cluster=selected.independence_cluster,
            reliability=min(float(selected.reliability), float(baseline.get("reliability") or 0.0)),
            materiality_band=band,
            computed_at=computed_at,
            accession=accession,
            comparison_basis=basis,
        )

    baselines[baseline_key] = current_snapshot
    decision = {
        "interpretation_id": _decision_id(
            contract_id=str(contract["contract_id"]), entity_id=entity_id, accession=accession,
            status=status, current_ids=current_ids, baseline_ids=baseline_ids, delta=delta,
        ),
        "computed_at": computed_at,
        "entity_id": entity_id,
        "belief_id": f"entity.{entity_id}.{dimension}",
        "dimension": dimension,
        "contract_id": contract["contract_id"],
        "status": status,
        "direction": direction,
        "strength": strength,
        "accession_number": accession,
        "current_primary_observation_ids": current_ids,
        "baseline_primary_observation_ids": baseline_ids,
        "current_value": current_value,
        "baseline_value": baseline_value,
        "delta": delta,
        "materiality_band": band,
        "comparison_basis": basis,
        "pnl_tuned": False,
        "evidence_id": evidence.evidence_id if evidence else None,
        "derived_observation_id": derived.observation_id if derived else None,
    }
    return decision, derived, evidence


def _matching_margin_pair(rows: Sequence[Observation]) -> Optional[Tuple[Observation, Observation]]:
    revenues = [row for row in rows if _fact_metric(row) == "revenue" and _number(row.value) is not None]
    operating = [row for row in rows if _fact_metric(row) == "operating_income" and _number(row.value) is not None]
    pairs: List[Tuple[Observation, Observation]] = []
    for revenue in revenues:
        for op_income in operating:
            if not _fiscal_period(revenue) or _fiscal_period(revenue) != _fiscal_period(op_income):
                continue
            if revenue.unit != op_income.unit:
                continue
            if revenue.metadata.get("period_start") != op_income.metadata.get("period_start"):
                continue
            if revenue.metadata.get("period_end") != op_income.metadata.get("period_end"):
                continue
            pairs.append((revenue, op_income))
    if not pairs:
        return None
    fp = _fiscal_period(pairs[0][0])
    if fp in {"Q1", "Q2", "Q3"}:
        pairs.sort(key=lambda pair: (_period_days(pair[0]) or 99999, pair[0].source_ref, pair[1].source_ref))
    elif fp in {"FY", "Q4"}:
        pairs.sort(key=lambda pair: (-(_period_days(pair[0]) or 0), pair[0].source_ref, pair[1].source_ref))
    else:
        pairs.sort(key=lambda pair: (_period_days(pair[0]) or 99999, pair[0].source_ref, pair[1].source_ref))
    return pairs[0]


def _margin_key(revenue: Observation, operating_income: Observation) -> str:
    return "|".join([
        str(MARGIN_CONTRACT["contract_id"]),
        _fiscal_period(revenue),
        revenue.unit,
        str(revenue.metadata.get("taxonomy") or ""),
        str(revenue.metadata.get("tag") or ""),
        str(operating_income.metadata.get("taxonomy") or ""),
        str(operating_income.metadata.get("tag") or ""),
        _duration_bucket(revenue),
    ])


def _margin_snapshot(revenue: Observation, operating_income: Observation) -> Optional[Dict[str, Any]]:
    revenue_value = _number(revenue.value)
    operating_value = _number(operating_income.value)
    if revenue_value is None or operating_value is None or revenue_value <= 0:
        return None
    return {
        "value": operating_value / revenue_value,
        "unit": "operating_margin_ratio",
        "source_observation_ids": [revenue.observation_id, operating_income.observation_id],
        "source_refs": [revenue.source_ref, operating_income.source_ref],
        "observed_at": max(revenue.observed_at, operating_income.observed_at),
        "independence_cluster": revenue.independence_cluster,
        "reliability": min(float(revenue.reliability), float(operating_income.reliability)),
        "accession_number": _accession(revenue),
        "fiscal_period": _fiscal_period(revenue),
        "fiscal_year": revenue.metadata.get("fiscal_year"),
        "period_start": revenue.metadata.get("period_start"),
        "period_end": revenue.metadata.get("period_end"),
        "period_days": _period_days(revenue),
    }


def _margin_comparable(current: Mapping[str, Any], baseline: Mapping[str, Any]) -> Tuple[bool, str]:
    current_end = _date(current.get("period_end"))
    baseline_end = _date(baseline.get("period_end"))
    if current_end is None or baseline_end is None:
        return False, "period_end_missing"
    gap = (current_end - baseline_end).days
    if gap == 0:
        return False, "same_period_revision_or_amendment"
    if gap < 300 or gap > 430:
        return False, "not_one_year_comparable_period"
    if str(current.get("fiscal_period") or "").upper() != str(baseline.get("fiscal_period") or "").upper():
        return False, "fiscal_period_mismatch"
    current_days = current.get("period_days")
    baseline_days = baseline.get("period_days")
    if current_days is None or baseline_days is None or abs(int(current_days) - int(baseline_days)) > 14:
        return False, "period_duration_mismatch"
    return True, "same_fiscal_period_yoy_operating_margin"


def _margin_interpretation(
    *, entity_id: str, rows: Sequence[Observation], baselines: MutableMapping[str, Any], computed_at: str
) -> Tuple[Dict[str, Any], Optional[Observation], Optional[Evidence]]:
    pair = _matching_margin_pair(rows)
    accession = _accession(rows[0]) if rows else ""
    contract_id = str(MARGIN_CONTRACT["contract_id"])
    dimension = str(MARGIN_CONTRACT["dimension"])
    if pair is None:
        decision = {
            "interpretation_id": _decision_id(
                contract_id=contract_id, entity_id=entity_id, accession=accession,
                status="source_metric_unavailable", current_ids=(), baseline_ids=(), delta=None,
            ),
            "computed_at": computed_at, "entity_id": entity_id,
            "belief_id": f"entity.{entity_id}.{dimension}", "dimension": dimension,
            "contract_id": contract_id, "status": "source_metric_unavailable",
            "direction": None, "strength": 0.0, "accession_number": accession,
            "note": "A same-period revenue + operating-income pair is required.",
        }
        return decision, None, None

    revenue, op_income = pair
    current = _margin_snapshot(revenue, op_income)
    current_ids = [revenue.observation_id, op_income.observation_id]
    if current is None:
        decision = {
            "interpretation_id": _decision_id(
                contract_id=contract_id, entity_id=entity_id, accession=accession,
                status="context_required", current_ids=current_ids, baseline_ids=(), delta=None,
            ),
            "computed_at": computed_at, "entity_id": entity_id,
            "belief_id": f"entity.{entity_id}.{dimension}", "dimension": dimension,
            "contract_id": contract_id, "status": "context_required",
            "direction": None, "strength": 0.0, "accession_number": accession,
            "current_primary_observation_ids": current_ids,
            "note": "Operating-margin contract requires positive reported revenue.",
        }
        return decision, None, None

    key = _margin_key(revenue, op_income)
    baseline = baselines.get(key)
    if baseline is None:
        baselines[key] = current
        decision = {
            "interpretation_id": _decision_id(
                contract_id=contract_id, entity_id=entity_id, accession=accession,
                status="baseline_only", current_ids=current_ids, baseline_ids=(), delta=None,
            ),
            "computed_at": computed_at, "entity_id": entity_id,
            "belief_id": f"entity.{entity_id}.{dimension}", "dimension": dimension,
            "contract_id": contract_id, "status": "baseline_only",
            "direction": None, "strength": 0.0, "accession_number": accession,
            "current_primary_observation_ids": current_ids,
            "note": "First prospective operating-margin observation establishes baseline only.",
        }
        return decision, None, None

    comparable, basis = _margin_comparable(current, baseline)
    baseline_ids = list(baseline.get("source_observation_ids") or [])
    if not comparable:
        if basis == "same_period_revision_or_amendment":
            baselines[key] = current
            status = "amendment_baseline_refresh"
        else:
            status = "context_required"
        decision = {
            "interpretation_id": _decision_id(
                contract_id=contract_id, entity_id=entity_id, accession=accession,
                status=status, current_ids=current_ids, baseline_ids=baseline_ids, delta=None,
            ),
            "computed_at": computed_at, "entity_id": entity_id,
            "belief_id": f"entity.{entity_id}.{dimension}", "dimension": dimension,
            "contract_id": contract_id, "status": status,
            "direction": None, "strength": 0.0, "accession_number": accession,
            "current_primary_observation_ids": current_ids,
            "baseline_primary_observation_ids": baseline_ids,
            "note": basis,
        }
        return decision, None, None

    current_margin = float(current["value"])
    baseline_margin = float(baseline["value"])
    delta = current_margin - baseline_margin
    band = float(MARGIN_CONTRACT["materiality_band"])
    strength = _strength(delta, band)
    if strength == 0.0:
        status, direction = "neutral", None
        derived = evidence = None
    else:
        status, direction = ("support", 1) if delta > 0 else ("oppose", -1)
        derived, evidence = _derived_and_evidence(
            entity_id=entity_id,
            dimension=dimension,
            contract_id=contract_id,
            current_ids=current_ids,
            baseline_ids=baseline_ids,
            current_value=current_margin,
            baseline_value=baseline_margin,
            delta=delta,
            unit="operating_margin_change",
            current_cluster=revenue.independence_cluster,
            reliability=min(float(current["reliability"]), float(baseline.get("reliability") or 0.0)),
            materiality_band=band,
            computed_at=computed_at,
            accession=accession,
            comparison_basis=basis,
        )
    baselines[key] = current
    decision = {
        "interpretation_id": _decision_id(
            contract_id=contract_id, entity_id=entity_id, accession=accession,
            status=status, current_ids=current_ids, baseline_ids=baseline_ids, delta=delta,
        ),
        "computed_at": computed_at, "entity_id": entity_id,
        "belief_id": f"entity.{entity_id}.{dimension}", "dimension": dimension,
        "contract_id": contract_id, "status": status, "direction": direction,
        "strength": strength, "accession_number": accession,
        "current_primary_observation_ids": current_ids,
        "baseline_primary_observation_ids": baseline_ids,
        "current_value": current_margin, "baseline_value": baseline_margin,
        "delta": delta, "materiality_band": band, "comparison_basis": basis,
        "pnl_tuned": False,
        "evidence_id": evidence.evidence_id if evidence else None,
        "derived_observation_id": derived.observation_id if derived else None,
    }
    return decision, derived, evidence


def _seed_group_baselines(
    entity_id: str, rows: Sequence[Observation], sector: str, baselines: MutableMapping[str, Any]
) -> None:
    for dimension, contract in DIRECT_CONTRACTS.items():
        if contract.get("sector") and str(contract.get("sector")) != sector:
            continue
        for metric in contract["metrics"]:
            observation = _select_fact(rows, str(metric))
            if observation is not None:
                baselines[_direct_baseline_key(str(contract["contract_id"]), observation)] = _snapshot(observation)
    pair = _matching_margin_pair(rows)
    if pair is not None:
        margin = _margin_snapshot(*pair)
        if margin is not None:
            baselines[_margin_key(*pair)] = margin


def _reset_entity_for_source_window(
    entity_state: MutableMapping[str, Any], source_window_opened_at: Optional[str], computed_at: str
) -> None:
    entity_state["baselines"] = {}
    entity_state["source_window_opened_at"] = source_window_opened_at
    entity_state["source_window_reset_at"] = computed_at
    entity_state["source_window_reset_count"] = int(entity_state.get("source_window_reset_count") or 0) + 1


def run(
    state_dir: Path,
    *,
    primary_state_path: Path,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    _assert_safety()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    computed_at = iso_z(now)
    state_dir = Path(state_dir)
    state_path = state_dir / STATE_FILENAME
    report_path = state_dir / REPORT_FILENAME
    primary_state = _read_json(primary_state_path, {})
    if not primary_state:
        raise ValueError("PR14 requires a non-empty PR13 primary-source state")

    state = _read_json(state_path, empty_state())
    state["schema_version"] = SCHEMA_VERSION
    state["mode"] = MODE
    if state.get("contract_version") not in {None, CONTRACT_VERSION}:
        raise ValueError("PR14 contract version changed; explicit migration/review is required")
    state["contract_version"] = CONTRACT_VERSION
    state.setdefault("entities", {})
    state.setdefault("interpretations", [])
    state.setdefault("derived_observations", [])
    state.setdefault("evidence", [])
    state.setdefault("seen_primary_observation_ids", [])

    facts, source_issues = _primary_facts(primary_state, now)
    first_run = not bool(state.get("first_run_at"))
    if first_run:
        state["first_run_at"] = computed_at

    seen = set(str(x) for x in state.get("seen_primary_observation_ids") or [])
    primary_entities = primary_state.get("entities") or {}
    entity_states: MutableMapping[str, Any] = state["entities"]

    # Synchronize PR13 active/dormant state and detect collection-window resets.
    for entity_id, primary_entity_raw in sorted(primary_entities.items()):
        primary_entity = dict(primary_entity_raw or {})
        entity_state = deepcopy(dict(entity_states.get(entity_id) or {}))
        entity_state.setdefault("entity_id", entity_id)
        entity_state.setdefault("baselines", {})
        entity_state["sector"] = primary_entity.get("sector")
        entity_state["current_status"] = primary_entity.get("current_status")
        entity_state["reporting_regime"] = primary_entity.get("reporting_regime")
        source_window = primary_entity.get("current_window_opened_at")
        prior_window = entity_state.get("source_window_opened_at")
        if prior_window != source_window:
            _reset_entity_for_source_window(entity_state, source_window, computed_at)
        entity_state["last_synced_at"] = computed_at
        entity_states[entity_id] = entity_state

    new_decisions: List[Dict[str, Any]] = []
    new_derived: List[Observation] = []
    new_evidence: List[Evidence] = []

    if first_run:
        # Activation-only: seed baselines/cursor from already-prospectively-collected
        # PR13 facts, but emit no historical interpretation/evidence.
        for (entity_id, _accession_number), rows in _group_by_accession(facts):
            entity_state = entity_states.setdefault(entity_id, {"entity_id": entity_id, "baselines": {}})
            if entity_state.get("current_status") != "active":
                continue
            source_window = entity_state.get("source_window_opened_at")
            if source_window and max(parse_time(row.observed_at) for row in rows) <= parse_time(str(source_window)):
                continue
            _seed_group_baselines(
                entity_id,
                rows,
                str(entity_state.get("sector") or ""),
                entity_state.setdefault("baselines", {}),
            )
        seen.update(obs.observation_id for obs in facts)
    else:
        unseen = [obs for obs in facts if obs.observation_id not in seen]
        for (entity_id, accession), rows in _group_by_accession(unseen):
            entity_state = entity_states.setdefault(entity_id, {"entity_id": entity_id, "baselines": {}})
            if entity_state.get("current_status") != "active":
                # Do not interpret dormant entities. Cursor them so reactivation
                # cannot accidentally replay a dormant-period source event.
                seen.update(row.observation_id for row in rows)
                continue
            source_window = entity_state.get("source_window_opened_at")
            if source_window and max(parse_time(row.observed_at) for row in rows) <= parse_time(str(source_window)):
                seen.update(row.observation_id for row in rows)
                continue

            baselines = entity_state.setdefault("baselines", {})
            sector = str(entity_state.get("sector") or "")
            for dimension, contract in DIRECT_CONTRACTS.items():
                if contract.get("sector") and str(contract.get("sector")) != sector:
                    continue
                decision, derived, evidence = _direct_interpretation(
                    entity_id=entity_id,
                    rows=rows,
                    dimension=dimension,
                    contract=contract,
                    baselines=baselines,
                    computed_at=computed_at,
                )
                new_decisions.append(decision)
                if derived is not None:
                    new_derived.append(derived)
                if evidence is not None:
                    new_evidence.append(evidence)

            decision, derived, evidence = _margin_interpretation(
                entity_id=entity_id,
                rows=rows,
                baselines=baselines,
                computed_at=computed_at,
            )
            new_decisions.append(decision)
            if derived is not None:
                new_derived.append(derived)
            if evidence is not None:
                new_evidence.append(evidence)

            entity_state["last_interpreted_accession"] = accession
            entity_state["last_interpreted_at"] = computed_at
            seen.update(row.observation_id for row in rows)

    added_decisions = _append_unique_dicts(state, "interpretations", new_decisions, "interpretation_id")
    added_derived = _append_unique_dicts(
        state, "derived_observations", [_observation_to_dict(row) for row in new_derived], "observation_id"
    )
    added_evidence = _append_unique_dicts(
        state, "evidence", [row.to_dict() for row in new_evidence], "evidence_id"
    )

    state["seen_primary_observation_ids"] = sorted(seen)
    state["last_run_at"] = computed_at
    state["primary_state_last_updated_at"] = primary_state.get("last_updated_at")
    state["primary_state_fingerprint"] = _canonical_sha256({
        "last_updated_at": primary_state.get("last_updated_at"),
        "observation_ids": sorted(str(row.get("observation_id")) for row in primary_state.get("observations", []) if isinstance(row, Mapping)),
        "entities": {
            key: {
                "current_status": value.get("current_status"),
                "current_window_opened_at": value.get("current_window_opened_at"),
            }
            for key, value in sorted(primary_entities.items()) if isinstance(value, Mapping)
        },
    })
    _write_json(state_path, state)

    interpretations = list(state.get("interpretations") or [])
    evidence_rows = list(state.get("evidence") or [])
    status_counts: Dict[str, int] = {}
    for row in interpretations:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    evidence_by_dimension: Dict[str, int] = {}
    for row in evidence_rows:
        belief_id = str(row.get("belief_id") or "")
        dimension = belief_id.rsplit(".", 1)[-1] if "." in belief_id else belief_id
        evidence_by_dimension[dimension] = evidence_by_dimension.get(dimension, 0) + 1

    active_entities = sum(
        1 for row in entity_states.values() if isinstance(row, Mapping) and row.get("current_status") == "active"
    )
    dormant_entities = sum(
        1 for row in entity_states.values() if isinstance(row, Mapping) and row.get("current_status") == "dormant"
    )

    report = {
        "report_version": REPORT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "generated_at": computed_at,
        "mode": MODE,
        "active_decision_influence": False,
        "purpose": "Deterministic prospective interpretation of selected PR13 primary facts into Belief-compatible support/oppose evidence without forecasts or BRACE influence.",
        "source_contract": {
            "input": "PR13 ENTITY_PRIMARY_SOURCE_EVIDENCE_STATE.json",
            "primary_source_required": True,
            "sec_primary_facts_only_in_v1": True,
            "secondary_news_used": False,
            "llm_used": False,
        },
        "anti_hindsight": {
            "historical_interpretation_backfill": False,
            "first_pr14_run_activation_only": True,
            "existing_primary_facts_seed_cursor_and_baseline_only": True,
            "derived_evidence_observed_at_is_interpretation_runtime": True,
            "pr13_collection_window_change_resets_comparison_baselines": True,
            "dormant_period_replay": False,
            "future_dated_primary_observations_fail_closed": True,
        },
        "interpretation_boundary": {
            "raw_primary_facts_directly_polarized": False,
            "support_oppose_neutral_enabled_only_for_frozen_contracts": True,
            "same_fiscal_period_yoy_required": True,
            "pnl_tuned_thresholds": False,
            "context_sensitive_dimensions_deferred": True,
            "evidence_materialized_but_not_applied_to_belief_core": True,
            "entity_forecasts_created": False,
        },
        "contracts": _contract_registry_report(),
        "capabilities": capabilities(),
        "safety_controls": safety_controls(),
        "promotion_evidence_standard": promotion_evidence_standard(),
        "sample": {
            "activation_only_this_run": first_run,
            "active_entities": active_entities,
            "dormant_entities": dormant_entities,
            "eligible_primary_fact_observations_seen": len(facts),
            "primary_observation_cursor_size": len(seen),
            "interpretations_total": len(interpretations),
            "evidence_total": len(evidence_rows),
            "new_interpretations_this_run": added_decisions,
            "new_derived_observations_this_run": added_derived,
            "new_evidence_this_run": added_evidence,
            "source_issues_this_run": len(source_issues),
        },
        "interpretation_status_counts": status_counts,
        "evidence_by_dimension": evidence_by_dimension,
        "source_issues": source_issues,
        "next_stage_not_enabled": {
            "entity_belief_state_update": True,
            "entity_forecast_capture": True,
            "entity_calibration": True,
            "brace_entity_bridge": True,
            "with_without_economic_test": True,
            "promotion_gate": True,
        },
    }
    _write_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="PR14 Entity Evidence Interpretation Foundation")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--primary-state", required=True)
    args = parser.parse_args()
    report = run(
        Path(args.state_dir),
        primary_state_path=Path(args.primary_state),
    )
    print(json.dumps({
        "mode": report["mode"],
        "sample": report["sample"],
        "interpretation_status_counts": report["interpretation_status_counts"],
        "active_decision_influence": report["active_decision_influence"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
