#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Price-based macro and MA structure context for the weekly paper model."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import investments_weekly_v2 as v2
import investments_weekly_ma_structure as ma_structure
import investments_research_bridge as research_bridge
from instrument_registry import canonical_vendor_symbol


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _macro_cfg(policy: Dict[str, Any]) -> Dict[str, Any]:
    value = policy.get("macro_context")
    return value if isinstance(value, dict) else {}


def _canonical_macro_symbols(cfg: Dict[str, Any]) -> Tuple[str, str]:
    """Resolve WTI and US10Y from canonical IDs; legacy fields are drift assertions."""
    oil_symbol = canonical_vendor_symbol(
        "wti_futures",
        "yahoo",
        configured_symbol=str(cfg.get("oil_symbol") or "CL=F"),
    )
    yield_symbol = canonical_vendor_symbol(
        "us10y_yield",
        "yahoo",
        configured_symbol=str(cfg.get("us10y_symbol") or "^TNX"),
    )
    return oil_symbol, yield_symbol


def _close_series(symbol: str, period: str = "2y") -> Tuple[Optional[List[float]], Optional[str], Optional[str]]:
    try:
        frame = v2.download_daily(symbol, period=period)
        if frame is None:
            return None, None, "download_failed"
        series = v2._series(frame, "Close").dropna().astype(float)
        if len(series) < 22:
            return None, None, "insufficient_daily_history"
        values = [float(value) for value in series.tolist()]
        index_value = series.index[-1]
        if hasattr(index_value, "to_pydatetime"):
            index_value = index_value.to_pydatetime()
        as_of = index_value.isoformat() if hasattr(index_value, "isoformat") else str(index_value)
        return values, as_of, None
    except Exception as exc:
        return None, None, f"{type(exc).__name__}"


def _return(values: List[float], sessions: int) -> float:
    if len(values) <= sessions or values[-sessions - 1] == 0:
        return 0.0
    return values[-1] / values[-sessions - 1] - 1.0


def _change_bps(values: List[float], sessions: int) -> float:
    if len(values) <= sessions:
        return 0.0
    return (values[-1] - values[-sessions - 1]) * 100.0


def score_from_observations(oil_1w_percent: float, oil_4w_percent: float, us10y_1w_bps: float, us10y_4w_bps: float, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or {}
    weights = cfg.get("weights") if isinstance(cfg.get("weights"), dict) else {}
    scales = cfg.get("normalization") if isinstance(cfg.get("normalization"), dict) else {}
    oil_1w = -float(weights.get("oil_1w") or 10.0) * clip(oil_1w_percent / float(scales.get("oil_1w_percent") or 8.0), -1.0, 1.0)
    oil_4w = -float(weights.get("oil_4w") or 5.0) * clip(oil_4w_percent / float(scales.get("oil_4w_percent") or 20.0), -1.0, 1.0)
    yield_1w = -float(weights.get("us10y_1w") or 10.0) * clip(us10y_1w_bps / float(scales.get("us10y_1w_bps") or 20.0), -1.0, 1.0)
    yield_4w = -float(weights.get("us10y_4w") or 5.0) * clip(us10y_4w_bps / float(scales.get("us10y_4w_bps") or 50.0), -1.0, 1.0)
    cap = abs(float(cfg.get("score_cap") or 30.0))
    score = clip(oil_1w + oil_4w + yield_1w + yield_4w, -cap, cap)
    return {"score": round(score, 4), "score_cap": cap, "components": {"oil_1w": round(oil_1w, 4), "oil_4w": round(oil_4w, 4), "us10y_1w": round(yield_1w, 4), "us10y_4w": round(yield_4w, 4)}}


def normalized_score(context: Dict[str, Any]) -> float:
    if context.get("data_quality") != "passed":
        return 0.0
    cap = abs(float(context.get("score_cap") or 30.0))
    return 0.0 if cap <= 0 else clip(float(context.get("score") or 0.0) / cap * 100.0, -100.0, 100.0)


def context(instrument_id: str, now: datetime, policy: Dict[str, Any]) -> Dict[str, Any]:
    ma_context = ma_structure.context(instrument_id, now, policy)
    cfg = _macro_cfg(policy)
    enabled_for = set(str(value) for value in cfg.get("applies_to") or ["eurusd"])
    if not cfg.get("enabled", True) or instrument_id not in enabled_for:
        return {"enabled": False, "instrument_id": instrument_id, "data_quality": "not_applicable", "score": 0.0, "ma_structure": ma_context}
    oil_symbol, yield_symbol = _canonical_macro_symbols(cfg)
    oil, oil_as_of, oil_error = _close_series(oil_symbol)
    us10y, yield_as_of, yield_error = _close_series(yield_symbol)
    require_both = bool(cfg.get("require_both_sources", True))
    if (require_both and (oil is None or us10y is None)) or (oil is None and us10y is None):
        return {"enabled": True, "instrument_id": instrument_id, "data_quality": "failed", "score": 0.0, "reason": "required_macro_source_unavailable", "errors": {"oil": oil_error, "us10y": yield_error}, "evaluated_at": now.isoformat(timespec="seconds"), "ma_structure": ma_context}
    oil = oil or [0.0] * 22
    us10y = us10y or [0.0] * 22
    observations = {"oil_1w_percent": round(_return(oil, 5) * 100.0, 4), "oil_4w_percent": round(_return(oil, 20) * 100.0, 4), "us10y_1w_bps": round(_change_bps(us10y, 5), 4), "us10y_4w_bps": round(_change_bps(us10y, 20), 4)}
    scored = score_from_observations(**observations, cfg=cfg)
    score = float(scored["score"])
    return {"enabled": True, "instrument_id": instrument_id, "data_quality": "passed", "direction": "long" if score > 0 else "short" if score < 0 else "neutral", **scored, "observations": observations, "sources": {"oil": {"instrument_id": "wti_futures", "symbol": oil_symbol, "as_of": oil_as_of, "error": oil_error}, "us10y": {"instrument_id": "us10y_yield", "symbol": yield_symbol, "as_of": yield_as_of, "error": yield_error}}, "as_of": max(str(oil_as_of or ""), str(yield_as_of or "")), "evaluated_at": now.isoformat(timespec="seconds"), "interpretation": "positive_supports_eurusd_long_negative_supports_eurusd_short", "rule": "Oil and US10Y are context inputs, not standalone trade triggers.", "ma_structure": ma_context}


def apply_to_candidates(instrument_id: str, candidates: Dict[str, Dict[str, Any]], fresh: Dict[str, Any], weekly: Dict[str, Any], macro_context: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = {key: dict(value) for key, value in candidates.items()}
    if instrument_id != "eurusd":
        return rows
    cfg = _macro_cfg(policy)
    if macro_context.get("data_quality") == "passed":
        score = float(macro_context.get("score") or 0.0)
        cap = abs(float(macro_context.get("score_cap") or cfg.get("score_cap") or 30.0)) or 30.0
        bonus_max = abs(float(cfg.get("candidate_alignment_bonus_max") or 6.0))
        for row in rows.values():
            side = 1.0 if row.get("direction") == "long" else -1.0 if row.get("direction") == "short" else 0.0
            adjustment = bonus_max * side * score / cap
            base = float(row.get("conviction") or 0.0)
            row["base_conviction"] = round(base, 4)
            row["macro_alignment_adjustment"] = round(adjustment, 4)
            row["macro_context_score"] = round(score, 4)
            row["conviction"] = round(max(0.0, base + adjustment), 4)
        blend = cfg.get("blend_weights") if isinstance(cfg.get("blend_weights"), dict) else {}
        daily_weight = float(blend.get("daily") or 0.35); weekly_weight = float(blend.get("weekly") or 0.40); macro_weight = float(blend.get("macro") or 0.25)
        daily_score = float(fresh.get("score") or 0.0); weekly_score = float(weekly.get("score") or 0.0) if weekly.get("data_quality") == "passed" else 0.0; macro_scaled = normalized_score(macro_context)
        combined = clip(daily_weight * daily_score + weekly_weight * weekly_score + macro_weight * macro_scaled, -100.0, 100.0)
        tie = str(next((row.get("default_tie_direction") for row in policy.get("instruments") or [] if row.get("instrument_id") == instrument_id), "long"))
        direction = "long" if combined > 0 else "short" if combined < 0 else (tie if tie in {"long", "short"} else "long")
        rows["macro_weekly_blend"] = {"direction": direction, "raw_score": round(combined, 4), "conviction": round(abs(combined) * 0.15, 4), "base_conviction": round(abs(combined) * 0.15, 4), "macro_alignment_adjustment": 0.0, "macro_context_score": round(score, 4), "inputs": {"daily_score": daily_score, "weekly_score": weekly_score, "macro_score_normalized": round(macro_scaled, 4), "weights": {"daily": daily_weight, "weekly": weekly_weight, "macro": macro_weight}}}
    rows = ma_structure.apply_to_candidates(instrument_id, rows, macro_context.get("ma_structure") or {}, policy)
    rows, bridge = research_bridge.apply(instrument_id, rows)
    for row in rows.values():
        row["research_lab_bridge"] = bridge
    return rows


def position_review(item: Dict[str, Any], macro_context: Dict[str, Any]) -> Dict[str, Any]:
    score = float(macro_context.get("score") or 0.0)
    side = str(item.get("direction") or "neutral")
    aligned = (side == "long" and score >= 0) or (side == "short" and score <= 0) or score == 0
    return {"as_of": macro_context.get("as_of"), "data_quality": macro_context.get("data_quality"), "macro_score": round(score, 4), "macro_direction": macro_context.get("direction"), "position_direction": side, "aligned": aligned, "observations": macro_context.get("observations"), "ma_structure": macro_context.get("ma_structure"), "rule": "Context is recorded immediately; an open weekly position is changed only by the governed review rules."}
