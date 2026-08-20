#!/usr/bin/env python3
"""PR #19 — Epistemic Contract & Causal Belief Graph v1.

Research-shadow epistemic metadata only.

This module adds a reviewed epistemic contract beside the existing Belief Core
state. It does not change Belief probabilities, evidence weights, forecasts,
BRACE/WES decisions, ranking, sizing, exposure, execution or promotion.

The contract explicitly separates:
- the claim itself;
- assumptions required for the claim/transmission story;
- a hypothesised transmission path;
- falsifiers;
- alternative explanations;
- unknowns;
- regime dependencies;
- the existing operational outcome rule and its measurement limitations.

Important boundary: a graph edge is a research hypothesis, never proof of
causality. PR19 never infers causality from correlation and never turns graph
structure into an alpha score.

Prospective binding boundary
----------------------------
PR19 snapshots the epistemic graph append-only. Existing PR15 Entity forecasts
on the first run are cursor-only. A later PR15 forecast may be bound only to a
graph snapshot and epistemic contract that both existed before ``forecast_at``.
If no such snapshot exists, the forecast is terminally unbound; PR19 never
attaches a later, more convenient causal story retroactively.
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

try:
    from brace_broad_market_belief import BROAD_MARKET_BELIEFS, MACRO, RATES, LIQUIDITY, RISK
    from brace_sector_factor_belief import SECTOR_FACTOR_BELIEFS, SPEC_BY_ID
    from brace_entity_belief_state_forecast import (
        CONTRACT_VERSION as PR15_CONTRACT_VERSION,
        DIMENSION_CONFIG,
        FORECAST_HORIZON_HOURS,
    )
except ModuleNotFoundError:  # package imports in unit tests
    from scripts.brace_broad_market_belief import BROAD_MARKET_BELIEFS, MACRO, RATES, LIQUIDITY, RISK
    from scripts.brace_sector_factor_belief import SECTOR_FACTOR_BELIEFS, SPEC_BY_ID
    from scripts.brace_entity_belief_state_forecast import (
        CONTRACT_VERSION as PR15_CONTRACT_VERSION,
        DIMENSION_CONFIG,
        FORECAST_HORIZON_HOURS,
    )

MODE = "research_shadow"
SCHEMA_VERSION = "belief-epistemic-causal-graph-v1"
REPORT_VERSION = "belief-epistemic-causal-graph-report-v1"
CONTRACT_VERSION = "belief-epistemic-contract-v1"
GRAPH_CONTRACT_VERSION = "belief-causal-graph-contract-v1"
FORECAST_BINDING_CONTRACT_VERSION = "entity-forecast-epistemic-binding-v1"
STATE_FILENAME = "BELIEF_EPISTEMIC_CAUSAL_GRAPH_STATE.json"
REPORT_FILENAME = "BELIEF_EPISTEMIC_CAUSAL_GRAPH_REPORT.json"

CAUSAL_STATUS = "UNVERIFIED_HYPOTHESIS"
NO_CAUSAL_PROOF = "NOT_CAUSAL_PROOF"


def safety_controls() -> Dict[str, bool]:
    return {
        "active_decision_influence": False,
        "belief_probability_writeback": False,
        "belief_confidence_writeback": False,
        "evidence_weight_writeback": False,
        "forecast_rewrite": False,
        "historical_forecast_epistemic_backfill": False,
        "retroactive_causal_classification": False,
        "correlation_to_causation_inference": False,
        "causal_edge_auto_discovery": False,
        "alpha_score_output": False,
        "miv_score_output": False,
        "engine_score_writeback": False,
        "candidate_ranking_change": False,
        "target_exposure_change": False,
        "sizing_change": False,
        "veto": False,
        "forced_exit": False,
        "direction_reversal": False,
        "trade_execution": False,
        "automatic_tuning": False,
        "engine_specific_trust_output": False,
        "automatic_promotion": False,
    }


def capabilities() -> Dict[str, bool]:
    return {
        "epistemic_contract_registry_enabled": True,
        "causal_hypothesis_graph_enabled": True,
        "claim_assumption_separation_enabled": True,
        "explicit_falsifiers_enabled": True,
        "alternative_explanations_enabled": True,
        "unknowns_registry_enabled": True,
        "regime_dependencies_enabled": True,
        "measurement_gap_registry_enabled": True,
        "prospective_forecast_epistemic_binding_enabled": True,
        "causal_claim_verification_enabled": False,
        "automatic_abduction_enabled": False,
        "causal_graph_alpha_enabled": False,
        "engine_specific_trust_enabled": False,
        "promotion_gate_enabled": False,
    }


def _assert_safety() -> None:
    bad = [key for key, value in safety_controls().items() if value is not False]
    if bad:
        raise RuntimeError("PR19 zero-authority invariant violated: " + ",".join(bad))


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


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}-{_sha(payload)[:20]}"


def _belief_parts(belief_id: str) -> Optional[Tuple[str, str]]:
    text = str(belief_id or "")
    if not text.startswith("entity."):
        return None
    rest = text[len("entity."):]
    if "." not in rest:
        return None
    entity_id, dimension = rest.rsplit(".", 1)
    if not entity_id or dimension not in DIMENSION_CONFIG:
        return None
    return entity_id, dimension


def _row_id(prefix: str, key: str) -> str:
    return prefix + "." + key.replace(" ", "_").replace("/", "_").lower()


def _assumptions(rows: Sequence[Tuple[str, str, str]]) -> List[Dict[str, Any]]:
    return [
        {
            "assumption_id": _row_id("assumption", key),
            "statement": statement,
            "role": role,
            "status": "EXPLICIT_UNVERIFIED_ASSUMPTION",
        }
        for key, statement, role in rows
    ]


def _alternatives(rows: Sequence[Tuple[str, str, str]]) -> List[Dict[str, Any]]:
    return [
        {
            "alternative_id": _row_id("alternative", key),
            "explanation": explanation,
            "discriminating_evidence": evidence,
            "status": "OPEN_ALTERNATIVE",
        }
        for key, explanation, evidence in rows
    ]


def _unknowns(rows: Sequence[Tuple[str, str, str]]) -> List[Dict[str, Any]]:
    return [
        {
            "unknown_id": _row_id("unknown", key),
            "statement": statement,
            "materiality": materiality,
            "status": "UNRESOLVED",
        }
        for key, statement, materiality in rows
    ]


def _regimes(rows: Sequence[Tuple[str, str, str]]) -> List[Dict[str, Any]]:
    return [
        {
            "dependency_id": _row_id("regime", key),
            "condition": condition,
            "failure_mode": failure_mode,
            "status": "CONDITIONAL_APPLICABILITY",
        }
        for key, condition, failure_mode in rows
    ]


def _falsifier(
    key: str,
    target: str,
    statement: str,
    *,
    machine_testable: bool,
    evaluator: Optional[str] = None,
    strength: str = "DIRECT_FOR_OPERATIONAL_RULE",
) -> Dict[str, Any]:
    return {
        "falsifier_id": _row_id("falsifier", key),
        "target": target,
        "statement": statement,
        "machine_testable": bool(machine_testable),
        "existing_evaluator": evaluator,
        "falsification_strength": strength,
        "prospective_only": True,
    }


def _canonical_definition_rows(entity_belief_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for definition in BROAD_MARKET_BELIEFS:
        out[str(definition.belief_id)] = {
            "belief_id": str(definition.belief_id),
            "layer": "broad_market",
            "claim": str(definition.claim),
            "outcome_rule": str(definition.outcome_rule),
            "horizon_hours": float(definition.horizon_hours or 0.0),
            "domain": str(definition.domain or ""),
            "source_contract": "brace-broad-market-belief-v1",
        }
    for definition in SECTOR_FACTOR_BELIEFS:
        belief_id = str(definition.belief_id)
        spec = SPEC_BY_ID[belief_id]
        out[belief_id] = {
            "belief_id": belief_id,
            "layer": str(spec["layer"]),
            "claim": str(definition.claim),
            "outcome_rule": str(definition.outcome_rule),
            "horizon_hours": float(definition.horizon_hours or 0.0),
            "domain": str(definition.domain or ""),
            "source_contract": "brace-sector-factor-belief-v1",
        }
    for belief_id in sorted(set(str(x) for x in entity_belief_ids)):
        parts = _belief_parts(belief_id)
        if parts is None:
            continue
        entity_id, dimension = parts
        config = DIMENSION_CONFIG[dimension]
        out[belief_id] = {
            "belief_id": belief_id,
            "layer": "entity",
            "claim": str(config["claim"]).format(entity=entity_id.upper()),
            "outcome_rule": str(config["outcome_rule"]),
            "horizon_hours": float(FORECAST_HORIZON_HOURS),
            "domain": "entity_fundamentals",
            "dimension": dimension,
            "entity_id": entity_id,
            "source_contract": PR15_CONTRACT_VERSION,
        }
    for row in out.values():
        row["definition_fingerprint"] = _sha({
            "belief_id": row["belief_id"],
            "claim": row["claim"],
            "outcome_rule": row["outcome_rule"],
            "horizon_hours": row["horizon_hours"],
            "domain": row["domain"],
        })
    return out


MARKET_SPEC: Mapping[str, Mapping[str, Any]] = {
    RATES: {
        "measurement_relation": "PARTIAL_PROXY",
        "measurement_limitations": [
            "The operational TLT outcome tests a duration/rates-pressure proxy, not the full risk-asset support claim.",
            "Term premium, growth expectations and positioning can move duration independently of the intended discount-rate channel.",
        ],
        "assumptions": _assumptions((
            ("duration_proxy", "TLT remains informative about the relevant rates-pressure channel over the forecast horizon.", "measurement"),
            ("discount_rate_channel", "Lower rates pressure can transmit to risk assets through discount rates before a dominant offsetting shock intervenes.", "transmission"),
        )),
        "transmission": [
            {"from": RATES, "to": "mechanism.discount_rate_relief", "mechanism": "discount-rate channel", "direction": "supportive", "expected_lag": "hours_to_days", "status": CAUSAL_STATUS},
        ],
        "falsifiers": [
            _falsifier("rates_operational", "operational_outcome", "TLT closes below the frozen reference under the existing PR10 target rule.", machine_testable=True, evaluator="brace_broad_market_belief.evaluate_outcome", strength="DIRECT_FOR_OPERATIONAL_PROXY_ONLY"),
            _falsifier("rates_transmission", "transmission_assumption", "Rates pressure eases materially while growth/risk-sensitive assets deteriorate without a separately identified dominant shock.", machine_testable=False, strength="RESEARCH_HYPOTHESIS"),
        ],
        "alternatives": _alternatives((
            ("recession_duration_bid", "Duration rallies because recession risk rises rather than because financial conditions become supportive.", "Compare earnings expectations, credit and equity breadth."),
            ("term_premium", "Term-premium or supply effects dominate the observed duration move.", "Compare real yields, inflation compensation and curve decomposition when available."),
        )),
        "unknowns": _unknowns((
            ("rates_magnitude", "The magnitude and nonlinearity of the equity response to a given rates move are not known ex ante.", "high"),
            ("rates_lag", "The relevant transmission lag can vary by regime.", "medium"),
        )),
        "regimes": _regimes((
            ("inflation_shock", "Inflation or supply shock dominates discount-rate relief.", "Rates relief may not translate into risk-asset support."),
            ("recession", "Falling yields coincide with rapidly worsening growth expectations.", "The sign of the equity transmission can reverse."),
        )),
    },
    LIQUIDITY: {
        "measurement_relation": "PARTIAL_PROXY",
        "measurement_limitations": [
            "HYG/LQD is a credit-risk-appetite/liquidity proxy, not dealer balance-sheet, funding or order-book liquidity itself.",
        ],
        "assumptions": _assumptions((
            ("credit_proxy", "HYG/LQD remains informative about broad credit risk appetite over the horizon.", "measurement"),
            ("financing_channel", "Improving financing/risk capacity can support higher-beta assets before an offsetting shock dominates.", "transmission"),
        )),
        "transmission": [
            {"from": LIQUIDITY, "to": "mechanism.financing_and_risk_capacity", "mechanism": "credit/liquidity channel", "direction": "supportive", "expected_lag": "hours_to_days", "status": CAUSAL_STATUS},
        ],
        "falsifiers": [
            _falsifier("liquidity_operational", "operational_outcome", "HYG/LQD closes below the frozen reference under the existing PR10 target rule.", machine_testable=True, evaluator="brace_broad_market_belief.evaluate_outcome", strength="DIRECT_FOR_OPERATIONAL_PROXY_ONLY"),
            _falsifier("liquidity_transmission", "transmission_assumption", "Credit/liquidity proxy improves while financing-sensitive leadership persistently deteriorates without an identified offsetting shock.", machine_testable=False, strength="RESEARCH_HYPOTHESIS"),
        ],
        "alternatives": _alternatives((
            ("credit_beta", "The ratio move reflects credit beta or duration composition rather than broad liquidity.", "Compare funding, spreads and breadth when available."),
            ("technical_flows", "ETF technical flows create a temporary ratio move.", "Compare persistence and non-ETF credit evidence."),
        )),
        "unknowns": _unknowns((
            ("liquidity_scope", "The proxy does not observe all funding and market-liquidity channels.", "high"),
            ("liquidity_threshold", "The amount of improvement needed to matter economically is unknown.", "medium"),
        )),
        "regimes": _regimes((
            ("funding_stress", "Funding stress emerges outside the HYG/LQD proxy.", "The proxy can look benign while true liquidity deteriorates."),
            ("event_shock", "A discrete event shock dominates financing conditions.", "Transmission to risk assets can break."),
        )),
    },
    MACRO: {
        "measurement_relation": "PARTIAL_PROXY_PLUS_PRIMARY_INPUTS",
        "measurement_limitations": [
            "The operational outcome is a cross-asset majority rule and does not directly verify the full macro causal story.",
            "BLS evidence improves provenance but remains a partial view of the macro state.",
        ],
        "assumptions": _assumptions((
            ("macro_proxy", "The selected BLS and cross-asset inputs are sufficiently representative of the near-term macro/financial backdrop.", "measurement"),
            ("earnings_channel", "A supportive macro backdrop can transmit through demand and earnings expectations.", "transmission"),
        )),
        "transmission": [
            {"from": MACRO, "to": "mechanism.earnings_expectation_support", "mechanism": "demand/earnings-expectations channel", "direction": "supportive", "expected_lag": "days_to_weeks", "status": CAUSAL_STATUS},
        ],
        "falsifiers": [
            _falsifier("macro_operational", "operational_outcome", "The existing PR10 macro cross-asset majority outcome resolves adverse.", machine_testable=True, evaluator="brace_broad_market_belief.evaluate_outcome", strength="DIRECT_FOR_OPERATIONAL_PROXY_ONLY"),
            _falsifier("macro_transmission", "transmission_assumption", "Macro inputs remain supportive but earnings expectations and economically sensitive leadership deteriorate without a separately identified shock.", machine_testable=False, strength="RESEARCH_HYPOTHESIS"),
        ],
        "alternatives": _alternatives((
            ("market_leads_macro", "Market prices move ahead of reported macro data rather than because reported macro conditions caused the move.", "Compare timestamp ordering and revisions."),
            ("policy_offset", "Policy expectations offset or dominate the observed macro impulse.", "Compare rates and liquidity channels."),
        )),
        "unknowns": _unknowns((
            ("macro_revision", "Future revisions can change the interpretation of contemporaneous macro data.", "medium"),
            ("macro_mapping", "The mapping from macro support to sector leadership is regime dependent.", "high"),
        )),
        "regimes": _regimes((
            ("stagflation", "Growth and inflation impulses conflict.", "A single supportive/adverse label can become insufficient."),
            ("policy_transition", "A major policy transition changes the market response function.", "Historical transmission assumptions can lose applicability."),
        )),
    },
    RISK: {
        "measurement_relation": "PARTIAL_CROSS_ASSET_PROXY",
        "measurement_limitations": [
            "The majority rule captures a non-defensive cross-asset state, not a structural causal regime by itself.",
        ],
        "assumptions": _assumptions((
            ("risk_proxy", "SPY, breadth, credit, volatility and USD jointly provide a useful near-term risk-regime proxy.", "measurement"),
            ("risk_appetite", "A non-defensive regime can transmit through investor risk appetite and positioning.", "transmission"),
        )),
        "transmission": [
            {"from": RISK, "to": "mechanism.risk_appetite", "mechanism": "risk-appetite/positioning channel", "direction": "supportive", "expected_lag": "hours_to_days", "status": CAUSAL_STATUS},
        ],
        "falsifiers": [
            _falsifier("risk_operational", "operational_outcome", "The existing PR10 risk-regime majority outcome resolves defensive/adverse.", machine_testable=True, evaluator="brace_broad_market_belief.evaluate_outcome", strength="DIRECT_FOR_OPERATIONAL_PROXY_ONLY"),
            _falsifier("risk_transmission", "transmission_assumption", "The non-defensive proxy persists while risk-sensitive factor leadership deteriorates without a separately identified offsetting shock.", machine_testable=False, strength="RESEARCH_HYPOTHESIS"),
        ],
        "alternatives": _alternatives((
            ("index_concentration", "Index concentration masks weak underlying risk appetite.", "Compare equal-weight breadth and cross-sectional participation."),
            ("volatility_supply", "Volatility-supply mechanics suppress VIX without genuine improvement in fundamentals.", "Compare credit and breadth confirmation."),
        )),
        "unknowns": _unknowns((
            ("risk_crowding", "Consensus risk-on positioning can become fragile even while the proxy remains supportive.", "high"),
        )),
        "regimes": _regimes((
            ("crowded_risk_on", "Positioning becomes one-sided.", "A supportive regime can contain elevated crash risk."),
            ("event_risk", "Discrete geopolitical or policy event risk dominates.", "Cross-asset regime can switch faster than the normal horizon."),
        )),
    },
}


ENTITY_DIMENSION_SPEC: Mapping[str, Mapping[str, Any]] = {
    "revenue_durability": {
        "mechanism": "demand_and_monetization_persistence",
        "assumptions": _assumptions((
            ("revenue_comparability", "The next comparable reporting observation is economically comparable to the frozen base period.", "measurement"),
            ("revenue_structure", "No unmodelled acquisition, disposal or accounting reclassification dominates reported revenue change.", "auxiliary"),
        )),
        "alternatives": _alternatives((
            ("revenue_fx", "FX translation rather than underlying demand explains the reported revenue change.", "Inspect constant-currency or geographic evidence when available."),
            ("revenue_ma", "M&A or portfolio changes create the apparent durability signal.", "Inspect acquisition/disposal disclosures."),
        )),
        "unknowns": _unknowns((
            ("revenue_quality", "Revenue growth quality and pricing/volume mix may not be identifiable from the current contract.", "high"),
        )),
        "regimes": _regimes((
            ("demand_cycle", "Sector demand cycle changes materially before the next report.", "Historical durability can lose predictive relevance."),
        )),
    },
    "earnings_momentum": {
        "mechanism": "earnings_conversion_persistence",
        "assumptions": _assumptions((
            ("earnings_comparability", "EPS/net-income observations remain comparable across the forecast window.", "measurement"),
            ("earnings_oneoffs", "One-off items do not dominate the interpreted earnings trajectory.", "auxiliary"),
        )),
        "alternatives": _alternatives((
            ("buyback_eps", "Share-count changes rather than operating improvement explain EPS momentum.", "Compare diluted share count and net income."),
            ("oneoff_earnings", "Tax, impairment or other one-offs dominate net income.", "Inspect reconciliation and unusual-item disclosures."),
        )),
        "unknowns": _unknowns((
            ("earnings_quality", "The persistence of accounting earnings into cash earnings is not fully observed.", "high"),
        )),
        "regimes": _regimes((
            ("earnings_cycle", "A rapid earnings-cycle turn occurs before the next comparable report.", "Current momentum can reverse before verification."),
        )),
    },
    "margin_trajectory": {
        "mechanism": "operating_leverage_and_mix",
        "assumptions": _assumptions((
            ("margin_comparability", "Operating-margin definitions remain comparable across the forecast window.", "measurement"),
            ("margin_mix", "Major mix or classification changes are identified rather than mistaken for operating leverage.", "auxiliary"),
        )),
        "alternatives": _alternatives((
            ("margin_mix_shift", "Product/geographic mix rather than structural efficiency explains the margin change.", "Inspect segment mix and gross/operating bridge when available."),
            ("temporary_cost", "Temporary cost timing creates a non-persistent margin move.", "Inspect management explanations and subsequent normalization."),
        )),
        "unknowns": _unknowns((
            ("margin_cost_curve", "Future cost elasticity to revenue is not directly observed.", "high"),
        )),
        "regimes": _regimes((
            ("input_cost_shock", "Input, wage or logistics costs shift abruptly.", "Past margin trajectory can become non-transferable."),
        )),
    },
    "net_interest_income_durability": {
        "mechanism": "bank_asset_liability_spread_and_volume",
        "assumptions": _assumptions((
            ("nii_comparability", "NII reporting remains comparable and reflects recurring asset-liability economics.", "measurement"),
            ("balance_sheet_stability", "No unmodelled balance-sheet restructuring dominates the next NII observation.", "auxiliary"),
        )),
        "alternatives": _alternatives((
            ("nii_volume", "Balance-sheet volume rather than spread durability explains NII change.", "Separate average balances from yield/cost effects when available."),
            ("nii_hedges", "Hedging/accounting effects dominate reported NII movement.", "Inspect hedge and ALM disclosures."),
        )),
        "unknowns": _unknowns((
            ("deposit_beta", "Future deposit beta and repricing speed are uncertain.", "high"),
        )),
        "regimes": _regimes((
            ("rate_regime", "The policy/rates path changes materially.", "Asset-liability repricing assumptions can fail."),
            ("deposit_stress", "Deposit competition or outflows accelerate.", "NII durability can deteriorate independently of asset yields."),
        )),
    },
}


def _sector_factor_contract(definition: Mapping[str, Any]) -> Dict[str, Any]:
    belief_id = str(definition["belief_id"])
    spec = SPEC_BY_ID[belief_id]
    numerator, denominator = str(spec["numerator"]), str(spec["denominator"])
    return {
        "measurement_relation": "OPERATIONALLY_DIRECT_RELATIVE_PRICE_PROXY",
        "measurement_limitations": [
            f"{numerator}/{denominator} verifies relative ETF-price leadership, not the complete fundamental state of the category.",
            "Index composition, concentration and ETF flows can affect measured leadership.",
        ],
        "assumptions": _assumptions((
            (belief_id + "_proxy", f"{numerator}/{denominator} remains a reasonable liquid proxy for the stated {spec['label']} leadership concept.", "measurement"),
            (belief_id + "_persistence", "Relative leadership has enough persistence to be meaningful over the next-session forecast horizon.", "auxiliary"),
        )),
        "transmission": [
            {"from": "mechanism.relative_demand_and_fundamental_support", "to": belief_id, "mechanism": "relative leadership expression", "direction": "supportive", "expected_lag": "same_session_to_days", "status": CAUSAL_STATUS},
        ],
        "falsifiers": [
            _falsifier(belief_id + "_operational", "operational_outcome", f"{numerator}/{denominator} closes below its frozen reference under the PR11 target rule.", machine_testable=True, evaluator="brace_sector_factor_belief.evaluate_outcome"),
        ],
        "alternatives": _alternatives((
            (belief_id + "_flows", "ETF/index flow or concentration effects produce the observed relative move without broad category improvement.", "Compare breadth and constituent dispersion when available."),
            (belief_id + "_beta", "The relative move is a transient beta/regime effect rather than persistent leadership.", "Compare persistence across multiple independent states."),
        )),
        "unknowns": _unknowns((
            (belief_id + "_attribution", "The current contract cannot fully attribute leadership to fundamentals, positioning or flows.", "high"),
        )),
        "regimes": _regimes((
            (belief_id + "_stress", "Broad market stress overwhelms category-specific information.", "Relative leadership can become a defensive/beta artifact."),
        )),
    }


def _entity_contract(definition: Mapping[str, Any]) -> Dict[str, Any]:
    belief_id = str(definition["belief_id"])
    entity_id = str(definition["entity_id"])
    dimension = str(definition["dimension"])
    spec = ENTITY_DIMENSION_SPEC[dimension]
    mechanism_node = f"mechanism.entity.{entity_id}.{spec['mechanism']}"
    info_node = f"information.entity.{entity_id}.fundamental_context"
    return {
        "measurement_relation": "DIRECT_TO_REVIEWED_PR14_INTERPRETATION_CONTRACT",
        "measurement_limitations": [
            "The forecast is verified against the next comparable PR14 interpretation, not against stock return.",
            "A correct fundamental forecast does not by itself establish incremental economic value for BRACE.",
        ],
        "assumptions": deepcopy(spec["assumptions"]),
        "transmission": [
            {"from": belief_id, "to": mechanism_node, "mechanism": str(spec["mechanism"]), "direction": "supportive", "expected_lag": "reporting_horizon", "status": CAUSAL_STATUS},
            {"from": mechanism_node, "to": info_node, "mechanism": "fundamental-information channel", "direction": "supportive", "expected_lag": "reporting_horizon", "status": CAUSAL_STATUS},
        ],
        "falsifiers": [
            _falsifier(belief_id + "_operational", "claim", "The next comparable PR14 interpretation inside the frozen horizon resolves oppose.", machine_testable=True, evaluator="brace_entity_belief_state_forecast._resolve_due_forecasts", strength="DIRECT_FOR_PR15_FORECAST_TARGET"),
            _falsifier(belief_id + "_economic", "economic_transmission", "The fundamental belief verifies correctly but repeated prospective WITH/WITHOUT evidence shows no incremental economic value after dependence controls.", machine_testable=False, strength="FUTURE_BRIDGE_RESEARCH_ONLY"),
        ],
        "alternatives": deepcopy(spec["alternatives"]),
        "unknowns": deepcopy(spec["unknowns"]),
        "regimes": deepcopy(spec["regimes"]),
    }


def build_epistemic_contracts(entity_belief_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    definitions = _canonical_definition_rows(entity_belief_ids)
    contracts: Dict[str, Dict[str, Any]] = {}
    for belief_id, definition in sorted(definitions.items()):
        if belief_id in MARKET_SPEC:
            spec = deepcopy(MARKET_SPEC[belief_id])
        elif belief_id in SPEC_BY_ID:
            spec = _sector_factor_contract(definition)
        elif definition.get("layer") == "entity":
            spec = _entity_contract(definition)
        else:
            raise ValueError(f"No reviewed PR19 epistemic specification for {belief_id}")
        base = {
            "contract_version": CONTRACT_VERSION,
            "belief_id": belief_id,
            "layer": definition["layer"],
            "domain": definition["domain"],
            "claim": definition["claim"],
            "claim_status": "TESTABLE_BELIEF_HYPOTHESIS",
            "operational_outcome_rule": definition["outcome_rule"],
            "horizon_hours": definition["horizon_hours"],
            "source_definition_contract": definition["source_contract"],
            "source_definition_fingerprint": definition["definition_fingerprint"],
            "causal_status": CAUSAL_STATUS,
            "causal_proof": False,
            "pnl_tuned": False,
            "decision_influence": False,
            "promotion_authority": False,
            "scope_conditions": [
                "Only information available prospectively under the source Belief contract is admissible.",
                "A failed transmission assumption does not automatically rewrite the underlying Belief probability.",
                "A regime-dependency failure can make the hypothesis inapplicable without being relabelled as a successful/failed market call by hindsight.",
            ],
            "economic_transmission_status": "UNTESTED_BY_PR19",
        }
        base.update(spec)
        contract_payload = deepcopy(base)
        contract_payload["epistemic_contract_id"] = _stable_id("epistemic", contract_payload)
        contract_payload["immutable_sha256"] = _sha(contract_payload)
        contracts[belief_id] = contract_payload
    validate_contracts(contracts, definitions)
    return contracts


def validate_contracts(contracts: Mapping[str, Mapping[str, Any]], definitions: Mapping[str, Mapping[str, Any]]) -> None:
    required = (
        "claim", "assumptions", "transmission", "falsifiers", "alternative_explanations",
        "unknowns", "regime_dependencies", "measurement_relation", "measurement_limitations",
    )
    for belief_id, contract in contracts.items():
        if belief_id not in definitions:
            raise ValueError(f"Epistemic contract has no canonical Belief definition: {belief_id}")
        if contract.get("claim") != definitions[belief_id].get("claim"):
            raise ValueError(f"Epistemic claim drift from canonical Belief definition: {belief_id}")
        if contract.get("operational_outcome_rule") != definitions[belief_id].get("outcome_rule"):
            raise ValueError(f"Epistemic outcome-rule drift: {belief_id}")
        for key in required:
            value = contract.get(key)
            if not value:
                raise ValueError(f"Epistemic contract missing {key}: {belief_id}")
        if not any(bool(row.get("machine_testable")) for row in contract.get("falsifiers") or []):
            raise ValueError(f"Epistemic contract requires at least one machine-testable operational falsifier: {belief_id}")
        if contract.get("causal_proof") is not False or contract.get("decision_influence") is not False:
            raise ValueError(f"PR19 causal/authority boundary violated: {belief_id}")


def _node(node_id: str, node_type: str, **extra: Any) -> Dict[str, Any]:
    return {"node_id": node_id, "node_type": node_type, **extra}


def _edge(source: str, target: str, mechanism: str, *, edge_type: str = "hypothesized_causal_channel", direction: str = "supportive") -> Dict[str, Any]:
    payload = {
        "source": source,
        "target": target,
        "edge_type": edge_type,
        "mechanism": mechanism,
        "direction": direction,
        "causal_status": CAUSAL_STATUS if edge_type != "context_dependency" else "CONTEXT_ONLY_NOT_CAUSAL",
        "causal_proof": False,
        "pnl_tuned": False,
        "decision_influence": False,
    }
    payload["edge_id"] = _stable_id("edge", payload)
    return payload


def _add_market_graph(nodes: MutableMapping[str, Dict[str, Any]], edges: List[Dict[str, Any]], contracts: Mapping[str, Mapping[str, Any]]) -> None:
    channels = {
        RATES: ("mechanism.discount_rate_relief", "discount-rate channel", (
            "factor.growth.leadership", "sector.technology.leadership", "sector.semiconductors.leadership", "sector.consumer_discretionary.leadership",
        )),
        LIQUIDITY: ("mechanism.financing_and_risk_capacity", "financing/risk-capacity channel", (
            "factor.small_cap.leadership", "factor.growth.leadership", "factor.momentum.leadership",
        )),
        MACRO: ("mechanism.earnings_expectation_support", "demand/earnings-expectations channel", (
            "sector.financials.leadership", "sector.consumer_discretionary.leadership", "factor.quality.leadership", "factor.small_cap.leadership",
        )),
        RISK: ("mechanism.risk_appetite", "risk-appetite/positioning channel", (
            "factor.momentum.leadership", "factor.growth.leadership", "factor.small_cap.leadership",
        )),
    }
    for source, (mechanism_node, mechanism, targets) in channels.items():
        if source not in contracts:
            continue
        nodes.setdefault(mechanism_node, _node(mechanism_node, "mechanism", causal_status=CAUSAL_STATUS))
        edges.append(_edge(source, mechanism_node, mechanism))
        for target in targets:
            if target in contracts:
                edges.append(_edge(mechanism_node, target, mechanism))


def _add_entity_graph(nodes: MutableMapping[str, Dict[str, Any]], edges: List[Dict[str, Any]], contracts: Mapping[str, Mapping[str, Any]]) -> None:
    for belief_id, contract in sorted(contracts.items()):
        if contract.get("layer") != "entity":
            continue
        for step in contract.get("transmission") or []:
            source, target = str(step["from"]), str(step["to"])
            for node_id in (source, target):
                if node_id == belief_id:
                    continue
                node_type = "information" if node_id.startswith("information.") else "mechanism"
                nodes.setdefault(node_id, _node(node_id, node_type, causal_status=CAUSAL_STATUS))
            edges.append(_edge(source, target, str(step.get("mechanism") or "entity fundamental transmission")))


def _assert_causal_dag(nodes: Mapping[str, Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]) -> None:
    causal = [row for row in edges if row.get("edge_type") == "hypothesized_causal_channel"]
    adjacency: Dict[str, List[str]] = {key: [] for key in nodes}
    indegree: Dict[str, int] = {key: 0 for key in nodes}
    for edge in causal:
        source, target = str(edge["source"]), str(edge["target"])
        if source not in nodes or target not in nodes:
            raise ValueError(f"Causal edge references missing node: {source}->{target}")
        adjacency[source].append(target)
        indegree[target] += 1
    queue = [key for key, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        node_id = queue.pop()
        visited += 1
        for target in adjacency[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(nodes):
        raise ValueError("PR19 hypothesised causal graph must remain acyclic in v1")


def build_graph(contracts: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    for belief_id, contract in sorted(contracts.items()):
        nodes[belief_id] = _node(
            belief_id,
            "belief",
            layer=contract.get("layer"),
            epistemic_contract_id=contract.get("epistemic_contract_id"),
            causal_status=CAUSAL_STATUS,
        )
    nodes["mechanism.relative_demand_and_fundamental_support"] = _node(
        "mechanism.relative_demand_and_fundamental_support", "mechanism", causal_status=CAUSAL_STATUS
    )
    for belief_id, contract in sorted(contracts.items()):
        if contract.get("layer") in {"sector", "factor"}:
            edges.append(_edge("mechanism.relative_demand_and_fundamental_support", belief_id, "relative demand/fundamental support"))
    _add_market_graph(nodes, edges, contracts)
    _add_entity_graph(nodes, edges, contracts)
    edges.sort(key=lambda row: (str(row["source"]), str(row["target"]), str(row["edge_id"])))
    _assert_causal_dag(nodes, edges)
    graph = {
        "graph_contract_version": GRAPH_CONTRACT_VERSION,
        "causal_status": CAUSAL_STATUS,
        "causal_proof": False,
        "correlation_to_causation_inference": False,
        "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": edges,
    }
    graph["structure_sha256"] = _sha(graph)
    graph["graph_structure_id"] = "causal-graph-" + graph["structure_sha256"][:20]
    return graph


def empty_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "mode": MODE,
        "activated_at": None,
        "last_run_at": None,
        "graph_snapshots": {},
        "latest_graph_snapshot_id": None,
        "seen_pr15_forecast_ids": [],
        "pre_activation_forecast_ids": [],
        "forecast_bindings": {},
        "terminal_unbound_forecasts": {},
    }


def _validate_pr15_report(report: Mapping[str, Any]) -> None:
    if str(report.get("contract_version") or "") != PR15_CONTRACT_VERSION:
        raise ValueError("PR19 requires reviewed PR15 Entity forecast contract")
    if str(report.get("mode") or "") != MODE:
        raise ValueError("PR19 accepts PR15 research_shadow report only")
    if report.get("active_decision_influence") is not False:
        raise ValueError("PR19 refuses PR15 input with active decision influence")


def _entity_belief_ids(pr15_report: Mapping[str, Any]) -> Tuple[str, ...]:
    rows = []
    for row in pr15_report.get("belief_states") or []:
        if not isinstance(row, Mapping):
            continue
        belief_id = str(row.get("belief_id") or "")
        if _belief_parts(belief_id) is not None:
            rows.append(belief_id)
    for row in pr15_report.get("forecasts") or []:
        if not isinstance(row, Mapping):
            continue
        belief_id = str(row.get("belief_id") or "")
        if _belief_parts(belief_id) is not None:
            rows.append(belief_id)
    return tuple(sorted(set(rows)))


def _forecast_rows(pr15_report: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in pr15_report.get("forecasts") or []:
        if not isinstance(row, Mapping) or not row.get("forecast_id"):
            continue
        out[str(row["forecast_id"])] = dict(row)
    return out


def _snapshot_payload(graph: Mapping[str, Any], contracts: Mapping[str, Mapping[str, Any]], created_at: datetime) -> Dict[str, Any]:
    contract_index = {
        belief_id: {
            "epistemic_contract_id": contract["epistemic_contract_id"],
            "immutable_sha256": contract["immutable_sha256"],
            "source_definition_fingerprint": contract["source_definition_fingerprint"],
        }
        for belief_id, contract in sorted(contracts.items())
    }
    base = {
        "graph_contract_version": GRAPH_CONTRACT_VERSION,
        "structure_sha256": graph["structure_sha256"],
        "graph_structure_id": graph["graph_structure_id"],
        "created_at": iso_z(created_at),
        "contract_index": contract_index,
        "graph": deepcopy(graph),
        "contracts": {key: deepcopy(contracts[key]) for key in sorted(contracts)},
        "causal_proof": False,
        "decision_influence": False,
        "pnl_tuned": False,
    }
    base["graph_snapshot_id"] = _stable_id("epistemic-graph-snapshot", base)
    base["immutable_sha256"] = _sha(base)
    return base


def _ensure_graph_snapshot(state: MutableMapping[str, Any], graph: Mapping[str, Any], contracts: Mapping[str, Mapping[str, Any]], now: datetime) -> Tuple[str, bool]:
    snapshots = state.setdefault("graph_snapshots", {})
    for snapshot_id, snapshot in snapshots.items():
        if isinstance(snapshot, Mapping) and snapshot.get("structure_sha256") == graph.get("structure_sha256"):
            state["latest_graph_snapshot_id"] = snapshot_id
            return str(snapshot_id), False
    snapshot = _snapshot_payload(graph, contracts, now)
    snapshot_id = str(snapshot["graph_snapshot_id"])
    snapshots[snapshot_id] = snapshot
    state["latest_graph_snapshot_id"] = snapshot_id
    return snapshot_id, True


def _eligible_snapshot_for_forecast(state: Mapping[str, Any], belief_id: str, forecast_at: datetime) -> Optional[Mapping[str, Any]]:
    candidates = []
    for snapshot in (state.get("graph_snapshots") or {}).values():
        if not isinstance(snapshot, Mapping):
            continue
        try:
            created_at = parse_time(str(snapshot.get("created_at") or ""))
        except Exception:
            continue
        if created_at > forecast_at:
            continue
        if belief_id not in (snapshot.get("contract_index") or {}):
            continue
        candidates.append((created_at, snapshot))
    return max(candidates, key=lambda row: row[0])[1] if candidates else None


def _bind_forecast(forecast: Mapping[str, Any], snapshot: Mapping[str, Any], bound_at: datetime) -> Dict[str, Any]:
    forecast_id = str(forecast["forecast_id"])
    belief_id = str(forecast["belief_id"])
    contract_ref = dict((snapshot.get("contract_index") or {})[belief_id])
    payload = {
        "binding_contract_version": FORECAST_BINDING_CONTRACT_VERSION,
        "forecast_id": forecast_id,
        "belief_id": belief_id,
        "forecast_at": forecast.get("forecast_at"),
        "target_at": forecast.get("target_at"),
        "graph_snapshot_id": snapshot.get("graph_snapshot_id"),
        "graph_snapshot_sha256": snapshot.get("immutable_sha256"),
        "graph_structure_id": snapshot.get("graph_structure_id"),
        "epistemic_contract_id": contract_ref.get("epistemic_contract_id"),
        "epistemic_contract_sha256": contract_ref.get("immutable_sha256"),
        "source_definition_fingerprint": contract_ref.get("source_definition_fingerprint"),
        "prospective": True,
        "historical_backfill": False,
        "retroactive_causal_classification": False,
        "decision_influence": False,
        "promotion_authority": False,
    }
    binding = {
        **payload,
        "binding_id": _stable_id("forecast-epistemic-binding", payload),
        "bound_at": iso_z(bound_at),
    }
    binding["immutable_sha256"] = _sha(binding)
    return binding


def _assert_append_only(previous: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    for field in ("graph_snapshots", "forecast_bindings", "terminal_unbound_forecasts"):
        before = previous.get(field) or {}
        after = current.get(field) or {}
        for key, value in before.items():
            if key not in after or after[key] != value:
                raise RuntimeError(f"PR19 append-only mutation detected in {field}: {key}")


def _measurement_summary(contracts: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    by_relation: Dict[str, int] = {}
    gaps = []
    for belief_id, contract in sorted(contracts.items()):
        relation = str(contract.get("measurement_relation") or "UNKNOWN")
        by_relation[relation] = by_relation.get(relation, 0) + 1
        if relation.startswith("PARTIAL"):
            gaps.append({
                "belief_id": belief_id,
                "measurement_relation": relation,
                "limitations": list(contract.get("measurement_limitations") or []),
            })
    return {
        "by_relation": dict(sorted(by_relation.items())),
        "explicit_claim_outcome_gaps": gaps,
        "explicit_claim_outcome_gap_count": len(gaps),
        "rule": "Operational forecast correctness is not treated as proof of the full causal claim when the outcome is only a proxy.",
    }


def run(state_dir: Path, *, pr15_report_path: Path, as_of: Optional[datetime] = None) -> Dict[str, Any]:
    _assert_safety()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_z = iso_z(now)
    state_dir = Path(state_dir)
    state_path = state_dir / STATE_FILENAME
    report_path = state_dir / REPORT_FILENAME

    pr15_report = _read_json(pr15_report_path, {})
    _validate_pr15_report(pr15_report)
    entity_ids = _entity_belief_ids(pr15_report)
    contracts = build_epistemic_contracts(entity_ids)
    graph = build_graph(contracts)

    previous = _read_json(state_path, empty_state())
    state = deepcopy(previous)
    if str(state.get("schema_version") or "") != SCHEMA_VERSION:
        raise ValueError("PR19 state schema mismatch")
    if str(state.get("contract_version") or "") != CONTRACT_VERSION:
        raise ValueError("PR19 state contract mismatch")
    first_run = not bool(state.get("activated_at"))
    if first_run:
        state["activated_at"] = now_z

    graph_snapshot_id, new_graph_snapshot = _ensure_graph_snapshot(state, graph, contracts, now)
    forecasts = _forecast_rows(pr15_report)
    seen = set(str(x) for x in state.get("seen_pr15_forecast_ids") or [])
    pre_activation = set(str(x) for x in state.get("pre_activation_forecast_ids") or [])
    new_bindings = 0
    new_unbound = 0

    if first_run:
        for forecast_id in forecasts:
            seen.add(forecast_id)
            pre_activation.add(forecast_id)
    else:
        activated_at = parse_time(str(state["activated_at"]))
        for forecast_id, forecast in sorted(forecasts.items()):
            if forecast_id in seen:
                continue
            seen.add(forecast_id)
            belief_id = str(forecast.get("belief_id") or "")
            try:
                forecast_at = parse_time(str(forecast.get("forecast_at") or ""))
            except Exception:
                state.setdefault("terminal_unbound_forecasts", {})[forecast_id] = {
                    "forecast_id": forecast_id,
                    "belief_id": belief_id,
                    "status": "invalid_forecast_timestamp",
                    "recorded_at": now_z,
                    "terminal_no_retroactive_binding": True,
                }
                new_unbound += 1
                continue
            if forecast_at > now:
                state.setdefault("terminal_unbound_forecasts", {})[forecast_id] = {
                    "forecast_id": forecast_id,
                    "belief_id": belief_id,
                    "status": "future_dated_forecast_rejected",
                    "recorded_at": now_z,
                    "terminal_no_retroactive_binding": True,
                }
                new_unbound += 1
                continue
            if forecast_at < activated_at:
                state.setdefault("terminal_unbound_forecasts", {})[forecast_id] = {
                    "forecast_id": forecast_id,
                    "belief_id": belief_id,
                    "status": "forecast_precedes_pr19_activation",
                    "recorded_at": now_z,
                    "terminal_no_retroactive_binding": True,
                }
                new_unbound += 1
                continue
            snapshot = _eligible_snapshot_for_forecast(state, belief_id, forecast_at)
            if snapshot is None:
                state.setdefault("terminal_unbound_forecasts", {})[forecast_id] = {
                    "forecast_id": forecast_id,
                    "belief_id": belief_id,
                    "forecast_at": iso_z(forecast_at),
                    "status": "no_preexisting_epistemic_contract_snapshot",
                    "recorded_at": now_z,
                    "terminal_no_retroactive_binding": True,
                }
                new_unbound += 1
                continue
            state.setdefault("forecast_bindings", {})[forecast_id] = _bind_forecast(forecast, snapshot, now)
            new_bindings += 1

    state["seen_pr15_forecast_ids"] = sorted(seen)
    state["pre_activation_forecast_ids"] = sorted(pre_activation)
    state["last_run_at"] = now_z
    _assert_append_only(previous, state)
    _write_json(state_path, state)

    layer_counts: Dict[str, int] = {}
    falsifier_count = 0
    alternative_count = 0
    assumption_count = 0
    unknown_count = 0
    regime_count = 0
    for contract in contracts.values():
        layer = str(contract.get("layer") or "unknown")
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
        falsifier_count += len(contract.get("falsifiers") or [])
        alternative_count += len(contract.get("alternative_explanations") or [])
        assumption_count += len(contract.get("assumptions") or [])
        unknown_count += len(contract.get("unknowns") or [])
        regime_count += len(contract.get("regime_dependencies") or [])

    report = {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "graph_contract_version": GRAPH_CONTRACT_VERSION,
        "mode": MODE,
        "generated_at": now_z,
        "purpose": "Explicit epistemic contracts and a non-authorising causal-hypothesis graph for existing Belief Core layers.",
        "active_decision_influence": False,
        "epistemic_principles": {
            "popper": "Every Belief contract carries explicit prospective falsifiers; unfalsifiable narrative is not sufficient.",
            "bayes": "Evidence may update Belief probability only through the existing Belief Core; PR19 does not alter Bayesian state mechanics.",
            "lakatos": "Core claim and auxiliary assumptions are separated; failure of an auxiliary transmission assumption does not silently rewrite the claim.",
            "peirce": "Alternative explanations are recorded as open abductive hypotheses; PR19 does not automatically select a preferred explanation.",
            "kuhn": "Regime dependencies can make a transmission hypothesis inapplicable; regime change is not relabelled post hoc as forecast success or failure.",
        },
        "sample": {
            "activation_only_this_run": first_run,
            "canonical_beliefs_total": len(contracts),
            "beliefs_by_layer": dict(sorted(layer_counts.items())),
            "entity_beliefs_total": sum(1 for row in contracts.values() if row.get("layer") == "entity"),
            "assumptions_total": assumption_count,
            "falsifiers_total": falsifier_count,
            "alternative_explanations_total": alternative_count,
            "unknowns_total": unknown_count,
            "regime_dependencies_total": regime_count,
            "graph_nodes": len(graph.get("nodes") or []),
            "graph_edges": len(graph.get("edges") or []),
            "verified_causal_edges": 0,
            "pr15_forecasts_visible": len(forecasts),
            "pre_activation_forecasts_total": len(pre_activation),
            "forecast_bindings_total": len(state.get("forecast_bindings") or {}),
            "terminal_unbound_forecasts_total": len(state.get("terminal_unbound_forecasts") or {}),
            "new_bindings_this_run": new_bindings,
            "new_terminal_unbound_this_run": new_unbound,
        },
        "graph_runtime": {
            "latest_graph_snapshot_id": graph_snapshot_id,
            "new_graph_snapshot_this_run": new_graph_snapshot,
            "graph_snapshots_total": len(state.get("graph_snapshots") or {}),
            "graph_structure_id": graph.get("graph_structure_id"),
            "graph_structure_sha256": graph.get("structure_sha256"),
            "causal_status": CAUSAL_STATUS,
            "causal_proof": False,
            "correlation_to_causation_inference": False,
            "causal_dag_required": True,
        },
        "measurement_epistemics": _measurement_summary(contracts),
        "prospective_binding": {
            "binding_contract_version": FORECAST_BINDING_CONTRACT_VERSION,
            "first_run_existing_forecasts_cursor_only": True,
            "graph_snapshot_must_preexist_forecast": True,
            "epistemic_contract_must_preexist_forecast": True,
            "terminal_unbound_never_retroactively_bound": True,
            "historical_forecast_epistemic_backfill": False,
            "retroactive_causal_classification": False,
        },
        "research_boundary": {
            "graph_edges_are_hypotheses_not_causal_proof": True,
            "operational_correctness_is_not_automatically_causal_validation": True,
            "entity_fundamental_correctness_is_not_economic_value": True,
            "miv_score_enabled": False,
            "alpha_score_enabled": False,
            "engine_specific_trust_enabled": False,
            "automatic_abduction_enabled": False,
            "causal_edge_auto_discovery_enabled": False,
        },
        "promotion": {
            "status": "NOT_ELIGIBLE_FOR_PROMOTION_REVIEW",
            "eligible_for_promotion_review": False,
            "automatic_promotion": False,
            "effective_n_threshold_defined_here": False,
        },
        "capabilities": capabilities(),
        "safety_controls": safety_controls(),
        "contracts": {key: contracts[key] for key in sorted(contracts)},
        "graph": graph,
        "forecast_bindings": [state["forecast_bindings"][key] for key in sorted(state.get("forecast_bindings") or {})],
        "terminal_unbound_forecasts": [state["terminal_unbound_forecasts"][key] for key in sorted(state.get("terminal_unbound_forecasts") or {})],
    }
    _write_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Belief Epistemic Contract & Causal Graph v1")
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--pr15-report", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.state_dir, pr15_report_path=args.pr15_report)
    print(json.dumps({
        "status": report["promotion"]["status"],
        "canonical_beliefs_total": report["sample"]["canonical_beliefs_total"],
        "graph_nodes": report["sample"]["graph_nodes"],
        "graph_edges": report["sample"]["graph_edges"],
        "forecast_bindings_total": report["sample"]["forecast_bindings_total"],
        "causal_status": report["graph_runtime"]["causal_status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
