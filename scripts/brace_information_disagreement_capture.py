#!/usr/bin/env python3
"""PR #18 — BRACE Information Set & Disagreement Capture.

Prospective research instrumentation only.

PR17 answers whether a hypothetical Entity Belief modifier changed a BRACE
counterfactual. PR18 freezes the *information available to BRACE* for those
prospective PR17 pairs so later research can ask the harder question:

    what did the Belief add beyond what the engine already knew?

Hard anti-hindsight rule
------------------------
A new PR17 pair can be captured only while the exact BRACE ``analysis.json``
and ``pending_decisions.json`` snapshots used by PR17 are still available.
Their canonical SHA-256 hashes must equal the hashes frozen in the PR17 pair.
If not, PR18 records a terminal ``source_snapshot_not_available`` result and
never fills it later from newer repository state.

PR18 does NOT compute a Marginal Information Value score. It records immutable
engine/belief/world-state information and descriptive disagreement topology.
Redundancy, orthogonality and MIV remain ``NOT_YET_ESTIMABLE`` until prospective
samples mature and a separately reviewed research contract is implemented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

MODE = "research_shadow"
SCHEMA_VERSION = "brace-information-disagreement-capture-v1"
REPORT_VERSION = "brace-information-disagreement-capture-report-v1"
CONTRACT_VERSION = "brace-information-disagreement-capture-contract-v1"
ENGINE_INFORMATION_CONTRACT_VERSION = "brace-engine-information-set-v1"
BELIEF_INFORMATION_CONTRACT_VERSION = "entity-belief-information-set-v1"
DISAGREEMENT_CONTRACT_VERSION = "belief-engine-disagreement-topology-v1"

PR17_CONTRACT_VERSION = "brace-entity-belief-shadow-bridge-contract-v1"
PR16_1_CONTRACT_VERSION = "investment-semantics-world-state-contract-v1"
WORLD_STATE_CONTRACT_VERSION = "investment-world-state-v1"

STATE_FILENAME = "BRACE_INFORMATION_DISAGREEMENT_STATE.json"
REPORT_FILENAME = "BRACE_INFORMATION_DISAGREEMENT_REPORT.json"

# Fixed descriptive dead-band. It is not PnL tuned and is not an alpha threshold.
STANCE_EPSILON = 0.05

BROAD_MARKET_IDS: Tuple[str, ...] = (
    "market.rates.supportive",
    "market.liquidity.supportive",
    "market.macro_regime.supportive",
    "market.risk_regime.supportive",
)
FACTOR_IDS: Tuple[str, ...] = (
    "factor.growth.leadership",
    "factor.quality.leadership",
    "factor.momentum.leadership",
    "factor.small_cap.leadership",
)
SECTOR_ALIASES: Mapping[str, str] = {
    "technology": "sector.technology.leadership",
    "information technology": "sector.technology.leadership",
    "financials": "sector.financials.leadership",
    "financial services": "sector.financials.leadership",
    "health care": "sector.health_care.leadership",
    "healthcare": "sector.health_care.leadership",
    "consumer discretionary": "sector.consumer_discretionary.leadership",
    "consumer cyclical": "sector.consumer_discretionary.leadership",
    "consumer staples": "sector.consumer_staples.leadership",
    "consumer defensive": "sector.consumer_staples.leadership",
    "communication services": "sector.communication_services.leadership",
    "semiconductors": "sector.semiconductors.leadership",
}

FEATURE_SCORE_FIELDS: Tuple[str, ...] = (
    "quality_score",
    "valuation_score",
    "momentum_score",
    "risk_score",
    "diversification_score",
    "thesis_score",
    "data_quality_score",
    "final_score",
    "risk_adjusted_score",
)
EXPECTATION_FIELDS: Tuple[str, ...] = (
    "expected_return_base",
    "expected_return_bull",
    "expected_return_bear",
    "expected_drawdown",
    "probability_of_reaching_target",
    "target_shortfall",
    "required_risk_to_target",
)
RAW_FEATURE_BLOCKS: Tuple[str, ...] = (
    "momentum",
    "risk",
    "quality",
    "valuation",
    "liquidity",
)


def safety_controls() -> Dict[str, bool]:
    return {
        "active_decision_influence": False,
        "engine_score_writeback": False,
        "belief_probability_writeback": False,
        "candidate_ranking_change": False,
        "optimizer_change": False,
        "target_exposure_change": False,
        "sizing_change": False,
        "veto": False,
        "forced_exit": False,
        "direction_reversal": False,
        "trade_execution": False,
        "policy_output": False,
        "automatic_tuning": False,
        "historical_information_set_backfill": False,
        "retroactive_source_reconstruction": False,
        "miv_score_output": False,
        "automatic_promotion": False,
    }


def capabilities() -> Dict[str, bool]:
    return {
        "prospective_engine_information_capture_enabled": True,
        "prospective_belief_information_capture_enabled": True,
        "world_state_context_capture_enabled": True,
        "disagreement_topology_capture_enabled": True,
        "append_only_capture_ledger_enabled": True,
        "pr17_outcome_join_for_descriptive_diagnostics_enabled": True,
        "source_snapshot_sha_parity_required": True,
        "marginal_information_value_score_enabled": False,
        "redundancy_estimation_enabled": False,
        "orthogonality_estimation_enabled": False,
        "causal_belief_graph_enabled": False,
        "engine_specific_trust_enabled": False,
        "promotion_gate_enabled": False,
    }


def _assert_safety() -> None:
    bad = [key for key, value in safety_controls().items() if value is not False]
    if bad:
        raise RuntimeError("PR18 zero-authority invariant violated: " + ",".join(bad))


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


def _float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _copy_mapping(value: Any) -> Dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def empty_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "contract_version": CONTRACT_VERSION,
        "activated_at": None,
        "last_run_at": None,
        "seen_pr17_pair_set_ids": [],
        "pre_activation_pr17_pair_set_ids": [],
        "captures": {},
        "terminal_uncaptured": {},
    }


def _validate_inputs(
    pr17_state: Mapping[str, Any],
    pr17_report: Mapping[str, Any],
    world_state: Mapping[str, Any],
    world_report: Mapping[str, Any],
) -> None:
    if str(pr17_state.get("contract_version") or "") != PR17_CONTRACT_VERSION:
        raise ValueError("PR18 requires reviewed PR17 state contract")
    if str(pr17_report.get("contract_version") or "") != PR17_CONTRACT_VERSION:
        raise ValueError("PR18 requires reviewed PR17 report contract")
    if str(pr17_report.get("mode") or "") != MODE:
        raise ValueError("PR18 requires PR17 research_shadow mode")
    if pr17_report.get("active_decision_influence") is not False:
        raise ValueError("PR18 refuses PR17 input with active decision influence")
    if str(world_state.get("contract_version") or "") != PR16_1_CONTRACT_VERSION:
        raise ValueError("PR18 requires reviewed PR16.1 state contract")
    if str(world_report.get("contract_version") or "") != PR16_1_CONTRACT_VERSION:
        raise ValueError("PR18 requires reviewed PR16.1 report contract")
    if world_report.get("active_decision_influence") is not False:
        raise ValueError("PR18 refuses World State input with active decision influence")


def _positions(analysis: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(row.get("instrument_id") or row.get("id") or "").lower(): dict(row)
        for row in (analysis.get("positions") or [])
        if isinstance(row, Mapping) and (row.get("instrument_id") or row.get("id"))
    }


def _recommendations(pending: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(row.get("instrument") or "").lower(): dict(row)
        for row in (pending.get("recommendations") or [])
        if isinstance(row, Mapping) and row.get("instrument")
    }


def _world_snapshots(world_state: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {
        str(row.get("world_state_id")): row
        for row in (world_state.get("snapshots") or [])
        if isinstance(row, Mapping) and row.get("world_state_id")
    }


def _semantic_value(row: Any) -> Optional[float]:
    if not isinstance(row, Mapping):
        return None
    value = row.get("value")
    return _float(value)


def _belief_probability(snapshot: Mapping[str, Any], belief_id: str) -> Optional[float]:
    context = snapshot.get("belief_context") or {}
    for layer in ("broad_market", "sector_factor"):
        rows = context.get(layer) or {}
        if isinstance(rows, Mapping) and belief_id in rows:
            return _semantic_value((rows.get(belief_id) or {}).get("probability"))
    return None


def _belief_confidence(snapshot: Mapping[str, Any], belief_id: str) -> Optional[float]:
    context = snapshot.get("belief_context") or {}
    for layer in ("broad_market", "sector_factor"):
        rows = context.get(layer) or {}
        if isinstance(rows, Mapping) and belief_id in rows:
            return _semantic_value((rows.get(belief_id) or {}).get("confidence"))
    return None


def _signed_probability(probability: Optional[float]) -> Optional[float]:
    if probability is None:
        return None
    return max(-1.0, min(1.0, 2.0 * float(probability) - 1.0))


def _mean_available(values: Iterable[Optional[float]]) -> Optional[float]:
    rows = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return sum(rows) / len(rows) if rows else None


def _stance(value: Optional[float], *, positive: str = "POSITIVE", negative: str = "NEGATIVE") -> str:
    if value is None:
        return "UNAVAILABLE"
    if value > STANCE_EPSILON:
        return positive
    if value < -STANCE_EPSILON:
        return negative
    return "NEUTRAL"


def engine_stance(
    *,
    final_score: Optional[float],
    risk_score: Optional[float],
    data_quality_confidence: Optional[float],
) -> str:
    if final_score is None or risk_score is None or data_quality_confidence is None:
        return "UNAVAILABLE"
    if data_quality_confidence < 0.5:
        return "UNAVAILABLE"
    if risk_score < 25.0 or final_score < 43.0:
        return "NEGATIVE"
    if final_score >= 56.0:
        return "POSITIVE"
    return "NEUTRAL"


def sector_belief_id(sector: Any) -> Optional[str]:
    key = " ".join(str(sector or "").strip().lower().replace("&", "and").split())
    if key in SECTOR_ALIASES:
        return SECTOR_ALIASES[key]
    if "semiconductor" in key:
        return "sector.semiconductors.leadership"
    return None


def _relation(left: str, right: str) -> str:
    if "UNAVAILABLE" in {left, right}:
        return "UNAVAILABLE"
    if left == "NEUTRAL" and right == "NEUTRAL":
        return "NEUTRAL"
    if "NEUTRAL" in {left, right}:
        return "MIXED"
    return "AGREEMENT" if left == right else "CONFLICT"


def _top_down(market: str, sector: str) -> str:
    if market == "UNAVAILABLE" and sector == "UNAVAILABLE":
        return "UNAVAILABLE"
    if sector == "UNAVAILABLE":
        return f"MARKET_{market}"
    if market == "UNAVAILABLE":
        return f"SECTOR_{sector}"
    if market == "POSITIVE" and sector == "POSITIVE":
        return "SUPPORTIVE"
    if market == "NEGATIVE" and sector == "NEGATIVE":
        return "ADVERSE"
    if market == "NEUTRAL" and sector == "NEUTRAL":
        return "NEUTRAL"
    return "MIXED"


def _world_context(snapshot: Mapping[str, Any], sector: Any) -> Dict[str, Any]:
    broad = {
        belief_id: {
            "probability": _belief_probability(snapshot, belief_id),
            "confidence": _belief_confidence(snapshot, belief_id),
        }
        for belief_id in BROAD_MARKET_IDS
    }
    market_signed = _mean_available(
        _signed_probability(row["probability"]) for row in broad.values()
    )
    sector_id = sector_belief_id(sector)
    sector_probability = _belief_probability(snapshot, sector_id) if sector_id else None
    sector_confidence = _belief_confidence(snapshot, sector_id) if sector_id else None
    sector_signed = _signed_probability(sector_probability)
    factors = {
        belief_id: {
            "probability": _belief_probability(snapshot, belief_id),
            "confidence": _belief_confidence(snapshot, belief_id),
        }
        for belief_id in FACTOR_IDS
    }
    factor_signed = _mean_available(
        _signed_probability(row["probability"]) for row in factors.values()
    )
    return {
        "world_state_id": snapshot.get("world_state_id"),
        "context_as_of": snapshot.get("context_as_of"),
        "source_cutoff_at": snapshot.get("source_cutoff_at"),
        "broad_market": broad,
        "market_support_score": market_signed,
        "market_stance": _stance(market_signed),
        "sector": str(sector or ""),
        "sector_belief_id": sector_id,
        "sector_probability": sector_probability,
        "sector_confidence": sector_confidence,
        "sector_support_score": sector_signed,
        "sector_stance": _stance(sector_signed),
        "factor_context": factors,
        "factor_support_score": factor_signed,
        "factor_stance": _stance(factor_signed),
    }


def _entity_information(pair_item: Mapping[str, Any]) -> Dict[str, Any]:
    forecasts = []
    signed_rows = []
    for raw in pair_item.get("forecasts") or []:
        if not isinstance(raw, Mapping):
            continue
        p = _float(raw.get("predicted_probability"))
        confidence = _float(raw.get("forecast_confidence"))
        signed = None
        if p is not None and confidence is not None:
            signed = (2.0 * p - 1.0) * confidence
            signed_rows.append(signed)
        forecasts.append({
            "forecast_id": raw.get("forecast_id"),
            "belief_id": raw.get("belief_id"),
            "dimension": raw.get("dimension"),
            "predicted_probability": p,
            "probability_semantic_type": "model_probability",
            "forecast_confidence": confidence,
            "confidence_semantic_type": "belief_confidence",
            "forecast_at": raw.get("forecast_at"),
            "target_at": raw.get("target_at"),
            "forecast_world_state_id": raw.get("forecast_world_state_id"),
            "binding_id": raw.get("binding_id"),
        })
    aggregate = _mean_available(signed_rows)
    modifier = _float(pair_item.get("belief_modifier_score_points"), 0.0) or 0.0
    return {
        "contract_version": BELIEF_INFORMATION_CONTRACT_VERSION,
        "forecasts": forecasts,
        "dimensions": sorted({str(row.get("dimension")) for row in forecasts if row.get("dimension")}),
        "aggregate_confidence_weighted_signed_signal": aggregate,
        "entity_stance": _stance(aggregate),
        "primary_modifier_score_points": modifier,
        "modifier_nonzero": abs(modifier) > 1e-12,
        "with_action": pair_item.get("with_action"),
        "without_action": pair_item.get("without_action"),
        "decision_changed": pair_item.get("with_action") != pair_item.get("without_action"),
    }


def _engine_information(
    position: Mapping[str, Any],
    recommendation: Mapping[str, Any],
    pair_item: Mapping[str, Any],
    *,
    analysis: Mapping[str, Any],
    pending: Mapping[str, Any],
) -> Dict[str, Any]:
    scores = {field: _float(position.get(field)) for field in FEATURE_SCORE_FIELDS}
    expectations = {field: _float(position.get(field)) for field in EXPECTATION_FIELDS}
    raw_blocks = {field: _copy_mapping(position.get(field)) for field in RAW_FEATURE_BLOCKS}
    confidence = _float(position.get("confidence_score"), _float(recommendation.get("confidence")))
    final_score = _float(position.get("final_score"))
    risk_score = _float(position.get("risk_score"))
    return {
        "contract_version": ENGINE_INFORMATION_CONTRACT_VERSION,
        "instrument": str(position.get("instrument_id") or position.get("id") or "").lower(),
        "broker_symbol": position.get("broker_symbol") or recommendation.get("broker_symbol"),
        "asset_type": position.get("asset_type"),
        "sector": position.get("sector"),
        "region": position.get("region"),
        "currency": position.get("currency"),
        "engine_methodology_version": analysis.get("methodology_version") or pending.get("methodology_version"),
        "analysis_generated_at": analysis.get("generated_at"),
        "pending_generated_at": pending.get("generated_at"),
        "feature_scores": scores,
        "raw_feature_blocks": raw_blocks,
        "expectations": {
            **expectations,
            "probability_of_reaching_target_semantic_type": "model_probability",
            "probability_calibration_status": "uncalibrated",
        },
        "decision_context": {
            "final_score": final_score,
            "source_action": recommendation.get("action"),
            "without_action": pair_item.get("without_action"),
            "with_action": pair_item.get("with_action"),
            "current_weight": _float(recommendation.get("current_weight"), _float(position.get("current_weight"))),
            "proposed_weight": _float(recommendation.get("proposed_weight"), _float(position.get("current_weight"))),
            "target_weight": _float(position.get("target_weight")),
            "positive_factors": list(recommendation.get("positive_factors") or position.get("positive_factors") or []),
            "negative_factors": list(recommendation.get("negative_factors") or position.get("negative_factors") or []),
            "conditions_for_change": list(recommendation.get("conditions_for_change") or position.get("conditions_for_change") or []),
            "material_event_context": deepcopy(recommendation.get("material_event_context")),
        },
        "data_quality_confidence": {
            "value": confidence,
            "semantic_type": "data_quality_confidence",
            "probability_like": False,
        },
        "engine_stance": engine_stance(
            final_score=final_score,
            risk_score=risk_score,
            data_quality_confidence=confidence,
        ),
        "data_context": {
            "market_date": position.get("market_date") or position.get("latest_price_date"),
            "current_price": _float(position.get("current_price")),
            "current_price_updated_at": position.get("current_price_updated_at"),
            "current_price_source": position.get("current_price_source"),
            "current_fx_to_pln": _float(position.get("current_fx_to_pln")),
            "current_fx_updated_at": position.get("current_fx_updated_at"),
            "current_fx_source": position.get("current_fx_source"),
            "fundamental_data_status": position.get("fundamental_data_status"),
            "data_status": position.get("data_status"),
            "source_errors": list(position.get("source_errors") or []),
        },
        "static_thesis_fingerprint": _sha({
            "thesis_pl": position.get("thesis_pl"),
            "thesis_en": position.get("thesis_en"),
            "invalidation_pl": position.get("invalidation_pl"),
            "invalidation_en": position.get("invalidation_en"),
        }),
    }


def disagreement_topology(
    engine_information: Mapping[str, Any],
    belief_information: Mapping[str, Any],
    world_context: Mapping[str, Any],
) -> Dict[str, Any]:
    engine = str(engine_information.get("engine_stance") or "UNAVAILABLE")
    entity = str(belief_information.get("entity_stance") or "UNAVAILABLE")
    market = str(world_context.get("market_stance") or "UNAVAILABLE")
    sector = str(world_context.get("sector_stance") or "UNAVAILABLE")
    factor = str(world_context.get("factor_stance") or "UNAVAILABLE")
    engine_entity = _relation(engine, entity)
    entity_sector = _relation(entity, sector)
    entity_market = _relation(entity, market)
    engine_market = _relation(engine, market)
    top_down = _top_down(market, sector)
    pattern_parts = [
        f"ENGINE_{engine}",
        f"ENTITY_{entity}",
        f"MARKET_{market}",
        f"SECTOR_{sector}",
        f"FACTOR_{factor}",
        f"ENGINE_ENTITY_{engine_entity}",
        f"TOP_DOWN_{top_down}",
    ]
    return {
        "contract_version": DISAGREEMENT_CONTRACT_VERSION,
        "classification_threshold": {
            "signed_support_deadband": STANCE_EPSILON,
            "pnl_tuned": False,
            "promotion_threshold": False,
        },
        "engine_stance": engine,
        "entity_stance": entity,
        "market_stance": market,
        "sector_stance": sector,
        "factor_stance": factor,
        "engine_entity_relation": engine_entity,
        "entity_sector_relation": entity_sector,
        "entity_market_relation": entity_market,
        "engine_market_relation": engine_market,
        "top_down_state": top_down,
        "bottom_up_state": entity,
        "flags": {
            "engine_entity_conflict": engine_entity == "CONFLICT",
            "entity_sector_conflict": entity_sector == "CONFLICT",
            "entity_market_conflict": entity_market == "CONFLICT",
            "engine_market_conflict": engine_market == "CONFLICT",
        },
        "pattern_code": "__".join(pattern_parts),
    }


def _parity_ok(position: Mapping[str, Any], recommendation: Mapping[str, Any], pair_item: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    score = _float(position.get("final_score"))
    pair_score = _float(pair_item.get("original_score"))
    if score is None or pair_score is None or abs(score - pair_score) > 1e-6:
        issues.append("final_score_mismatch")
    source_action = str(recommendation.get("action") or "").upper()
    pair_source_action = str(pair_item.get("source_action") or "").upper()
    if source_action != pair_source_action:
        issues.append("source_action_mismatch")
    current_price = _float(position.get("current_price"))
    signal_price = _float(pair_item.get("signal_price"))
    if current_price is None or signal_price is None or current_price <= 0 or abs(current_price - signal_price) > max(1e-6, abs(current_price) * 1e-6):
        issues.append("signal_price_mismatch")
    return not issues, issues


def build_capture(
    pair: Mapping[str, Any],
    *,
    analysis: Mapping[str, Any],
    pending: Mapping[str, Any],
    world_state: Mapping[str, Any],
    captured_at: datetime,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    pair_id = str(pair.get("pair_set_id") or "")
    decision_at = parse_time(str(pair.get("decision_at") or ""))
    if pair.get("engine_consumed_belief") is not False or pair.get("hypothetical_only") is not True or pair.get("historical_backfill") is not False:
        return None, {
            "pair_set_id": pair_id,
            "status": "pr17_pair_governance_invalid",
            "recorded_at": iso_z(captured_at),
            "terminal_no_reconstruction": True,
        }
    frozen_hashes = pair.get("source_sha256") or {}
    current_hashes = {
        "analysis": _sha(analysis),
        "pending_decisions": _sha(pending),
    }
    if frozen_hashes.get("analysis") != current_hashes["analysis"] or frozen_hashes.get("pending_decisions") != current_hashes["pending_decisions"]:
        return None, {
            "pair_set_id": pair_id,
            "status": "source_snapshot_not_available",
            "recorded_at": iso_z(captured_at),
            "expected_source_sha256": {
                "analysis": frozen_hashes.get("analysis"),
                "pending_decisions": frozen_hashes.get("pending_decisions"),
            },
            "observed_source_sha256": current_hashes,
            "terminal_no_reconstruction": True,
        }
    snapshots = _world_snapshots(world_state)
    world_id = str(pair.get("decision_world_state_id") or "")
    world = snapshots.get(world_id)
    if world is None:
        return None, {
            "pair_set_id": pair_id,
            "status": "decision_world_state_missing",
            "recorded_at": iso_z(captured_at),
            "terminal_no_reconstruction": True,
        }
    try:
        created = parse_time(str(world.get("created_at") or ""))
        cutoff = parse_time(str(world.get("source_cutoff_at") or world.get("context_as_of") or ""))
    except Exception:
        created = captured_at + (captured_at - captured_at)
        cutoff = created
        return None, {
            "pair_set_id": pair_id,
            "status": "decision_world_state_timestamp_invalid",
            "recorded_at": iso_z(captured_at),
            "terminal_no_reconstruction": True,
        }
    if created > decision_at or cutoff > decision_at:
        return None, {
            "pair_set_id": pair_id,
            "status": "decision_world_state_not_prospective",
            "recorded_at": iso_z(captured_at),
            "terminal_no_reconstruction": True,
        }

    positions = _positions(analysis)
    recommendations = _recommendations(pending)
    items: List[Dict[str, Any]] = []
    for pair_item in pair.get("items") or []:
        if not isinstance(pair_item, Mapping):
            continue
        instrument = str(pair_item.get("instrument") or "").lower()
        position = positions.get(instrument)
        recommendation = recommendations.get(instrument)
        if position is None or recommendation is None:
            return None, {
                "pair_set_id": pair_id,
                "status": "instrument_source_row_missing",
                "instrument": instrument,
                "recorded_at": iso_z(captured_at),
                "terminal_no_reconstruction": True,
            }
        ok, issues = _parity_ok(position, recommendation, pair_item)
        if not ok:
            return None, {
                "pair_set_id": pair_id,
                "status": "engine_information_parity_failure",
                "instrument": instrument,
                "issues": issues,
                "recorded_at": iso_z(captured_at),
                "terminal_no_reconstruction": True,
            }
        engine_info = _engine_information(
            position,
            recommendation,
            pair_item,
            analysis=analysis,
            pending=pending,
        )
        belief_info = _entity_information(pair_item)
        world_context = _world_context(world, position.get("sector"))
        topology = disagreement_topology(engine_info, belief_info, world_context)
        item = {
            "instrument": instrument,
            "engine_information": engine_info,
            "belief_information": belief_info,
            "world_context": world_context,
            "disagreement_topology": topology,
            "research_readiness": {
                "economic_incremental_value": "PENDING_PR17_OUTCOME",
                "redundancy_status": "NOT_YET_ESTIMABLE",
                "orthogonality_status": "NOT_YET_ESTIMABLE",
                "miv_score": None,
                "miv_score_contract_exists": False,
            },
        }
        item["immutable_sha256"] = _sha(item)
        items.append(item)

    if not items:
        return None, {
            "pair_set_id": pair_id,
            "status": "no_capture_items",
            "recorded_at": iso_z(captured_at),
            "terminal_no_reconstruction": True,
        }
    payload = {
        "contract_version": CONTRACT_VERSION,
        "pair_set_id": pair_id,
        "decision_set_id": pair.get("decision_set_id"),
        "decision_at": pair.get("decision_at"),
        "decision_world_state_id": world_id,
        "engine_methodology_version": pair.get("engine_methodology_version"),
        "items": items,
        "source_sha256": {
            "analysis": current_hashes["analysis"],
            "pending_decisions": current_hashes["pending_decisions"],
        },
    }
    capture = {
        **payload,
        "capture_id": _stable_id("brace-information", payload),
        "captured_at": iso_z(captured_at),
        "prospective_to_economic_outcome": True,
        "historical_information_backfill": False,
        "source_reconstruction": False,
        "promotion_authority": False,
    }
    capture["immutable_sha256"] = _sha(capture)
    return capture, None


def _assert_append_only(previous: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    for field in ("captures", "terminal_uncaptured"):
        before = previous.get(field) or {}
        after = current.get(field) or {}
        for key, value in before.items():
            if key not in after or after[key] != value:
                raise RuntimeError(f"PR18 append-only mutation detected in {field}: {key}")


def _outcome_map(pr17_state: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {
        str(key): value
        for key, value in (pr17_state.get("economic_outcomes") or {}).items()
        if isinstance(value, Mapping)
    }


def _descriptive_rows(state: Mapping[str, Any], pr17_state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    outcomes = _outcome_map(pr17_state)
    rows: List[Dict[str, Any]] = []
    for pair_id, capture in sorted((state.get("captures") or {}).items()):
        outcome = outcomes.get(str(pair_id)) or {}
        matured = outcome.get("status") == "matured" and outcome.get("calibration_eligible") is True
        for item in capture.get("items") or []:
            topology = item.get("disagreement_topology") or {}
            belief = item.get("belief_information") or {}
            rows.append({
                "pair_set_id": pair_id,
                "instrument": item.get("instrument"),
                "pattern_code": topology.get("pattern_code"),
                "engine_entity_relation": topology.get("engine_entity_relation"),
                "top_down_state": topology.get("top_down_state"),
                "modifier_nonzero": bool(belief.get("modifier_nonzero")),
                "decision_changed": bool(belief.get("decision_changed")),
                "matured": matured,
                "delta_return": outcome.get("delta_return") if matured else None,
                "delta_pnl_pln": outcome.get("delta_pnl_pln") if matured else None,
                "redundancy_status": "NOT_YET_ESTIMABLE",
                "orthogonality_status": "NOT_YET_ESTIMABLE",
                "miv_score": None,
            })
    return rows


def _group_diagnostics(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: MutableMapping[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("pattern_code") or "UNAVAILABLE")].append(row)
    result = []
    for pattern, items in sorted(grouped.items()):
        matured = [x for x in items if x.get("matured") is True and x.get("delta_return") is not None]
        result.append({
            "pattern_code": pattern,
            "raw_n": len(items),
            "matured_n": len(matured),
            "decision_change_rate": (
                sum(1 for x in items if x.get("decision_changed")) / len(items)
                if items else None
            ),
            "nonzero_modifier_rate": (
                sum(1 for x in items if x.get("modifier_nonzero")) / len(items)
                if items else None
            ),
            "mean_delta_return": (
                sum(float(x["delta_return"]) for x in matured) / len(matured)
                if matured else None
            ),
            "promotion_interpretation": "DESCRIPTIVE_ONLY",
        })
    return result


def run(
    state_dir: Path,
    *,
    pr17_state_path: Path,
    pr17_report_path: Path,
    world_state_path: Path,
    world_report_path: Path,
    analysis_path: Path,
    pending_path: Path,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    _assert_safety()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_z = iso_z(now)
    state_dir = Path(state_dir)
    state_path = state_dir / STATE_FILENAME
    report_path = state_dir / REPORT_FILENAME

    pr17_state = _read_json(pr17_state_path, {})
    pr17_report = _read_json(pr17_report_path, {})
    world_state = _read_json(world_state_path, {})
    world_report = _read_json(world_report_path, {})
    analysis = _read_json(analysis_path, {})
    pending = _read_json(pending_path, {})
    _validate_inputs(pr17_state, pr17_report, world_state, world_report)

    previous = _read_json(state_path, empty_state())
    state = deepcopy(previous)
    if str(state.get("schema_version") or "") != SCHEMA_VERSION:
        raise ValueError("PR18 state schema mismatch")
    if str(state.get("contract_version") or "") != CONTRACT_VERSION:
        raise ValueError("PR18 state contract mismatch")

    first_run = not bool(state.get("activated_at"))
    if first_run:
        state["activated_at"] = now_z

    pairs = {
        str(key): value
        for key, value in (pr17_state.get("pair_sets") or {}).items()
        if isinstance(value, Mapping)
    }
    seen = set(str(x) for x in state.get("seen_pr17_pair_set_ids") or [])
    pre_activation = set(str(x) for x in state.get("pre_activation_pr17_pair_set_ids") or [])
    new_captures = 0
    new_terminal = 0

    if first_run:
        for pair_id in pairs:
            seen.add(pair_id)
            pre_activation.add(pair_id)
    else:
        for pair_id, pair in sorted(pairs.items()):
            if pair_id in seen:
                continue
            seen.add(pair_id)
            capture, terminal = build_capture(
                pair,
                analysis=analysis,
                pending=pending,
                world_state=world_state,
                captured_at=now,
            )
            if capture is not None:
                state.setdefault("captures", {})[pair_id] = capture
                new_captures += 1
            elif terminal is not None:
                state.setdefault("terminal_uncaptured", {})[pair_id] = terminal
                new_terminal += 1

    state["seen_pr17_pair_set_ids"] = sorted(seen)
    state["pre_activation_pr17_pair_set_ids"] = sorted(pre_activation)
    state["last_run_at"] = now_z
    _assert_append_only(previous, state)
    _write_json(state_path, state)

    descriptive = _descriptive_rows(state, pr17_state)
    pattern_counts = Counter(str(row.get("pattern_code") or "UNAVAILABLE") for row in descriptive)
    matured_rows = [row for row in descriptive if row.get("matured") is True]
    report = {
        "schema_version": SCHEMA_VERSION,
        "report_version": REPORT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "mode": MODE,
        "generated_at": now_z,
        "purpose": "Freeze BRACE information, Entity Belief information and disagreement topology for prospective PR17 pairs.",
        "active_decision_influence": False,
        "sample": {
            "activation_only_this_run": first_run,
            "pr17_pair_sets_visible": len(pairs),
            "pair_sets_seen_total": len(seen),
            "pre_activation_pair_sets_total": len(pre_activation),
            "captures_total": len(state.get("captures") or {}),
            "terminal_uncaptured_total": len(state.get("terminal_uncaptured") or {}),
            "new_captures_this_run": new_captures,
            "new_terminal_uncaptured_this_run": new_terminal,
            "descriptive_item_rows": len(descriptive),
            "matured_descriptive_rows": len(matured_rows),
        },
        "information_contracts": {
            "engine_information": ENGINE_INFORMATION_CONTRACT_VERSION,
            "belief_information": BELIEF_INFORMATION_CONTRACT_VERSION,
            "disagreement_topology": DISAGREEMENT_CONTRACT_VERSION,
            "source_snapshot_sha_parity_required": True,
            "historical_information_backfill": False,
            "retroactive_source_reconstruction": False,
        },
        "disagreement_topology": {
            "stance_deadband": STANCE_EPSILON,
            "pnl_tuned": False,
            "pattern_counts": dict(sorted(pattern_counts.items())),
            "pattern_diagnostics": _group_diagnostics(descriptive),
        },
        "marginal_information_value": {
            "status": "MEASUREMENT_INPUTS_ONLY",
            "miv_score": None,
            "miv_score_contract_exists": False,
            "economic_incremental_value_available_when_pr17_pair_matures": True,
            "redundancy_status": "NOT_YET_ESTIMABLE",
            "orthogonality_status": "NOT_YET_ESTIMABLE",
            "next_research_stage": "PR18.1 Marginal Information Value Diagnostics",
            "rule": "Do not infer edge from correctness alone; test incremental economic value beyond the frozen engine information set.",
        },
        "descriptive_rows": descriptive,
        "captures": [state["captures"][key] for key in sorted(state.get("captures") or {})],
        "terminal_uncaptured": [state["terminal_uncaptured"][key] for key in sorted(state.get("terminal_uncaptured") or {})],
        "anti_hindsight": {
            "first_run_existing_pairs_cursor_only": True,
            "pre_activation_pairs_not_captured": True,
            "source_hashes_must_match_pr17_frozen_hashes": True,
            "source_snapshot_mismatch_is_terminal": True,
            "historical_information_set_backfill": False,
            "retroactive_source_reconstruction": False,
            "world_state_must_preexist_decision": True,
        },
        "research_boundary": {
            "information_capture_is_measurement_not_alpha": True,
            "disagreement_topology_is_descriptive_not_alpha": True,
            "miv_score_enabled": False,
            "causal_belief_graph_enabled": False,
            "engine_specific_trust_enabled": False,
        },
        "promotion": {
            "status": "NOT_ELIGIBLE_FOR_PROMOTION_REVIEW",
            "eligible_for_promotion_review": False,
            "automatic_promotion": False,
            "effective_n_threshold_defined_here": False,
        },
        "capabilities": capabilities(),
        "safety_controls": safety_controls(),
    }
    _write_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="BRACE Information Set & Disagreement Capture")
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--pr17-state", required=True, type=Path)
    parser.add_argument("--pr17-report", required=True, type=Path)
    parser.add_argument("--world-state", required=True, type=Path)
    parser.add_argument("--world-report", required=True, type=Path)
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--pending", required=True, type=Path)
    args = parser.parse_args()
    report = run(
        args.state_dir,
        pr17_state_path=args.pr17_state,
        pr17_report_path=args.pr17_report,
        world_state_path=args.world_state,
        world_report_path=args.world_report,
        analysis_path=args.analysis,
        pending_path=args.pending,
    )
    print(json.dumps({
        "status": report["promotion"]["status"],
        "captures_total": report["sample"]["captures_total"],
        "matured_descriptive_rows": report["sample"]["matured_descriptive_rows"],
        "miv_status": report["marginal_information_value"]["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
