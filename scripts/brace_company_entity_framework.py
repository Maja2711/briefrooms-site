#!/usr/bin/env python3
"""PR #12 — BRACE Company / Entity Belief Framework Foundation.

This module formalizes entity activation before any company-specific Belief is
allowed to influence BRACE:

- active Stock in the current Portfolio 10K -> always-on entity research,
- Stock in canonical BRACE analysis.candidates -> activate at candidate/watchlist stage,
- all other universe stocks -> inactive,
- a previously activated candidate that disappears becomes dormant; history is preserved.

PR #12 is framework-only. It materializes entity belief definitions and an
append-only activation lineage, but it deliberately does not ingest company
evidence, freeze entity forecasts, modify BRACE, or authorize promotion.

PR #19.1 hardens the Entity taxonomy: sector is not treated as business model.
Bank-specific dimensions require an explicit semantic archetype contract and
Financials without a reviewed bank archetype fail closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence, Tuple

try:
    from entity_semantic_eligibility import (
        BANK_SPECIFIC_DIMENSIONS,
        CONTRACT_VERSION as SEMANTIC_ELIGIBILITY_CONTRACT_VERSION,
        annotate_entity,
        dimension_eligibility,
        semantic_profile,
    )
except ModuleNotFoundError:
    from scripts.entity_semantic_eligibility import (
        BANK_SPECIFIC_DIMENSIONS,
        CONTRACT_VERSION as SEMANTIC_ELIGIBILITY_CONTRACT_VERSION,
        annotate_entity,
        dimension_eligibility,
        semantic_profile,
    )

MODE = "research_shadow"
SCHEMA_VERSION = "brace-company-entity-framework-v1"
REPORT_VERSION = "brace-company-entity-framework-report-v1"
DIMENSION_REGISTRY_VERSION = "entity-dimensions-v2"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORTFOLIO = ROOT / "data" / "investments" / "portfolio_10k_usd.json"
DEFAULT_ANALYSIS = ROOT / "data" / "portfolio10k" / "analysis.json"
DEFAULT_UNIVERSE = ROOT / "data" / "portfolio10k" / "universe.json"
STATE_FILENAME = "ENTITY_ACTIVATION_STATE.json"
REPORT_FILENAME = "BRACE_COMPANY_ENTITY_FRAMEWORK_REPORT.json"

COMMON_DIMENSIONS: Tuple[Mapping[str, Any], ...] = (
    {"dimension": "earnings_momentum", "horizon": "next_report_to_multi_quarter", "outcome_family": "reported_earnings_trajectory"},
    {"dimension": "revenue_durability", "horizon": "multi_quarter", "outcome_family": "reported_revenue_persistence"},
    {"dimension": "margin_trajectory", "horizon": "multi_quarter", "outcome_family": "reported_margin_trajectory"},
    {"dimension": "earnings_quality", "horizon": "multi_quarter", "outcome_family": "cash_conversion_and_accrual_quality"},
    {"dimension": "valuation", "horizon": "multi_quarter", "outcome_family": "valuation_contract_requires_separate_review"},
    {"dimension": "balance_sheet_strength", "horizon": "multi_quarter", "outcome_family": "reported_balance_sheet_resilience"},
    {"dimension": "competitive_position", "horizon": "multi_quarter", "outcome_family": "competitive_position_contract_requires_primary_evidence"},
    {"dimension": "capital_allocation", "horizon": "multi_quarter", "outcome_family": "capital_allocation_follow_through"},
    {"dimension": "capex_returns", "horizon": "multi_quarter", "outcome_family": "future_cashflow_or_return_on_investment"},
    {"dimension": "regulatory_risk", "horizon": "event_or_multi_quarter", "outcome_family": "event_resolution_or_regulatory_status"},
)

SECTOR_MODULES: Mapping[str, Tuple[Mapping[str, Any], ...]] = {
    "Financials": (
        {"dimension": "net_interest_income_durability", "horizon": "multi_quarter", "outcome_family": "reported_nii_trajectory", "eligibility_scope": "entity_archetype:bank"},
        {"dimension": "credit_quality", "horizon": "multi_quarter", "outcome_family": "reported_credit_loss_and_asset_quality", "eligibility_scope": "entity_archetype:bank"},
        {"dimension": "deposit_funding", "horizon": "multi_quarter", "outcome_family": "reported_deposit_and_funding_quality", "eligibility_scope": "entity_archetype:bank"},
        {"dimension": "capital_strength", "horizon": "multi_quarter", "outcome_family": "reported_regulatory_capital_strength", "eligibility_scope": "entity_archetype:bank"},
    ),
    "Health Care": (
        {"dimension": "pipeline_durability", "horizon": "event_or_multi_quarter", "outcome_family": "pipeline_and_approval_follow_through"},
        {"dimension": "product_concentration", "horizon": "multi_quarter", "outcome_family": "reported_revenue_concentration"},
    ),
    "Information Technology": (
        {"dimension": "cycle_position", "horizon": "multi_quarter", "outcome_family": "reported_demand_cycle_follow_through"},
        {"dimension": "capacity_utilization", "horizon": "multi_quarter", "outcome_family": "reported_capacity_or_utilization_follow_through"},
    ),
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
        "historical_backfill": False,
        "entity_evidence_ingestion": False,
        "entity_forecast_capture": False,
        "entity_promotion": False,
    }


def capabilities() -> Dict[str, bool]:
    return {
        "entity_activation_registry_enabled": True,
        "entity_definition_materialization_enabled": True,
        "entity_semantic_eligibility_enabled": True,
        "bank_specific_dimensions_require_bank_archetype": True,
        "current_portfolio_always_on_enabled": True,
        "candidate_watchlist_pre_entry_activation_enabled": True,
        "dormant_history_preservation_enabled": True,
        "entity_evidence_ingestion_enabled": False,
        "entity_forecast_capture_enabled": False,
        "with_without_bridge_enabled": False,
        "promotion_gate_enabled": False,
    }


def _assert_safety() -> None:
    bad = [key for key, value in safety_controls().items() if value is not False]
    if bad:
        raise RuntimeError("PR12 zero-influence invariant violated: " + ",".join(bad))


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _canonical_sha256(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_stock(row: Mapping[str, Any]) -> bool:
    value = str(row.get("asset_type") or "").strip().lower()
    return value in {"stock", "equity", "common_stock", "common stock"}


def _entity_id(row: Mapping[str, Any]) -> str:
    return str(row.get("instrument_id") or row.get("id") or "").strip().lower()


def _market_symbol(row: Mapping[str, Any]) -> str:
    return str(row.get("data_symbol") or row.get("market_symbol") or row.get("broker_symbol") or "").strip()


def _universe_by_id(universe: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        _entity_id(row): dict(row)
        for row in (universe.get("instruments") or [])
        if _entity_id(row)
    }


def current_portfolio_entities(portfolio: Mapping[str, Any], universe: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    by_id = _universe_by_id(universe)
    out: Dict[str, Dict[str, Any]] = {}
    for raw in portfolio.get("positions", []) or []:
        row = dict(raw)
        entity_id = _entity_id(row)
        merged = {**by_id.get(entity_id, {}), **row}
        if not entity_id or not _is_stock(merged):
            continue
        if str(merged.get("status") or "active").lower() != "active":
            continue
        out[entity_id] = {
            **merged,
            "entity_id": entity_id,
            "activation_source": "current_portfolio",
            "activation_class": "always_on",
            "candidate_rank": None,
        }
    return out


def candidate_watchlist_entities(analysis: Mapping[str, Any], universe: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Use BRACE's canonical candidate list as the activation boundary.

    `analysis.candidates` is already produced by brace_portfolio_candidates.rank_candidates
    after availability, confidence, observation-count and data-freshness filtering.
    PR12 intentionally adds no second hidden score threshold.
    """
    by_id = _universe_by_id(universe)
    out: Dict[str, Dict[str, Any]] = {}
    for raw in analysis.get("candidates", []) or []:
        row = dict(raw)
        entity_id = _entity_id(row)
        merged = {**by_id.get(entity_id, {}), **row}
        if not entity_id or not _is_stock(merged):
            continue
        if str(merged.get("availability") or "AVAILABLE").upper() != "AVAILABLE":
            continue
        if merged.get("active", True) is False:
            continue
        out[entity_id] = {
            **merged,
            "entity_id": entity_id,
            "activation_source": "brace_candidate_watchlist",
            "activation_class": "pre_entry_research",
            "candidate_rank": merged.get("rank"),
        }
    return out


def desired_entities(
    portfolio: Mapping[str, Any],
    analysis: Mapping[str, Any],
    universe: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    candidates = candidate_watchlist_entities(analysis, universe)
    current = current_portfolio_entities(portfolio, universe)
    merged = dict(candidates)
    for entity_id, row in current.items():
        previous = merged.get(entity_id)
        if previous:
            row = {
                **previous,
                **row,
                "activation_source": "current_portfolio_and_candidate",
                "activation_class": "always_on",
                "candidate_rank": previous.get("candidate_rank"),
            }
        merged[entity_id] = row
    return {entity_id: annotate_entity(row) for entity_id, row in merged.items()}


def dimension_registry_for(entity: Mapping[str, Any]) -> Tuple[Mapping[str, Any], ...]:
    sector = str(entity.get("sector") or "Unknown")
    rows = list(COMMON_DIMENSIONS)
    seen = {str(row["dimension"]) for row in rows}
    for row in SECTOR_MODULES.get(sector, ()):
        dimension = str(row["dimension"])
        if dimension in BANK_SPECIFIC_DIMENSIONS and not dimension_eligibility(entity, dimension)["eligible"]:
            continue
        if dimension not in seen:
            rows.append(row)
            seen.add(dimension)
    return tuple(rows)


def entity_belief_definitions(entity: Mapping[str, Any]) -> Tuple[Dict[str, Any], ...]:
    entity_id = str(entity["entity_id"]).lower()
    symbol = _market_symbol(entity)
    sector = str(entity.get("sector") or "Unknown")
    profile = semantic_profile(entity)
    definitions = []
    for row in dimension_registry_for(entity):
        dimension = str(row["dimension"])
        eligibility = dimension_eligibility(entity, dimension)
        definitions.append({
            "belief_id": f"entity.{entity_id}.{dimension}",
            "entity_id": entity_id,
            "market_symbol": symbol,
            "sector": sector,
            "exposure_key": profile.get("exposure_key"),
            "entity_archetype": profile["entity_archetype"],
            "semantic_eligibility_contract_version": SEMANTIC_ELIGIBILITY_CONTRACT_VERSION,
            "eligibility_scope": eligibility["eligibility_scope"],
            "dimension": dimension,
            "horizon": row["horizon"],
            "outcome_family": row["outcome_family"],
            "reporting_regime": "unresolved_requires_primary_source_adapter",
            "evidence_adapter_status": "not_enabled_in_pr12",
            "outcome_contract_status": "required_before_forecast_capture",
            "forecast_capture_enabled": False,
            "engine_influence_enabled": False,
            "with_without_required_before_promotion": True,
        })
    return tuple(definitions)


def empty_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "activation_boundary_established_at": None,
        "entities": {},
    }


def _append_semantic_event(existing: MutableMapping[str, Any], row: Mapping[str, Any], when: str) -> None:
    profile = semantic_profile(row)
    existing.setdefault("semantic_eligibility_events", [])
    event_payload = {
        "contract_version": SEMANTIC_ELIGIBILITY_CONTRACT_VERSION,
        "entity_archetype": profile["entity_archetype"],
        "entity_archetype_source": profile["entity_archetype_source"],
        "exposure_key": profile.get("exposure_key"),
        "bank_specific_dimensions_eligible": profile["bank_specific_dimensions_eligible"],
    }
    event_key = _canonical_sha256(event_payload)[:20]
    known = {str(x.get("event_key")) for x in existing.get("semantic_eligibility_events", []) if isinstance(x, Mapping)}
    if event_key not in known:
        existing["semantic_eligibility_events"].append({
            "event_key": event_key,
            "observed_at": when,
            **event_payload,
            "historical_semantic_state_preserved": True,
        })


def update_activation_state(
    previous: Mapping[str, Any],
    desired: Mapping[str, Mapping[str, Any]],
    as_of: datetime,
) -> Dict[str, Any]:
    when = as_of.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    state = deepcopy(dict(previous or empty_state()))
    state["schema_version"] = SCHEMA_VERSION
    state["mode"] = MODE
    state.setdefault("entities", {})
    if not state.get("activation_boundary_established_at"):
        state["activation_boundary_established_at"] = when

    entities: MutableMapping[str, Any] = state["entities"]
    for entity_id, row in desired.items():
        existing = deepcopy(dict(entities.get(entity_id) or {}))
        source = str(row.get("activation_source"))
        if not existing:
            existing = {
                "entity_id": entity_id,
                "first_activated_at": when,
                "first_activation_source": source,
                "ever_current_portfolio": False,
                "ever_candidate_watchlist": False,
                "activation_events": [],
                "semantic_eligibility_events": [],
            }
        event_key = f"{source}:{when[:10]}"
        known = {str(x.get("event_key")) for x in existing.get("activation_events", [])}
        if event_key not in known:
            existing.setdefault("activation_events", []).append({
                "event_key": event_key,
                "observed_at": when,
                "source": source,
                "candidate_rank": row.get("candidate_rank"),
            })
        _append_semantic_event(existing, row, when)
        profile = semantic_profile(row)
        existing["ever_current_portfolio"] = bool(
            existing.get("ever_current_portfolio") or "current_portfolio" in source
        )
        existing["ever_candidate_watchlist"] = bool(
            existing.get("ever_candidate_watchlist") or "candidate" in source
        )
        existing.update({
            "current_status": "active",
            "current_activation_source": source,
            "activation_class": row.get("activation_class"),
            "last_seen_at": when,
            "candidate_rank": row.get("candidate_rank"),
            "market_symbol": _market_symbol(row),
            "sector": row.get("sector"),
            "exposure_key": profile.get("exposure_key"),
            "entity_archetype": profile["entity_archetype"],
            "entity_archetype_source": profile["entity_archetype_source"],
            "semantic_eligibility_contract_version": SEMANTIC_ELIGIBILITY_CONTRACT_VERSION,
            "bank_specific_dimensions_eligible": profile["bank_specific_dimensions_eligible"],
            "region": row.get("region"),
            "exchange": row.get("exchange"),
            "asset_type": row.get("asset_type"),
        })
        entities[entity_id] = existing

    for entity_id, existing_raw in list(entities.items()):
        if entity_id in desired:
            continue
        existing = deepcopy(dict(existing_raw))
        existing["current_status"] = "dormant"
        existing["current_activation_source"] = "not_currently_portfolio_or_candidate"
        existing["activation_class"] = "history_preserved_dormant"
        existing["candidate_rank"] = None
        entities[entity_id] = existing

    state["last_updated_at"] = when
    return state


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


def build_report(
    state: Mapping[str, Any],
    desired: Mapping[str, Mapping[str, Any]],
    *,
    portfolio: Mapping[str, Any],
    analysis: Mapping[str, Any],
    universe: Mapping[str, Any],
    as_of: datetime,
) -> Dict[str, Any]:
    _assert_safety()
    active_rows = []
    dormant_rows = []
    definitions = []
    for entity_id, row in sorted((state.get("entities") or {}).items()):
        materialized = {
            **dict(row),
            "definitions": list(entity_belief_definitions(desired[entity_id])) if entity_id in desired else [],
        }
        if row.get("current_status") == "active":
            active_rows.append(materialized)
            definitions.extend(materialized["definitions"])
        else:
            dormant_rows.append(materialized)

    portfolio_active = sum("current_portfolio" in str(x.get("current_activation_source")) for x in active_rows)
    candidate_active = sum("candidate" in str(x.get("current_activation_source")) for x in active_rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "generated_at": as_of.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": MODE,
        "active_decision_influence": False,
        "hierarchy": {
            "broad_market": "prerequisite_layer",
            "sector_factor": "prerequisite_layer",
            "active_layer": "company_entity_framework",
            "entity_evidence_and_forecasts": "deferred_to_later_reviewed_pr",
            "engine_entity_bridge": "deferred_to_later_reviewed_pr",
        },
        "activation_policy": {
            "current_portfolio": "always_on",
            "new_company": "activate_when_present_in_canonical_BRACE_analysis_candidates",
            "portfolio_entry_not_required": True,
            "candidate_source": "data/portfolio10k/analysis.json:candidates",
            "additional_hidden_candidate_threshold": False,
            "non_candidate_universe": "inactive",
            "candidate_removed": "dormant_history_preserved",
            "reactivation": "resume_existing_lineage",
        },
        "anti_hindsight": {
            "historical_backfill": False,
            "activation_boundary_established_at": state.get("activation_boundary_established_at"),
            "pre_pr12_candidate_history_reconstructed": False,
            "first_activation_timestamp_immutable": True,
            "semantic_history_is_append_only": True,
            "past_bank_specific_definitions_are_not_deleted_by_pr19_1": True,
        },
        "semantic_eligibility": {
            "contract_version": SEMANTIC_ELIGIBILITY_CONTRACT_VERSION,
            "sector_is_not_business_model": True,
            "bank_specific_dimensions": list(BANK_SPECIFIC_DIMENSIONS),
            "bank_specific_dimensions_require_entity_archetype": "bank",
            "financials_without_resolved_bank_archetype": "fail_closed",
            "ticker_specific_exceptions": False,
        },
        "dimension_registry": {
            "version": DIMENSION_REGISTRY_VERSION,
            "common": [dict(x) for x in COMMON_DIMENSIONS],
            "sector_modules": {key: [dict(x) for x in rows] for key, rows in SECTOR_MODULES.items()},
            "common_core_plus_sector_modules": True,
            "financials_module_is_business_model_gated": True,
            "company_specific_extensions": "later_reviewed_pr_only",
        },
        "entities": {
            "active": active_rows,
            "dormant": dormant_rows,
        },
        "materialized_belief_definitions": definitions,
        "sample": {
            "active_entities": len(active_rows),
            "active_current_portfolio_entities": portfolio_active,
            "active_candidate_watchlist_entities": candidate_active,
            "dormant_entities": len(dormant_rows),
            "materialized_definition_count": len(definitions),
            "bank_archetype_entities": sum(1 for row in active_rows if row.get("entity_archetype") == "bank"),
            "financials_fail_closed_entities": sum(1 for row in active_rows if row.get("entity_archetype") == "financials_unresolved"),
        },
        "source_provenance": {
            "portfolio_sha256": _canonical_sha256(portfolio),
            "analysis_sha256": _canonical_sha256(analysis),
            "universe_sha256": _canonical_sha256(universe),
        },
        "capabilities": capabilities(),
        "safety_controls": safety_controls(),
        "promotion_evidence_standard": promotion_evidence_standard(),
        "limitations": [
            "PR12 does not ingest issuer filings, earnings releases, transcripts or regulatory events.",
            "PR12 does not infer 10-K/10-Q versus 20-F/6-K reporting regimes; primary-source adapters must resolve issuer reporting regime before forecast capture.",
            "Financials sector membership alone does not authorize bank-specific Belief dimensions.",
            "Materialized entity beliefs are definitions only; probability, confidence and calibration are intentionally absent until evidence/outcome contracts exist.",
            "No company belief may influence BRACE or be promoted on the basis of this framework report.",
        ],
    }


def run(
    state_dir: Path,
    *,
    portfolio_path: Path = DEFAULT_PORTFOLIO,
    analysis_path: Path = DEFAULT_ANALYSIS,
    universe_path: Path = DEFAULT_UNIVERSE,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    _assert_safety()
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    portfolio = _read_json(portfolio_path, {})
    analysis = _read_json(analysis_path, {})
    universe = _read_json(universe_path, {})
    desired = desired_entities(portfolio, analysis, universe)
    state_path = state_dir / STATE_FILENAME
    previous = _read_json(state_path, empty_state())
    state = update_activation_state(previous, desired, now)
    report = build_report(
        state,
        desired,
        portfolio=portfolio,
        analysis=analysis,
        universe=universe,
        as_of=now,
    )
    _write_json(state_path, state)
    _write_json(state_dir / REPORT_FILENAME, report)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--portfolio", type=Path, default=DEFAULT_PORTFOLIO)
    parser.add_argument("--analysis", type=Path, default=DEFAULT_ANALYSIS)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--as-of", default=None, help="ISO timestamp for deterministic tests")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of else None
    report = run(
        args.state_dir,
        portfolio_path=args.portfolio,
        analysis_path=args.analysis,
        universe_path=args.universe,
        as_of=as_of,
    )
    print(json.dumps({
        "mode": report["mode"],
        "sample": report["sample"],
        "activation_policy": report["activation_policy"],
        "active_decision_influence": report["active_decision_influence"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
