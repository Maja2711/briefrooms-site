#!/usr/bin/env python3
"""Adaptive memory, outcomes and evidence learning for BRACE.

Decisions are immutable, outcomes are append-only, and learned evidence
multipliers are Bayesian, sample-gated and bounded. The module has no network
access, so the learning logic can be tested deterministically.
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import portfolio_10k_brace_engine as engine

SCHEMA_VERSION = "2.0.0"
HORIZONS_DAYS = (7, 30, 90, 180, 365)
HORIZON_WEIGHTS = {7: 0.30, 30: 0.65, 90: 1.0, 180: 1.0, 365: 1.0}
PRIOR_ALPHA = 4.0
PRIOR_BETA = 4.0
MIN_EFFECTIVE_SAMPLES = 8.0
FULL_MATURITY_SAMPLES = 24.0
MIN_MULTIPLIER = 0.80
MAX_MULTIPLIER = 1.20
EXCESS_RETURN_DEADBAND = 0.005


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def _num(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_memory(now=None):
    stamp = now or _now()
    return {
        "schema_version": SCHEMA_VERSION,
        "model_id": "BRACE",
        "created_at": stamp,
        "updated_at": stamp,
        "objective": {
            "primary_pl": "Maksymalizować pięcioletnią geometryczną stopę zwrotu netto po kosztach, przy kontrolowanym ryzyku trwałej utraty kapitału.",
            "primary_en": "Maximise five-year geometric return net of costs while controlling the risk of permanent capital loss.",
            "constraints": {
                "no_leverage": True,
                "no_cfds": True,
                "single_stock_max_weight": 0.18,
                "broad_etf_max_weight": 0.30,
                "target_max_drawdown": 0.30,
                "human_approval_required": True,
            },
        },
        "learning_policy": {
            "mode": "bounded_bayesian_adaptive_evidence",
            "horizons_days": list(HORIZONS_DAYS),
            "prior_alpha": PRIOR_ALPHA,
            "prior_beta": PRIOR_BETA,
            "minimum_effective_samples": MIN_EFFECTIVE_SAMPLES,
            "full_maturity_samples": FULL_MATURITY_SAMPLES,
            "multiplier_bounds": [MIN_MULTIPLIER, MAX_MULTIPLIER],
            "counterfactual_attribution": True,
            "decisions_immutable": True,
            "outcomes_append_only": True,
            "learned_changes_apply_next_review": True,
            "pillar_weights_self_modified": False,
            "decision_thresholds_self_modified": False,
        },
        "decisions": [],
        "outcome_events": [],
        "reliability": {"global": {}, "contextual": {}, "generated_at": stamp},
        "audit": [],
    }


def load_memory(path: Path, legacy_snapshot=None):
    try:
        memory = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        memory = {}
    if not isinstance(memory, dict) or not memory.get("model_id"):
        memory = new_memory()
    defaults = new_memory()
    for key in ("decisions", "outcome_events", "audit"):
        memory.setdefault(key, [])
    memory.setdefault("objective", defaults["objective"])
    memory.setdefault("learning_policy", defaults["learning_policy"])
    memory.setdefault("reliability", {"global": {}, "contextual": {}})
    memory.setdefault("created_at", _now())
    if legacy_snapshot:
        _migrate_legacy(memory, legacy_snapshot)
    return memory


def write_memory(path: Path, memory):
    payload = dict(memory)
    payload["schema_version"] = SCHEMA_VERSION
    payload["updated_at"] = _now()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _migrate_legacy(memory, snapshot):
    existing = {str(item.get("decision_id")) for item in memory["decisions"]}
    count = 0
    for item in snapshot.get("decision_history", []) or []:
        week, position_id = str(item.get("week_id") or ""), str(item.get("id") or "")
        decision_id = f"legacy:{week}:{position_id}"
        if not week or not position_id or decision_id in existing:
            continue
        memory["decisions"].append({
            "decision_id": decision_id,
            "week_id": week,
            "review_date": item.get("review_date"),
            "id": position_id,
            "broker_symbol": item.get("broker_symbol"),
            "decision": item.get("decision"),
            "score": item.get("score"),
            "confidence": item.get("confidence"),
            "reference_price": item.get("reference_price"),
            "reference_benchmark_price": item.get("reference_benchmark_price"),
            "evidence": [],
            "counterfactuals": [],
            "legacy_record": True,
            "immutable": True,
        })
        existing.add(decision_id)
        count += 1
    if count:
        memory["audit"].append({"at": _now(), "event": "legacy_history_migrated", "records": count})


def context_key(code, asset_type, regime):
    return f"{code}|{asset_type or 'Unknown'}|{regime or 'unknown'}"


def _posterior(alpha, beta, samples):
    mean = alpha / max(alpha + beta, 1e-9)
    if samples < MIN_EFFECTIVE_SAMPLES:
        maturity, multiplier, active = 0.0, 1.0, False
    else:
        maturity = min(1.0, max(0.0, (samples - MIN_EFFECTIVE_SAMPLES) / (FULL_MATURITY_SAMPLES - MIN_EFFECTIVE_SAMPLES)))
        multiplier = max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, 1.0 + (mean - 0.5) * 0.8 * maturity))
        active = True
    return {
        "alpha": round(alpha, 6),
        "beta": round(beta, 6),
        "effective_samples": round(samples, 6),
        "posterior_mean": round(mean, 6),
        "maturity": round(maturity, 6),
        "multiplier": round(multiplier, 6),
        "active": active,
    }


def rebuild_reliability(memory):
    global_stats, contextual_stats = {}, {}

    def update(bucket, key, correct, credit):
        if credit <= 0:
            return
        alpha, beta, samples = bucket.setdefault(key, [PRIOR_ALPHA, PRIOR_BETA, 0.0])
        alpha += credit if correct else 0.0
        beta += 0.0 if correct else credit
        bucket[key] = [alpha, beta, samples + credit]

    for event in memory.get("outcome_events", []) or []:
        for item in event.get("evidence_attribution", []) or []:
            if item.get("neutral_outcome"):
                continue
            code = str(item.get("code") or "")
            credit = max(0.0, _num(item.get("credit")))
            if not code or credit <= 0:
                continue
            correct = bool(item.get("signal_correct"))
            update(global_stats, code, correct, credit)
            update(contextual_stats, context_key(code, event.get("asset_type"), event.get("market_regime")), correct, credit)

    state = {
        "global": {key: _posterior(*values) for key, values in sorted(global_stats.items())},
        "contextual": {key: _posterior(*values) for key, values in sorted(contextual_stats.items())},
        "generated_at": _now(),
        "minimum_effective_samples": MIN_EFFECTIVE_SAMPLES,
        "bounds": [MIN_MULTIPLIER, MAX_MULTIPLIER],
    }
    memory["reliability"] = state
    return state


def multiplier_for(memory, code, asset_type, regime):
    reliability = memory.get("reliability") or {}
    exact_key = context_key(code, asset_type, regime)
    exact = (reliability.get("contextual") or {}).get(exact_key) or {}
    global_item = (reliability.get("global") or {}).get(code) or {}
    if exact.get("active"):
        chosen, source = exact, "contextual"
    elif global_item.get("active"):
        chosen, source = global_item, "global"
    else:
        chosen, source = exact or global_item, "insufficient_sample"
    multiplier = _num(chosen.get("multiplier"), 1.0) if chosen else 1.0
    return multiplier, {
        "source": source,
        "context_key": exact_key,
        "posterior_mean": chosen.get("posterior_mean", 0.5) if chosen else 0.5,
        "effective_samples": chosen.get("effective_samples", 0.0) if chosen else 0.0,
        "active": bool(chosen.get("active")) if chosen else False,
        "multiplier": round(multiplier, 6),
    }


def multipliers_for_evidence(memory, evidence, asset_type, regime):
    multipliers, metadata = {}, {}
    for item in evidence:
        multiplier, detail = multiplier_for(memory, item.code, asset_type, regime)
        multipliers[item.code], metadata[item.code] = multiplier, detail
    return multipliers, metadata


def adjusted_evidence(evidence, multipliers):
    output = []
    for item in evidence:
        multiplier = max(0.5, min(1.5, _num(multipliers.get(item.code), 1.0)))
        scale = math.sqrt(multiplier)
        output.append(replace(
            item,
            strength=max(0.0, min(1.0, item.strength * scale)),
            quality=max(0.0, min(1.0, item.quality * scale)),
        ))
    return output


def _decision(result, kwargs):
    return engine.decide(engine.DecisionInput(
        score=_num(result.get("score"), 50.0),
        confidence=_num(result.get("confidence")),
        pillar_scores=result.get("pillar_scores") or {},
        contradictions=result.get("contradictions") or [],
        current_weight=kwargs.get("current_weight"),
        target_weight=_num(kwargs.get("target_weight")),
        days_to_earnings=kwargs.get("days_to_earnings"),
        material_risk_count=int(kwargs.get("material_risk_count") or 0),
        previous_score=kwargs.get("previous_score"),
        previous_decision=kwargs.get("previous_decision"),
        asset_type=str(kwargs.get("asset_type") or "Stock"),
    ))


def counterfactual_attribution(pillar_scores, completeness, evidence, as_of, multipliers, decision_kwargs):
    learned = adjusted_evidence(evidence, multipliers)
    result = engine.aggregate_score(pillar_scores, completeness, learned, as_of)
    decision = _decision(result, decision_kwargs)
    rows = []
    for index, item in enumerate(evidence):
        reduced = [value for i, value in enumerate(learned) if i != index]
        alternative = engine.aggregate_score(pillar_scores, completeness, reduced, as_of)
        alternative_decision = _decision(alternative, decision_kwargs)
        marginal = _num(result.get("score")) - _num(alternative.get("score"))
        pivotal = alternative_decision.code != decision.code
        importance = min(1.0, 0.15 + abs(marginal) / 8.0 + (0.45 if pivotal else 0.0))
        rows.append({
            "evidence_id": f"{item.code}:{index}",
            "code": item.code,
            "pillar": item.pillar,
            "direction": int(item.direction),
            "marginal_score": round(marginal, 6),
            "decision_without": alternative_decision.code,
            "decision_changed": pivotal,
            "importance": round(importance, 6),
        })
    return result, decision, rows


def decision_record(week_id, position, result, decision, attribution, evidence, evidence_learning, benchmark_price, regime, as_of, model_version):
    decision_id = f"{week_id}:{position.get('id')}:{model_version}"
    evidence_rows = []
    for index, item in enumerate(evidence):
        row = item.to_dict(as_of)
        row["evidence_id"] = f"{item.code}:{index}"
        row["adaptive_learning"] = dict(evidence_learning.get(item.code) or {})
        evidence_rows.append(row)
    return {
        "decision_id": decision_id,
        "week_id": week_id,
        "review_date": as_of.isoformat(),
        "published_at": _now(),
        "model_version": model_version,
        "id": position.get("id"),
        "broker_symbol": position.get("broker_symbol"),
        "label": position.get("label"),
        "asset_type": position.get("asset_type"),
        "market_regime": regime,
        "decision": decision.code,
        "urgency": decision.urgency,
        "reason_codes": list(decision.reason_codes),
        "score": result.get("score"),
        "confidence": result.get("confidence"),
        "pillar_scores": result.get("pillar_scores"),
        "contradictions": result.get("contradictions"),
        "reference_price": position.get("market_price"),
        "reference_benchmark_price": round(float(benchmark_price), 6),
        "evidence": evidence_rows,
        "counterfactuals": [dict(item) for item in attribution],
        "immutable": True,
    }


def append_decision(memory, record):
    decision_id = str(record.get("decision_id") or "")
    if not decision_id:
        raise ValueError("decision_id is required")
    if decision_id in {str(item.get("decision_id")) for item in memory.setdefault("decisions", [])}:
        return False
    memory["decisions"].append(dict(record))
    return True


def evaluate_due_outcomes(memory, current_prices, benchmark_price, as_of):
    if not benchmark_price or benchmark_price <= 0:
        return []
    existing = {str(item.get("outcome_event_id")) for item in memory.setdefault("outcome_events", [])}
    created = []
    for record in memory.get("decisions", []) or []:
        decision_id, position_id = str(record.get("decision_id") or ""), str(record.get("id") or "")
        ref_price, ref_benchmark = _num(record.get("reference_price")), _num(record.get("reference_benchmark_price"))
        current_price, review_date = _num(current_prices.get(position_id)), record.get("review_date")
        if not decision_id or not position_id or not review_date or min(ref_price, ref_benchmark, current_price) <= 0:
            continue
        elapsed = (as_of - _date(review_date)).days
        counterfactuals = {str(item.get("evidence_id")): item for item in record.get("counterfactuals", []) or []}
        for horizon in HORIZONS_DAYS:
            event_id = f"{decision_id}:{horizon}d"
            if elapsed < horizon or event_id in existing:
                continue
            asset_return = current_price / ref_price - 1.0
            benchmark_return = float(benchmark_price) / ref_benchmark - 1.0
            excess = asset_return - benchmark_return
            attributions = []
            for item in record.get("evidence", []) or []:
                direction = int(item.get("direction") or 0)
                if direction == 0:
                    continue
                evidence_id = str(item.get("evidence_id") or "")
                cf = counterfactuals.get(evidence_id) or {}
                importance = max(0.0, min(1.0, _num(cf.get("importance"), 0.15)))
                neutral = abs(excess) < EXCESS_RETURN_DEADBAND
                attributions.append({
                    "evidence_id": evidence_id,
                    "code": item.get("code"),
                    "pillar": item.get("pillar"),
                    "direction": direction,
                    "signal_correct": bool(direction * excess > 0) if not neutral else False,
                    "neutral_outcome": neutral,
                    "credit": round(importance * HORIZON_WEIGHTS[horizon], 6),
                    "importance": round(importance, 6),
                    "decision_changed_without_evidence": bool(cf.get("decision_changed")),
                    "marginal_score": cf.get("marginal_score"),
                })
            event = {
                "outcome_event_id": event_id,
                "decision_id": decision_id,
                "evaluated_at": as_of.isoformat(),
                "horizon_days": horizon,
                "id": position_id,
                "broker_symbol": record.get("broker_symbol"),
                "asset_type": record.get("asset_type"),
                "market_regime": record.get("market_regime"),
                "decision": record.get("decision"),
                "asset_return": round(asset_return, 6),
                "benchmark_return": round(benchmark_return, 6),
                "excess_return": round(excess, 6),
                "decision_outcome": engine.score_outcome(str(record.get("decision") or ""), excess),
                "evidence_attribution": attributions,
                "append_only": True,
            }
            memory["outcome_events"].append(event)
            existing.add(event_id)
            created.append(event)
    return created


def compatibility_history(memory, limit=416):
    decisions = list(memory.get("decisions", []) or [])[-max(1, int(limit)):]
    events = {}
    for event in memory.get("outcome_events", []) or []:
        events.setdefault(str(event.get("decision_id")), []).append(event)
    output = []
    for record in decisions:
        row = {
            "week_id": record.get("week_id"),
            "review_date": record.get("review_date"),
            "id": record.get("id"),
            "broker_symbol": record.get("broker_symbol"),
            "decision": record.get("decision"),
            "score": record.get("score"),
            "confidence": record.get("confidence"),
            "reference_price": record.get("reference_price"),
            "reference_benchmark_price": record.get("reference_benchmark_price"),
            "decision_quality_at_publication": {
                "transparent_rules_passed": True,
                "contradictions_disclosed": bool(record.get("contradictions")),
                "immutable_record": True,
            },
        }
        for event in events.get(str(record.get("decision_id")), []):
            row[f"outcome_{event.get('horizon_days')}d"] = {
                **(event.get("decision_outcome") or {}),
                "asset_return": event.get("asset_return"),
                "benchmark_return": event.get("benchmark_return"),
                "evaluated_at": event.get("evaluated_at"),
            }
        output.append(row)
    return output


def learning_summary(memory):
    global_items = ((memory.get("reliability") or {}).get("global") or {})
    ranked = [{"code": code, **dict(item)} for code, item in global_items.items() if _num(item.get("effective_samples")) > 0]
    ranked.sort(key=lambda item: (bool(item.get("active")), _num(item.get("posterior_mean")), _num(item.get("effective_samples"))), reverse=True)
    weak = sorted(ranked, key=lambda item: (_num(item.get("posterior_mean"), 0.5), -_num(item.get("effective_samples"))))
    horizons = {}
    for event in memory.get("outcome_events", []) or []:
        key = f"{event.get('horizon_days')}d"
        horizons[key] = horizons.get(key, 0) + 1
    return {
        "mode": "bounded_bayesian_adaptive_evidence",
        "status": "learning" if memory.get("outcome_events") else "collecting_memory",
        "decisions_stored": len(memory.get("decisions", []) or []),
        "outcome_events_stored": len(memory.get("outcome_events", []) or []),
        "evaluated_horizons": horizons,
        "signals_tracked": len(global_items),
        "active_multipliers": sum(1 for item in global_items.values() if item.get("active")),
        "minimum_effective_samples": MIN_EFFECTIVE_SAMPLES,
        "multiplier_bounds": [MIN_MULTIPLIER, MAX_MULTIPLIER],
        "applies_next_review": True,
        "top_reliable_signals": ranked[:5],
        "signals_needing_review": weak[:5],
        "objective": memory.get("objective"),
        "governance_pl": "Współczynniki uczą się bayesowsko, lecz pozostają neutralne do czasu zebrania minimalnej próby. Są ograniczone do zakresu 0,80–1,20 i nie zmieniają samodzielnie wag filarów ani progów decyzji.",
        "governance_en": "Reliability coefficients update with Bayesian shrinkage but remain neutral until the minimum sample is reached. They are bounded to 0.80–1.20 and never self-modify pillar weights or decision thresholds.",
    }
