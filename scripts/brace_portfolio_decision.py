#!/usr/bin/env python3
"""Deterministic recommendations, rotations and shadow decision records."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from brace_portfolio_config import EngineConfig

DECISION_STATUSES = {
    "PROPOSED",
    "APPROVED",
    "REJECTED",
    "EXPIRED",
    "EXECUTED",
    "CANCELLED",
}
ACTIONS = {
    "HOLD",
    "WATCH",
    "REDUCE",
    "EXIT",
    "ADD",
    "REPLACE",
    "REBALANCE",
    "NO_ACTION",
}
LEARNING_ACTIONS = {"ADD", "REDUCE", "EXIT", "REPLACE"}


def deterministic_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:16]}"


def recommendation_for_position(
    analysis: Mapping[str, Any],
    current_weight: float,
    target_weight: float,
    safe_mode: bool = False,
) -> str:
    score = float(analysis.get("final_score") or 0.0)
    risk_score = float(analysis.get("risk_score") or 0.0)
    confidence = float(analysis.get("confidence_score") or 0.0)
    if safe_mode:
        return "WATCH"
    if confidence < 0.5:
        return "WATCH"
    if risk_score < 25 or score < 30:
        return "EXIT"
    if score < 43:
        return "REDUCE"
    if score < 56:
        return "WATCH"
    if score >= 74 and current_weight + 0.015 < target_weight:
        return "ADD"
    return "HOLD"


def _days_since(value: Any, today: date) -> int:
    if not value:
        return 99999
    try:
        return max(0, (today - date.fromisoformat(str(value)[:10])).days)
    except ValueError:
        return 0


def _recent_rotation(
    instrument_id: str,
    decision_history: Sequence[Mapping[str, Any]],
    today: date,
    cooldown_days: int,
) -> bool:
    for item in reversed(decision_history):
        if str(item.get("instrument") or "") != instrument_id:
            continue
        action = str(item.get("action") or item.get("brace_decision") or "")
        if action not in {"REPLACE", "EXIT", "ADD"}:
            continue
        generated = str(item.get("generated_at") or "")[:10]
        if generated and _days_since(generated, today) < cooldown_days:
            return True
    return False


def _rationale(
    action: str,
    current: Mapping[str, Any],
    candidate: Optional[Mapping[str, Any]],
    advantage: float,
) -> tuple[str, str]:
    current_symbol = current.get("broker_symbol") or current.get("instrument_id")
    candidate_symbol = (
        candidate.get("broker_symbol") if candidate else None
    ) or "—"
    if action == "REPLACE":
        return (
            f"{candidate_symbol} ma trwale lepszy profil skorygowany o ryzyko "
            f"niż {current_symbol}; przewaga punktowa wynosi {advantage:.1f}. "
            "Zmiana pozostaje propozycją do czasu spełnienia bramek wykonania.",
            f"{candidate_symbol} has a persistently better risk-adjusted profile "
            f"than {current_symbol}; the score advantage is {advantage:.1f}. "
            "The change remains a proposal until execution gates pass.",
        )
    if action == "NO_ACTION":
        return (
            "Żaden kandydat nie zapewnia obecnie materialnej przewagi po kosztach "
            "i przy obowiązujących limitach ryzyka.",
            "No candidate currently offers a material post-cost advantage within "
            "the applicable risk limits.",
        )
    return (
        f"Rekomendacja {action} wynika z wieloczynnikowej oceny {current_symbol}, "
        "jakości danych i bieżących limitów ryzyka.",
        f"The {action} recommendation follows from the multi-factor assessment "
        f"of {current_symbol}, data quality and current risk limits.",
    )


def _economic_id(prefix: str, payload: Mapping[str, Any]) -> str:
    return deterministic_id(f"economic-{prefix}", payload)


def build_pending_decisions(
    positions: Iterable[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    optimization: Mapping[str, Any],
    config: EngineConfig,
    generated_at: datetime,
    methodology_version: str,
    data_timestamp: str,
    safe_mode: bool,
    previous_pending: Optional[Mapping[str, Any]] = None,
    decision_history: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    today = generated_at.date()
    history = list(decision_history or [])
    previous_by_id = {
        str(item.get("decision_id")): item
        for item in (previous_pending or {}).get("decisions", []) or []
    }
    target_weights = optimization.get("target_weights") or {}
    position_rows = [dict(item) for item in positions]
    recommendations: List[Dict[str, Any]] = []
    for item in position_rows:
        current_weight = float(item.get("current_weight") or 0.0)
        target_weight = float(item.get("target_weight") or current_weight)
        action = recommendation_for_position(
            item,
            current_weight,
            target_weight,
            safe_mode,
        )
        rationale_pl, rationale_en = _rationale(action, item, None, 0.0)
        recommendations.append(
            {
                "instrument": item.get("instrument_id"),
                "broker_symbol": item.get("broker_symbol"),
                "action": action,
                "final_score": item.get("final_score"),
                "confidence": item.get("confidence_score"),
                "current_weight": current_weight,
                "proposed_weight": float(
                    target_weights.get(item.get("instrument_id"), current_weight)
                ),
                "signal_price": item.get("current_price"),
                "signal_fx_to_pln": item.get("current_fx_to_pln"),
                "positive_factors": item.get("positive_factors") or [],
                "negative_factors": item.get("negative_factors") or [],
                "rationale_pl": rationale_pl,
                "rationale_en": rationale_en,
                "conditions_for_change": item.get("conditions_for_change") or [],
            }
        )

    rotations: List[Dict[str, Any]] = []
    eligible_candidates = [
        item
        for item in candidates
        if item.get("eligible_for_rotation") and not safe_mode
    ]
    weakest = sorted(
        position_rows,
        key=lambda item: (
            float(item.get("risk_adjusted_score") or -99.0),
            float(item.get("final_score") or 0.0),
            str(item.get("instrument_id")),
        ),
    )
    for current in weakest:
        if not eligible_candidates:
            break
        candidate = eligible_candidates[0]
        score_improvement = float(candidate.get("final_score") or 0.0) - float(
            current.get("final_score") or 0.0
        )
        expected_alpha = float(
            candidate.get("expected_return_base") or 0.0
        ) - float(current.get("expected_return_base") or 0.0)
        confidence = min(
            float(candidate.get("confidence_score") or 0.0),
            float(current.get("confidence_score") or 0.0),
        )
        transaction_cost = config.transaction_cost_buffer
        checks = {
            "score_improvement": (
                score_improvement >= config.minimum_score_improvement
            ),
            "expected_alpha": expected_alpha >= config.minimum_expected_alpha,
            "holding_period": (
                _days_since(current.get("entry_date"), today)
                >= config.minimum_holding_period_days
            ),
            "rotation_cooldown": not _recent_rotation(
                str(current.get("instrument_id")),
                history,
                today,
                config.rotation_cooldown_days,
            ),
            "confidence": confidence >= config.minimum_confidence,
            "transaction_cost_buffer": expected_alpha > transaction_cost,
            "portfolio_risk_improves": (
                float(candidate.get("expected_drawdown") or 1.0)
                <= float(current.get("expected_drawdown") or 1.0)
                or float(candidate.get("risk_adjusted_score") or -99.0)
                > float(current.get("risk_adjusted_score") or -99.0)
            ),
            "data_quality": (
                float(candidate.get("confidence_score") or 0.0)
                >= config.minimum_confidence
            ),
            "optimization_rules": bool(optimization.get("rules_passed")),
        }
        if not all(checks.values()):
            continue
        current_weight = float(current.get("current_weight") or 0.0)
        proposed_weight = min(
            current_weight,
            config.max_single_stock_weight
            if candidate.get("asset_type") == "STOCK"
            else config.max_broad_etf_weight,
        )
        rationale_pl, rationale_en = _rationale(
            "REPLACE",
            current,
            candidate,
            score_improvement,
        )
        payload = {
            "date": today.isoformat(),
            "from": current.get("instrument_id"),
            "to": candidate.get("instrument_id"),
            "methodology": methodology_version,
        }
        decision_id = deterministic_id("rotation", payload)
        economic_decision_id = _economic_id("rotation", payload)
        prior = previous_by_id.get(decision_id) or {}
        rotations.append(
            {
                "decision_id": decision_id,
                "economic_decision_id": economic_decision_id,
                "generated_at": generated_at.isoformat(timespec="seconds"),
                "action": "REPLACE",
                "instrument": current.get("instrument_id"),
                "replacement_instrument": candidate.get("instrument_id"),
                "current_weight": current_weight,
                "proposed_weight": proposed_weight,
                "signal_price": current.get("current_price"),
                "replacement_signal_price": candidate.get("current_price"),
                "signal_fx_to_pln": current.get("current_fx_to_pln"),
                "replacement_signal_fx_to_pln": candidate.get("current_fx_to_pln"),
                "expected_benefit": round(expected_alpha, 6),
                "expected_risk": candidate.get("expected_drawdown"),
                "confidence": round(confidence, 4),
                "rationale_pl": rationale_pl,
                "rationale_en": rationale_en,
                "data_timestamp": data_timestamp,
                "methodology_version": methodology_version,
                "status": (
                    prior.get("status")
                    if prior.get("status") in DECISION_STATUSES
                    else "PROPOSED"
                ),
                "checks": checks,
                "transaction_cost_buffer": transaction_cost,
                "learning_eligible": True,
            }
        )
        break

    if not rotations and not safe_mode:
        priority = {"EXIT": 3, "REDUCE": 2, "ADD": 1}
        actionable = sorted(
            (
                item
                for item in recommendations
                if item.get("action") in priority
                and float(item.get("confidence") or 0.0) >= config.minimum_confidence
            ),
            key=lambda item: (
                priority[str(item.get("action"))],
                -float(item.get("final_score") or 0.0),
                str(item.get("instrument")),
            ),
            reverse=True,
        )
        if actionable:
            selected = actionable[0]
            action = str(selected["action"])
            proposed_weight = (
                0.0
                if action == "EXIT"
                else float(selected.get("proposed_weight") or 0.0)
            )
            payload = {
                "date": today.isoformat(),
                "action": action,
                "instrument": selected.get("instrument"),
                "methodology": methodology_version,
            }
            decision_id = deterministic_id("allocation", payload)
            economic_decision_id = _economic_id("allocation", payload)
            prior = previous_by_id.get(decision_id) or {}
            rotations.append(
                {
                    "decision_id": decision_id,
                    "economic_decision_id": economic_decision_id,
                    "generated_at": generated_at.isoformat(timespec="seconds"),
                    "action": action,
                    "instrument": selected.get("instrument"),
                    "replacement_instrument": None,
                    "current_weight": selected.get("current_weight"),
                    "proposed_weight": proposed_weight,
                    "signal_price": selected.get("signal_price"),
                    "signal_fx_to_pln": selected.get("signal_fx_to_pln"),
                    "expected_benefit": 0.0,
                    "expected_risk": None,
                    "confidence": selected.get("confidence"),
                    "rationale_pl": selected.get("rationale_pl"),
                    "rationale_en": selected.get("rationale_en"),
                    "data_timestamp": data_timestamp,
                    "methodology_version": methodology_version,
                    "status": (
                        prior.get("status")
                        if prior.get("status") in DECISION_STATUSES
                        else "PROPOSED"
                    ),
                    "checks": {
                        "confidence": True,
                        "safe_mode": False,
                        "optimizer_target_applied": True,
                        "transaction_cost_buffer": True,
                    },
                    "transaction_cost_buffer": config.transaction_cost_buffer,
                    "learning_eligible": True,
                }
            )

    if not rotations:
        payload = {
            "date": today.isoformat(),
            "action": "NO_ACTION",
            "methodology": methodology_version,
        }
        decision_id = deterministic_id("decision", payload)
        economic_decision_id = _economic_id("no-action", payload)
        rationale_pl, rationale_en = _rationale(
            "NO_ACTION",
            position_rows[0] if position_rows else {},
            None,
            0.0,
        )
        rotations.append(
            {
                "decision_id": decision_id,
                "economic_decision_id": economic_decision_id,
                "generated_at": generated_at.isoformat(timespec="seconds"),
                "action": "NO_ACTION",
                "instrument": None,
                "replacement_instrument": None,
                "current_weight": None,
                "proposed_weight": None,
                "signal_price": None,
                "expected_benefit": 0.0,
                "expected_risk": None,
                "confidence": min(
                    [
                        float(item.get("confidence_score") or 0.0)
                        for item in position_rows
                    ]
                    or [0.0]
                ),
                "rationale_pl": rationale_pl,
                "rationale_en": rationale_en,
                "data_timestamp": data_timestamp,
                "methodology_version": methodology_version,
                "status": "PROPOSED",
                "checks": {
                    "safe_mode": safe_mode,
                    "no_material_post_cost_advantage": True,
                },
                "transaction_cost_buffer": config.transaction_cost_buffer,
                "learning_eligible": False,
            }
        )

    return {
        "schema_version": "1.1.0",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "methodology_version": methodology_version,
        "data_freshness": "current" if not safe_mode else "unsafe",
        "source_metadata": {
            "decision_engine": "brace_portfolio_decision.py",
            "autonomy_mode": config.autonomy_mode,
            "paper_only": True,
            "economic_decision_schema": "1.0",
        },
        "safe_mode": safe_mode,
        "recommendations": recommendations,
        "decisions": rotations,
    }


def shadow_record(
    pending: Mapping[str, Any],
    baseline_positions: Sequence[Mapping[str, Any]],
    generated_at: datetime,
) -> Dict[str, Any]:
    baseline_by_id = {
        str(item.get("id")): str(item.get("review_flag") or "HOLD")
        for item in baseline_positions
    }
    recommendations = []
    for item in pending.get("recommendations", []) or []:
        recommendations.append(
            {
                "instrument": item.get("instrument"),
                "brace_decision": item.get("action"),
                "baseline_decision": baseline_by_id.get(
                    str(item.get("instrument")),
                    "HOLD",
                ),
                "record_type": "recommendation_snapshot",
                "learning_eligible": False,
                "hypothetical_execution_status": "NOT_EXECUTED_SHADOW",
                "signal_price": item.get("signal_price"),
                "execution_price": None,
                "costs": None,
                "later_outcome": None,
                "maximum_favorable_excursion": None,
                "maximum_adverse_excursion": None,
                "portfolio_impact": None,
            }
        )

    economic_decisions = []
    for item in pending.get("decisions", []) or []:
        action = str(item.get("action") or "").upper()
        instrument = item.get("instrument")
        economic_decisions.append(
            {
                "decision_id": item.get("decision_id"),
                "economic_decision_id": item.get("economic_decision_id")
                or item.get("decision_id"),
                "instrument": instrument,
                "replacement_instrument": item.get("replacement_instrument"),
                "brace_decision": action,
                "baseline_decision": baseline_by_id.get(str(instrument), "HOLD"),
                "record_type": "economic_decision",
                "learning_eligible": bool(
                    item.get("learning_eligible", action in LEARNING_ACTIONS)
                    and action in LEARNING_ACTIONS
                ),
                "current_weight": item.get("current_weight"),
                "proposed_weight": item.get("proposed_weight"),
                "expected_benefit": item.get("expected_benefit"),
                "expected_risk": item.get("expected_risk"),
                "confidence": item.get("confidence"),
                "data_timestamp": item.get("data_timestamp"),
                "methodology_version": item.get("methodology_version"),
                "hypothetical_execution_status": "NOT_EXECUTED_SHADOW",
                "signal_price": item.get("signal_price"),
                "replacement_signal_price": item.get("replacement_signal_price"),
                "signal_fx_to_pln": item.get("signal_fx_to_pln"),
                "replacement_signal_fx_to_pln": item.get("replacement_signal_fx_to_pln"),
                "execution_price": None,
                "replacement_execution_price": None,
                "costs": item.get("transaction_cost_buffer"),
                "later_outcome": None,
                "maximum_favorable_excursion": None,
                "maximum_adverse_excursion": None,
                "portfolio_impact": None,
            }
        )

    return {
        "shadow_run_id": deterministic_id(
            "shadow",
            {
                "generated_at": generated_at.isoformat(timespec="seconds"),
                "decisions": recommendations,
                "economic_decisions": economic_decisions,
            },
        ),
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "decisions": recommendations,
        "economic_decisions": economic_decisions,
    }
