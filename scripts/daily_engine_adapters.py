#!/usr/bin/env python3
"""Normalize existing Daily Trading engines to the canonical output contract.

This is an anti-corruption layer: GPW and US engines keep their established
payloads, hard gates, evidence logic and isolated learning histories. The
Belief Bridge can consume this normalized shape instead of learning every
market-specific schema.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from daily_engine_contract import DailyEngineOutput


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _reference_price(selection: Mapping[str, Any]) -> float | None:
    direct = _number(selection.get("reference_price"))
    if direct is not None:
        return direct
    zone = selection.get("entry_zone")
    if isinstance(zone, (list, tuple)) and len(zone) >= 2:
        low, high = _number(zone[0]), _number(zone[1])
        if low is not None and high is not None:
            return (low + high) / 2.0
    return None


def _decision_strength(score: float, directional: bool) -> float:
    """Decision strength, not a calibrated event probability."""
    if not directional:
        return 0.0
    return round(min(0.95, max(0.0, abs(float(score) - 50.0) / 50.0)), 3)


def normalize_daily_stock_payload(
    payload: Mapping[str, Any],
    market: str,
    *,
    decision_mode: str = "WITHOUT",
) -> DailyEngineOutput:
    """Map existing GPW/US Daily Stock payloads without changing their engines."""
    market_key = market.strip().upper()
    if market_key not in {"GPW", "US"}:
        raise ValueError("market must be GPW or US")

    selection = payload.get("selection") or {}
    if not isinstance(selection, Mapping):
        selection = {}
    decision = str(payload.get("decision") or "UNKNOWN")
    is_trade = decision == ("TRANSAKCJA" if market_key == "GPW" else "TRADE")
    direction = "LONG" if is_trade else "FLAT"

    selection_score = _number(selection.get("score"))
    score = 50.0 if selection_score is None else max(0.0, min(100.0, selection_score))
    entry = _reference_price(selection) if is_trade else None
    stop = _number(selection.get("stop")) if is_trade else None
    target = _number(selection.get("target")) if is_trade else None

    # Fail closed if a source payload claims a trade but lacks a valid plan.
    if is_trade and None in {entry, stop, target}:
        raise ValueError(f"{market_key} trade payload lacks entry/stop/target")

    ticker = str(selection.get("ticker") or selection.get("symbol") or market_key)
    generated_at = str(payload.get("generated_at") or payload.get("date") or "")
    if not generated_at:
        raise ValueError(f"{market_key} payload lacks generated_at/date")

    methodology = payload.get("methodology") or {}
    if not isinstance(methodology, Mapping):
        methodology = {}
    core = methodology.get("daily_stock_core") or {}
    horizon = str(methodology.get("horizon") or "1-2 sessions")
    engine_version = str(payload.get("policy_version") or core.get("core") or f"{market_key.lower()}-daily")

    return DailyEngineOutput(
        instrument=ticker,
        timestamp=generated_at,
        direction=direction,
        score=round(score, 2),
        confidence=_decision_strength(score, is_trade),
        entry=None if entry is None else round(entry, 5),
        stop=None if stop is None else round(stop, 5),
        target=None if target is None else round(target, 5),
        horizon=horizon,
        engine_version=engine_version,
        status=decision,
        decision_mode=decision_mode,
        metadata={
            "market": market_key,
            "source_schema_version": payload.get("schema_version"),
            "source_decision": decision,
            "source_reason": payload.get("reason"),
            "daily_stock_core": core.get("core") if isinstance(core, Mapping) else None,
            "market_memory_isolated": True,
            "source_hard_gates_preserved": True,
            "confidence_semantics": "decision_strength_not_calibrated_probability",
            "belief": {
                "mode": decision_mode,
                "normalized_only": True,
                "decision_influence": decision_mode == "WITH",
            },
        },
    ).validate()
