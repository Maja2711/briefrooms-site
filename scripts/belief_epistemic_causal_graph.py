#!/usr/bin/env python3
"""PR #19 — Epistemic Contract & Causal Belief Graph v1.

Research-shadow only. This module adds explicit epistemic metadata around the
existing Belief Core without changing probabilities, evidence weights, forecasts
or any engine decision.

Every contract separates:
claim -> assumptions -> transmission path -> falsifiers -> alternatives ->
unknowns -> regime dependencies.

Graph edges are hypotheses, never causal proof. Correlation never creates an
edge. Existing PR15 forecasts are cursor-only on activation; later forecasts may
bind only to an epistemic graph snapshot that already existed before forecast_at.
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
    from brace_broad_market_belief import BROAD_MARKET_BELIEFS, RATES, LIQUIDITY, MACRO, RISK
    from brace_sector_factor_belief import SECTOR_FACTOR_BELIEFS, SPEC_BY_ID
    from brace_entity_belief_state_forecast import (
        CONTRACT_VERSION as PR15_CONTRACT_VERSION,
        DIMENSION_CONFIG,
        FORECAST_HORIZON_HOURS,
    )
except ModuleNotFoundError:
    from scripts.brace_broad_market_belief import BROAD_MARKET_BELIEFS, RATES, LIQUIDITY, MACRO, RISK
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
    bad = [k for k, v in safety_controls().items() if v is not False]
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


def _item(prefix: str, key: str, **fields: Any) -> Dict[str, Any]:
    return {f"{prefix}_id": f"{prefix}.{key}", **fields}


def _canonical_definitions(entity_belief_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
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
        cfg = DIMENSION_CONFIG[dimension]
        out[belief_id] = {
            "belief_id": belief_id,
            "layer": "entity",
            "claim": str(cfg["claim"]).format(entity=entity_id.upper()),
            "outcome_rule": str(cfg["outcome_rule"]),
            "horizon_hours": float(FORECAST_HORIZON_HOURS),
            "domain": "entity_fundamentals",
            "entity_id": entity_id,
            "dimension": dimension,
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


MARKET_META: Mapping[str, Mapping[str, Any]] = {
    RATES: {
        "measurement_relation": "PARTIAL_PROXY",
        "measurement_limitations": ["TLT tests a duration/rates-pressure proxy, not the full risk-asset support claim."],
        "assumptions": [
            ("duration_proxy", "TLT remains informative about relevant rates pressure.", "measurement"),
            ("discount_rate_channel", "Easier rates pressure can transmit through discount rates before an offsetting shock dominates.", "transmission"),
        ],
        "mechanism": "mechanism.discount_rate_relief",
        "falsifier": "TLT closes below the frozen PR10 reference.",
        "alternatives": [
            ("recession_duration_bid", "Duration rallies because recession risk rises.", "Compare earnings expectations, credit and breadth."),
            ("term_premium", "Term-premium/supply effects dominate the duration move.", "Compare real-yield and curve decomposition when available."),
        ],
        "unknowns": [("response_magnitude", "Magnitude and lag of the equity response are unknown ex ante.", "high")],
        "regimes": [("recession", "Falling yields coincide with worsening growth expectations.", "Equity transmission can reverse sign.")],
    },
    LIQUIDITY: {
        "measurement_relation": "PARTIAL_PROXY",
        "measurement_limitations": ["HYG/LQD is a credit-risk-appetite proxy, not complete funding or order-book liquidity."],
        "assumptions": [
            ("credit_proxy", "HYG/LQD remains informative about broad credit risk appetite.", "measurement"),
            ("financing_channel", "Improving financing/risk capacity can support higher-beta assets.", "transmission"),
        ],
        "mechanism": "mechanism.financing_and_risk_capacity",
        "falsifier": "HYG/LQD closes below the frozen PR10 reference.",
        "alternatives": [
            ("credit_beta", "Credit beta/duration composition explains the ratio move.", "Compare spreads and funding evidence."),
            ("technical_flows", "ETF technical flows create a temporary ratio move.", "Compare persistence and non-ETF evidence."),
        ],
        "unknowns": [("liquidity_scope", "The proxy does not observe every liquidity channel.", "high")],
        "regimes": [("funding_stress", "Funding stress emerges outside the proxy.", "Proxy can remain benign while true liquidity deteriorates.")],
    },
    MACRO: {
        "measurement_relation": "PARTIAL_PROXY_PLUS_PRIMARY_INPUTS",
        "measurement_limitations": ["The cross-asset majority outcome does not directly verify the full macro causal story."],
        "assumptions": [
            ("macro_inputs", "Selected BLS and cross-asset inputs represent the relevant near-term macro backdrop.", "measurement"),
            ("earnings_channel", "Supportive macro conditions can transmit through demand and earnings expectations.", "transmission"),
        ],
        "mechanism": "mechanism.earnings_expectation_support",
        "falsifier": "The existing PR10 macro majority outcome resolves adverse.",
        "alternatives": [
            ("market_leads_macro", "Markets move ahead of reported macro rather than because reported macro caused the move.", "Compare timestamp ordering and revisions."),
            ("policy_offset", "Policy expectations dominate the macro impulse.", "Compare rates and liquidity channels."),
        ],
        "unknowns": [("macro_mapping", "Macro-to-sector transmission is regime dependent.", "high")],
        "regimes": [("stagflation", "Growth and inflation impulses conflict.", "A single supportive/adverse label can become insufficient.")],
    },
    RISK: {
        "measurement_relation": "PARTIAL_CROSS_ASSET_PROXY",
        "measurement_limitations": ["Cross-asset majority captures a non-defensive state, not a verified structural regime."],
        "assumptions": [
            ("risk_proxy", "SPY, breadth, credit, volatility and USD jointly proxy near-term risk regime.", "measurement"),
            ("risk_appetite", "A non-defensive regime can transmit through risk appetite and positioning.", "transmission"),
        ],
        "mechanism": "mechanism.risk_appetite",
        "falsifier": "The existing PR10 risk-regime majority outcome resolves adverse.",
        "alternatives": [
            ("index_concentration", "Index concentration masks weak underlying risk appetite.", "Compare equal-weight breadth."),
            ("volatility_supply", "Volatility supply suppresses VIX without genuine fundamental improvement.", "Compare credit and breadth confirmation."),
        ],
        "unknowns": [("crowding", "Supportive risk state can coexist with fragile crowding.", "high")],
        "regimes": [("event_risk", "A discrete event shock dominates normal transmission.", "Regime can switch faster than the normal horizon.")],
    },
}

ENTITY_META: Mapping[str, Mapping[str, Any]] = {
    "revenue_durability": {
        "mechanism": "demand_and_monetization_persistence",
        "assumptions": [("comparability", "Next comparable revenue observation is economically comparable.", "measurement"), ("structure", "M&A/disposals/accounting changes do not dominate the comparison.", "auxiliary")],
        "alternatives": [("fx", "FX translation explains reported change.", "Compare constant-currency evidence when available."), ("ma", "Portfolio changes create apparent durability.", "Inspect acquisition/disposal disclosures.")],
        "unknowns": [("quality", "Pricing/volume and revenue-quality mix are incompletely observed.", "high")],
        "regimes": [("demand_cycle", "Sector demand cycle changes materially.", "Past durability can lose predictive relevance.")],
    },
    "earnings_momentum": {
        "mechanism": "earnings_conversion_persistence",
        "assumptions": [("comparability", "EPS/net-income observations remain comparable.", "measurement"), ("oneoffs", "One-off items do not dominate interpreted earnings trajectory.", "auxiliary")],
        "alternatives": [("buybacks", "Share-count changes explain EPS momentum.", "Compare diluted shares and net income."), ("oneoffs", "Tax/impairment items dominate earnings.", "Inspect unusual-item disclosures.")],
        "unknowns": [("quality", "Persistence of accounting earnings into cash earnings is incomplete.", "high")],
        "regimes": [("earnings_cycle", "Earnings cycle turns rapidly.", "Current momentum can reverse before verification.")],
    },
    "margin_trajectory": {
        "mechanism": "operating_leverage_and_mix",
        "assumptions": [("comparability", "Operating-margin definitions remain comparable.", "measurement"), ("mix", "Major mix/classification changes are identified.", "auxiliary")],
        "alternatives": [("mix", "Product/geographic mix explains the margin change.", "Inspect segment mix."), ("temporary_cost", "Temporary cost timing creates a non-persistent move.", "Inspect subsequent normalization.")],
        "unknowns": [("cost_curve", "Future cost elasticity to revenue is not directly observed.", "high")],
        "regimes": [("input_cost_shock", "Input/wage/logistics costs shift abruptly.", "Past margin trajectory can become non-transferable.")],
    },
    "net_interest_income_durability": {
        "mechanism": "bank_asset_liability_spread_and_volume",
        "assumptions": [("comparability", "NII reporting remains comparable and recurring.", "measurement"), ("balance_sheet", "No unmodelled restructuring dominates NII.", "auxiliary")],
        "alternatives": [("volume", "Balance-sheet volume rather than spread durability explains NII.", "Separate balances from yield/cost effects."), ("hedges", "Hedging/accounting dominates NII movement.", "Inspect ALM disclosures.")],
        "unknowns": [("deposit_beta", "Future deposit beta and repricing speed are uncertain.", "high")],
        "regimes": [("rate_regime", "Policy/rates path changes materially.", "Asset-liability repricing assumptions can fail."), ("deposit_stress", "Deposit competition/outflows accelerate.", "NII can deteriorate independently of asset yields.")],
    },
}


def _expand_rows(prefix: str, rows: Sequence[Tuple[str, str, str]], field2: str, field3: str) -> List[Dict[str, Any]]:
    return [_item(prefix, key, **{field2: a, field3: b}) for key, a, b in rows]


def _sector_factor_meta(definition: Mapping[str, Any]) -> Dict[str, Any]:
    belief_id = str(definition["belief_id"])
    spec = SPEC_BY_ID[belief_id]
    n, d = str(spec["numerator"]), str(spec["denominator"])
    return {
        "measurement_relation": "OPERATIONALLY_DIRECT_RELATIVE_PRICE_PROXY",
        "measurement_limitations": [f"{n}/{d} verifies relative ETF-price leadership, not complete category fundamentals."],
        "assumptions": [
            ("proxy", f"{n}/{d} remains a reasonable liquid proxy for {spec['label']} leadership.", "measurement"),
            ("persistence", "Relative leadership persists enough to matter over the next-session horizon.", "auxiliary"),
        ],
        "mechanism": "mechanism.relative_demand_and_fundamental_support",
        "falsifier": f"{n}/{d} closes below its frozen PR11 reference.",
        "alternatives": [("flows", "ETF/index flows or concentration create the relative move.", "Compare breadth and constituent dispersion."), ("beta", "Transient beta/regime effects imitate leadership.", "Compare persistence across independent states.")],
        "unknowns": [("attribution", "Current contract cannot fully attribute leadership to fundamentals, positioning or flows.", "high")],
        "regimes": [("stress", "Broad market stress overwhelms category-specific information.", "Leadership can become a defensive/beta artifact.")],
    }


def build_epistemic_contracts(entity_belief_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    definitions = _canonical_definitions(entity_belief_ids)
    contracts: Dict[str, Dict[str, Any]] = {}
    for belief_id, definition in sorted(definitions.items()):
        if belief_id in MARKET_META:
            meta = dict(MARKET_META[belief_id])
            evaluator = "brace_broad_market_belief.evaluate_outcome"
            direct_strength = "DIRECT_FOR_OPERATIONAL_PROXY_ONLY"
        elif belief_id in SPEC_BY_ID:
            meta = _sector_factor_meta(definition)
            evaluator = "brace_sector_factor_belief.evaluate_outcome"
            direct_strength = "DIRECT_FOR_OPERATIONAL_RULE"
        else:
            parts = _belief_parts(belief_id)
            if parts is None:
                raise ValueError(f"No reviewed PR19 metadata for {belief_id}")
            entity_id, dimension = parts
            dim = ENTITY_META[dimension]
            meta = {
                "measurement_relation": "DIRECT_TO_REVIEWED_PR14_INTERPRETATION_CONTRACT",
                "measurement_limitations": ["Forecast verifies the next comparable PR14 interpretation, not stock return or engine value."],
                "assumptions": dim["assumptions"],
                "mechanism": f"mechanism.entity.{entity_id}.{dim['mechanism']}",
                "falsifier": "The next comparable PR14 interpretation inside the frozen horizon resolves oppose.",
                "alternatives": dim["alternatives"],
                "unknowns": dim["unknowns"],
                "regimes": dim["regimes"],
            }
            evaluator = "brace_entity_belief_state_forecast._resolve_due_forecasts"
            direct_strength = "DIRECT_FOR_PR15_FORECAST_TARGET"
        assumptions = _expand_rows("assumption", meta["assumptions"], "statement", "role")
        for row in assumptions:
            row["status"] = "EXPLICIT_UNVERIFIED_ASSUMPTION"
        alternatives = _expand_rows("alternative", meta["alternatives"], "explanation", "discriminating_evidence")
        for row in alternatives:
            row["status"] = "OPEN_ALTERNATIVE"
        unknowns = _expand_rows("unknown", meta["unknowns"], "statement", "materiality")
        for row in unknowns:
            row["status"] = "UNRESOLVED"
        regimes = _expand_rows("regime", meta["regimes"], "condition", "failure_mode")
        for row in regimes:
            row["status"] = "CONDITIONAL_APPLICABILITY"
        contract = {
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
            "causal_assumptions": assumptions,
            "transmission_path": [{
                "step": 1,
                "from": belief_id,
                "to": meta["mechanism"],
                "mechanism": str(meta["mechanism"]).split(".")[-1],
                "status": CAUSAL_STATUS,
            }],
            "falsifiers": [{
                "falsifier_id": f"falsifier.{belief_id}.operational",
                "target": "claim_or_operational_proxy",
                "statement": meta["falsifier"],
                "machine_testable": True,
                "existing_evaluator": evaluator,
                "falsification_strength": direct_strength,
                "prospective_only": True,
            }],
            "alternative_explanations": alternatives,
            "unknowns": unknowns,
            "regime_dependencies": regimes,
            "measurement_relation": meta["measurement_relation"],
            "measurement_limitations": list(meta["measurement_limitations"]),
            "scope_conditions": [
                "Only prospectively available source-contract information is admissible.",
                "Failure of an auxiliary assumption does not silently rewrite the core claim.",
                "Regime inapplicability is distinct from retrospective forecast relabelling.",
            ],
            "causal_status": CAUSAL_STATUS,
            "causal_proof": False,
            "economic_transmission_status": "UNTESTED_BY_PR19",
            "pnl_tuned": False,
            "decision_influence": False,
            "promotion_authority": False,
        }
        contract["epistemic_contract_id"] = _stable_id("epistemic", contract)
        contract["immutable_sha256"] = _sha(contract)
        contracts[belief_id] = contract
    validate_contracts(contracts, definitions)
    return contracts


def validate_contracts(contracts: Mapping[str, Mapping[str, Any]], definitions: Mapping[str, Mapping[str, Any]]) -> None:
    required = ("causal_assumptions", "transmission_path", "falsifiers", "alternative_explanations", "unknowns", "regime_dependencies", "measurement_limitations")
    for belief_id, contract in contracts.items():
        if contract.get("claim") != definitions[belief_id].get("claim"):
            raise ValueError(f"Canonical claim drift: {belief_id}")
        if contract.get("operational_outcome_rule") != definitions[belief_id].get("outcome_rule"):
            raise ValueError(f"Outcome-rule drift: {belief_id}")
        for key in required:
            if not contract.get(key):
                raise ValueError(f"Missing epistemic field {key}: {belief_id}")
        if not any(row.get("machine_testable") is True for row in contract["falsifiers"]):
            raise ValueError(f"Missing machine-testable falsifier: {belief_id}")
        if contract.get("causal_proof") is not False or contract.get("decision_influence") is not False:
            raise ValueError(f"PR19 authority boundary violated: {belief_id}")


def _edge(source: str, target: str, mechanism: str) -> Dict[str, Any]:
    row = {
        "source": source,
        "target": target,
        "edge_type": "hypothesized_causal_channel",
        "mechanism": mechanism,
        "causal_status": CAUSAL_STATUS,
        "causal_proof": False,
        "pnl_tuned": False,
        "decision_influence": False,
    }
    row["edge_id"] = _stable_id("edge", row)
    return row


def _assert_dag(node_ids: Iterable[str], edges: Sequence[Mapping[str, Any]]) -> None:
    nodes = set(node_ids)
    adj = {n: [] for n in nodes}
    indegree = {n: 0 for n in nodes}
    for edge in edges:
        source, target = str(edge["source"]), str(edge["target"])
        if source not in nodes or target not in nodes:
            raise ValueError(f"Missing graph node for {source}->{target}")
        adj[source].append(target)
        indegree[target] += 1
    queue = [n for n, d in indegree.items() if d == 0]
    seen = 0
    while queue:
        node = queue.pop()
        seen += 1
        for target in adj[node]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if seen != len(nodes):
        raise ValueError("PR19 causal hypothesis graph must be acyclic")


def build_graph(contracts: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    nodes: Dict[str, Dict[str, Any]] = {
        belief_id: {"node_id": belief_id, "node_type": "belief", "layer": contract["layer"], "epistemic_contract_id": contract["epistemic_contract_id"]}
        for belief_id, contract in contracts.items()
    }
    edges: List[Dict[str, Any]] = []
    for belief_id, contract in contracts.items():
        mechanism = str(contract["transmission_path"][0]["to"])
        nodes.setdefault(mechanism, {"node_id": mechanism, "node_type": "mechanism", "causal_status": CAUSAL_STATUS})
        edges.append(_edge(belief_id, mechanism, str(contract["transmission_path"][0]["mechanism"])))

    channels = {
        "mechanism.discount_rate_relief": ("factor.growth.leadership", "sector.technology.leadership", "sector.semiconductors.leadership", "sector.consumer_discretionary.leadership"),
        "mechanism.financing_and_risk_capacity": ("factor.small_cap.leadership", "factor.growth.leadership", "factor.momentum.leadership"),
        "mechanism.earnings_expectation_support": ("sector.financials.leadership", "sector.consumer_discretionary.leadership", "factor.quality.leadership", "factor.small_cap.leadership"),
        "mechanism.risk_appetite": ("factor.momentum.leadership", "factor.growth.leadership", "factor.small_cap.leadership"),
    }
    for mechanism, targets in channels.items():
        if mechanism not in nodes:
            continue
        for target in targets:
            if target in nodes:
                edges.append(_edge(mechanism, target, mechanism.split(".")[-1]))
    edges.sort(key=lambda x: (x["source"], x["target"], x["edge_id"]))
    _assert_dag(nodes, edges)
    graph = {
        "graph_contract_version": GRAPH_CONTRACT_VERSION,
        "causal_status": CAUSAL_STATUS,
        "causal_proof": False,
        "correlation_to_causation_inference": False,
        "nodes": [nodes[k] for k in sorted(nodes)],
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
        raise ValueError("PR19 requires reviewed PR15 contract")
    if str(report.get("mode") or "") != MODE or report.get("active_decision_influence") is not False:
        raise ValueError("PR19 accepts PR15 research_shadow without active influence only")


def _entity_belief_ids(report: Mapping[str, Any]) -> Tuple[str, ...]:
    values = []
    for field in ("belief_states", "forecasts"):
        for row in report.get(field) or []:
            if isinstance(row, Mapping):
                belief_id = str(row.get("belief_id") or "")
                if _belief_parts(belief_id):
                    values.append(belief_id)
    return tuple(sorted(set(values)))


def _forecasts(report: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(r["forecast_id"]): dict(r) for r in report.get("forecasts") or [] if isinstance(r, Mapping) and r.get("forecast_id")}


def _snapshot(graph: Mapping[str, Any], contracts: Mapping[str, Mapping[str, Any]], now: datetime) -> Dict[str, Any]:
    index = {k: {"epistemic_contract_id": v["epistemic_contract_id"], "immutable_sha256": v["immutable_sha256"], "source_definition_fingerprint": v["source_definition_fingerprint"]} for k, v in sorted(contracts.items())}
    row = {
        "graph_contract_version": GRAPH_CONTRACT_VERSION,
        "created_at": iso_z(now),
        "graph_structure_id": graph["graph_structure_id"],
        "structure_sha256": graph["structure_sha256"],
        "contract_index": index,
        "contracts": {k: deepcopy(contracts[k]) for k in sorted(contracts)},
        "graph": deepcopy(graph),
        "causal_proof": False,
        "decision_influence": False,
    }
    row["graph_snapshot_id"] = _stable_id("epistemic-graph-snapshot", row)
    row["immutable_sha256"] = _sha(row)
    return row


def _ensure_snapshot(state: MutableMapping[str, Any], graph: Mapping[str, Any], contracts: Mapping[str, Mapping[str, Any]], now: datetime) -> Tuple[str, bool]:
    snapshots = state.setdefault("graph_snapshots", {})
    for snapshot_id, row in snapshots.items():
        if isinstance(row, Mapping) and row.get("structure_sha256") == graph.get("structure_sha256"):
            state["latest_graph_snapshot_id"] = snapshot_id
            return str(snapshot_id), False
    row = _snapshot(graph, contracts, now)
    snapshot_id = str(row["graph_snapshot_id"])
    snapshots[snapshot_id] = row
    state["latest_graph_snapshot_id"] = snapshot_id
    return snapshot_id, True


def _eligible_snapshot(state: Mapping[str, Any], belief_id: str, forecast_at: datetime) -> Optional[Mapping[str, Any]]:
    rows = []
    for snapshot in (state.get("graph_snapshots") or {}).values():
        if not isinstance(snapshot, Mapping) or belief_id not in (snapshot.get("contract_index") or {}):
            continue
        try:
            created = parse_time(str(snapshot.get("created_at") or ""))
        except Exception:
            continue
        if created <= forecast_at:
            rows.append((created, snapshot))
    return max(rows, key=lambda x: x[0])[1] if rows else None


def _binding(forecast: Mapping[str, Any], snapshot: Mapping[str, Any], now: datetime) -> Dict[str, Any]:
    belief_id = str(forecast["belief_id"])
    ref = dict((snapshot.get("contract_index") or {})[belief_id])
    base = {
        "binding_contract_version": FORECAST_BINDING_CONTRACT_VERSION,
        "forecast_id": str(forecast["forecast_id"]),
        "belief_id": belief_id,
        "forecast_at": forecast.get("forecast_at"),
        "target_at": forecast.get("target_at"),
        "graph_snapshot_id": snapshot.get("graph_snapshot_id"),
        "graph_snapshot_sha256": snapshot.get("immutable_sha256"),
        "graph_structure_id": snapshot.get("graph_structure_id"),
        "epistemic_contract_id": ref.get("epistemic_contract_id"),
        "epistemic_contract_sha256": ref.get("immutable_sha256"),
        "source_definition_fingerprint": ref.get("source_definition_fingerprint"),
        "prospective": True,
        "historical_backfill": False,
        "retroactive_causal_classification": False,
        "decision_influence": False,
        "promotion_authority": False,
    }
    row = {**base, "binding_id": _stable_id("forecast-epistemic-binding", base), "bound_at": iso_z(now)}
    row["immutable_sha256"] = _sha(row)
    return row


def _assert_append_only(previous: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    for field in ("graph_snapshots", "forecast_bindings", "terminal_unbound_forecasts"):
        for key, value in (previous.get(field) or {}).items():
            if key not in (current.get(field) or {}) or current[field][key] != value:
                raise RuntimeError(f"PR19 append-only mutation detected: {field}:{key}")


def run(state_dir: Path, *, pr15_report_path: Path, as_of: Optional[datetime] = None) -> Dict[str, Any]:
    _assert_safety()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_z = iso_z(now)
    state_dir = Path(state_dir)
    state_path, report_path = state_dir / STATE_FILENAME, state_dir / REPORT_FILENAME
    pr15 = _read_json(pr15_report_path, {})
    _validate_pr15_report(pr15)
    contracts = build_epistemic_contracts(_entity_belief_ids(pr15))
    graph = build_graph(contracts)

    previous = _read_json(state_path, empty_state())
    state = deepcopy(previous)
    if state.get("schema_version") != SCHEMA_VERSION or state.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("PR19 state schema/contract mismatch")
    first_run = not bool(state.get("activated_at"))
    if first_run:
        state["activated_at"] = now_z
    snapshot_id, new_snapshot = _ensure_snapshot(state, graph, contracts, now)
    forecasts = _forecasts(pr15)
    seen = set(str(x) for x in state.get("seen_pr15_forecast_ids") or [])
    pre = set(str(x) for x in state.get("pre_activation_forecast_ids") or [])
    new_bindings = new_unbound = 0

    if first_run:
        for forecast_id in forecasts:
            seen.add(forecast_id)
            pre.add(forecast_id)
    else:
        activated = parse_time(str(state["activated_at"]))
        for forecast_id, forecast in sorted(forecasts.items()):
            if forecast_id in seen:
                continue
            seen.add(forecast_id)
            belief_id = str(forecast.get("belief_id") or "")
            try:
                forecast_at = parse_time(str(forecast.get("forecast_at") or ""))
            except Exception:
                status = "invalid_forecast_timestamp"
                forecast_at = None
            else:
                status = ""
                if forecast_at > now:
                    status = "future_dated_forecast_rejected"
                elif forecast_at < activated:
                    status = "forecast_precedes_pr19_activation"
            snapshot = None if status else _eligible_snapshot(state, belief_id, forecast_at)
            if not status and snapshot is None:
                status = "no_preexisting_epistemic_contract_snapshot"
            if status:
                state.setdefault("terminal_unbound_forecasts", {})[forecast_id] = {
                    "forecast_id": forecast_id,
                    "belief_id": belief_id,
                    "forecast_at": iso_z(forecast_at) if forecast_at else forecast.get("forecast_at"),
                    "status": status,
                    "recorded_at": now_z,
                    "terminal_no_retroactive_binding": True,
                }
                new_unbound += 1
            else:
                state.setdefault("forecast_bindings", {})[forecast_id] = _binding(forecast, snapshot, now)
                new_bindings += 1

    state["seen_pr15_forecast_ids"] = sorted(seen)
    state["pre_activation_forecast_ids"] = sorted(pre)
    state["last_run_at"] = now_z
    _assert_append_only(previous, state)
    _write_json(state_path, state)

    layer_counts: Dict[str, int] = {}
    for contract in contracts.values():
        layer = str(contract["layer"])
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
    gap_rows = [{"belief_id": b, "measurement_relation": c["measurement_relation"], "limitations": c["measurement_limitations"]} for b, c in sorted(contracts.items()) if str(c["measurement_relation"]).startswith("PARTIAL")]

    report = {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "graph_contract_version": GRAPH_CONTRACT_VERSION,
        "mode": MODE,
        "generated_at": now_z,
        "purpose": "Explicit epistemic contracts and non-authorising causal hypotheses around existing Belief Core layers.",
        "active_decision_influence": False,
        "epistemic_principles": {
            "popper": "Beliefs require explicit prospective falsifiers.",
            "bayes": "Probability updates remain exclusively in existing Belief Core mechanics.",
            "lakatos": "Core claims and auxiliary assumptions are separated.",
            "peirce": "Alternative explanations remain explicit open hypotheses; no automatic winner is selected.",
            "kuhn": "Regime dependency can make a transmission hypothesis inapplicable without hindsight relabelling.",
        },
        "sample": {
            "activation_only_this_run": first_run,
            "canonical_beliefs_total": len(contracts),
            "beliefs_by_layer": dict(sorted(layer_counts.items())),
            "entity_beliefs_total": sum(1 for c in contracts.values() if c["layer"] == "entity"),
            "graph_nodes": len(graph["nodes"]),
            "graph_edges": len(graph["edges"]),
            "verified_causal_edges": 0,
            "pr15_forecasts_visible": len(forecasts),
            "pre_activation_forecasts_total": len(pre),
            "forecast_bindings_total": len(state.get("forecast_bindings") or {}),
            "terminal_unbound_forecasts_total": len(state.get("terminal_unbound_forecasts") or {}),
            "new_bindings_this_run": new_bindings,
            "new_terminal_unbound_this_run": new_unbound,
        },
        "graph_runtime": {
            "latest_graph_snapshot_id": snapshot_id,
            "new_graph_snapshot_this_run": new_snapshot,
            "graph_snapshots_total": len(state.get("graph_snapshots") or {}),
            "graph_structure_id": graph["graph_structure_id"],
            "graph_structure_sha256": graph["structure_sha256"],
            "causal_status": CAUSAL_STATUS,
            "causal_proof": False,
            "correlation_to_causation_inference": False,
            "causal_dag_required": True,
        },
        "measurement_epistemics": {
            "explicit_claim_outcome_gap_count": len(gap_rows),
            "explicit_claim_outcome_gaps": gap_rows,
            "rule": "Operational correctness is not proof of the full causal claim when outcome semantics are only a proxy.",
        },
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
        "contracts": {k: contracts[k] for k in sorted(contracts)},
        "graph": graph,
        "forecast_bindings": [state["forecast_bindings"][k] for k in sorted(state.get("forecast_bindings") or {})],
        "terminal_unbound_forecasts": [state["terminal_unbound_forecasts"][k] for k in sorted(state.get("terminal_unbound_forecasts") or {})],
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
        "beliefs": report["sample"]["canonical_beliefs_total"],
        "graph_nodes": report["sample"]["graph_nodes"],
        "graph_edges": report["sample"]["graph_edges"],
        "forecast_bindings": report["sample"]["forecast_bindings_total"],
        "causal_status": report["graph_runtime"]["causal_status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
