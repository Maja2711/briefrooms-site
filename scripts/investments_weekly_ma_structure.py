#!/usr/bin/env python3
"""Governed EUR/USD MA30/60/100/200 structure context.

Research promoted only as a bounded context adjustment for weekly paper trading.
W1 is structural support/resistance, D1 is price-location confirmation, H1 is
execution timing. H4 and M1 remain research/background and have no veto power.
Never sends broker orders.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd
import yfinance as yf

from instrument_registry import canonical_vendor_symbol

INSTRUMENT_ID = "eurusd"
SYMBOL = canonical_vendor_symbol(INSTRUMENT_ID, "yahoo")
TZ = "Europe/Warsaw"
WINDOWS = (30, 60, 100, 200)


def _cfg(policy: Dict[str, Any]) -> Dict[str, Any]:
    value = policy.get("eurusd_ma_structure")
    return value if isinstance(value, dict) else {}


def _series(df: pd.DataFrame, name: str) -> pd.Series:
    value = df[name]
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, 0]
    return value.astype(float)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = pd.DataFrame({k.lower(): _series(df, k) for k in ("Open", "High", "Low", "Close") if k in df}).dropna()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    out.index = idx.tz_convert(TZ)
    return out[~out.index.duplicated(keep="last")].sort_index()


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()


def _features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for window in WINDOWS:
        out[f"ma{window}"] = out["close"].rolling(window).mean()
    prev = out["close"].shift(1)
    tr = pd.concat([
        out["high"] - out["low"],
        (out["high"] - prev).abs(),
        (out["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    out["atr14"] = tr.rolling(14).mean()
    return out.dropna()


def _snapshot(df: pd.DataFrame, before: pd.Timestamp) -> Optional[Dict[str, Any]]:
    eligible = df[df.index < before]
    if eligible.empty:
        return None
    row = eligible.iloc[-1]
    p = float(row.close); atr = max(float(row.atr14), 1e-12)
    mas = {name: float(row[name]) for name in ("ma30", "ma60", "ma100", "ma200")}
    nearest_name, nearest_value = min(mas.items(), key=lambda kv: abs(p - kv[1]))
    distance = abs(p - nearest_value) / atr
    slow = 1 if mas["ma100"] > mas["ma200"] else -1 if mas["ma100"] < mas["ma200"] else 0
    price_all = 1 if p > max(mas.values()) else -1 if p < min(mas.values()) else 0
    support_long = bool(slow == 1 and p >= nearest_value and distance <= 0.35 and float(row.low) <= nearest_value + 0.10 * atr)
    resistance_short = bool(slow == -1 and p <= nearest_value and distance <= 0.35 and float(row.high) >= nearest_value - 0.10 * atr)
    return {
        "bar_at": eligible.index[-1].isoformat(), "close": round(p, 6), **{k: round(v, 6) for k, v in mas.items()},
        "slow_trend": slow, "price_vs_all": price_all, "nearest_ma": nearest_name,
        "nearest_ma_distance_atr": round(distance, 4), "support_hold_long": support_long,
        "resistance_hold_short": resistance_short,
    }


def context(instrument_id: str, now: datetime, policy: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _cfg(policy)
    if instrument_id != INSTRUMENT_ID or not cfg.get("enabled", False):
        return {"enabled": False, "instrument_id": instrument_id, "data_quality": "not_applicable", "score": 0.0}
    try:
        h1 = _clean(yf.download(SYMBOL, period="60d", interval="1h", progress=False, auto_adjust=False, prepost=True, threads=False))
        d1 = _clean(yf.download(SYMBOL, period="5y", interval="1d", progress=False, auto_adjust=False, prepost=True, threads=False))
        if h1.empty or d1.empty:
            raise RuntimeError("required_price_history_unavailable")
        ts = pd.Timestamp(now)
        if ts.tzinfo is None:
            ts = ts.tz_localize(TZ)
        else:
            ts = ts.tz_convert(TZ)
        h1s = _snapshot(_features(h1), ts)
        d1s = _snapshot(_features(d1), ts)
        w1s = _snapshot(_features(_resample(d1, "W-FRI")), ts)
        if not h1s or not d1s or not w1s:
            raise RuntimeError("insufficient_ma_history")

        weights = cfg.get("weights") if isinstance(cfg.get("weights"), dict) else {}
        w1_long = float(weights.get("w1_support_long") or 1.0)
        w1_short = float(weights.get("w1_resistance_short") or 2.5)
        d1_weight = float(weights.get("d1_price_location") or 1.0)
        h1_weight = float(weights.get("h1_timing") or 0.5)
        score = 0.0
        reasons = []
        if w1s["support_hold_long"]:
            score += w1_long; reasons.append("w1_support_hold_long")
        if w1s["resistance_hold_short"]:
            score -= w1_short; reasons.append("w1_resistance_hold_short")
        if d1s["price_vs_all"] == 1:
            score += d1_weight; reasons.append("d1_above_all_ma")
        elif d1s["price_vs_all"] == -1:
            score -= d1_weight; reasons.append("d1_below_all_ma")
        if h1s["price_vs_all"] == 1:
            score += h1_weight; reasons.append("h1_above_all_ma_timing")
        elif h1s["price_vs_all"] == -1:
            score -= h1_weight; reasons.append("h1_below_all_ma_timing")
        cap = abs(float(cfg.get("score_cap") or 4.0))
        score = max(-cap, min(cap, score))
        return {
            "enabled": True, "instrument_id": instrument_id, "data_quality": "passed",
            "score": round(score, 4), "score_cap": cap,
            "direction": "long" if score > 0 else "short" if score < 0 else "neutral",
            "components": {"W1": w1s, "D1": d1s, "H1": h1s}, "reasons": reasons,
            "evaluated_at": now.isoformat(timespec="seconds"),
            "governance": "bounded_context_only_no_veto_no_standalone_trade_trigger",
        }
    except Exception as exc:
        return {"enabled": True, "instrument_id": instrument_id, "data_quality": "failed", "score": 0.0,
                "reason": type(exc).__name__, "evaluated_at": now.isoformat(timespec="seconds")}


def apply_to_candidates(instrument_id: str, candidates: Dict[str, Dict[str, Any]], ma_context: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = {key: dict(value) for key, value in candidates.items()}
    if instrument_id != INSTRUMENT_ID or ma_context.get("data_quality") != "passed":
        return rows
    cfg = _cfg(policy)
    cap = abs(float(ma_context.get("score_cap") or cfg.get("score_cap") or 4.0)) or 4.0
    score = max(-cap, min(cap, float(ma_context.get("score") or 0.0)))
    max_adjust = abs(float(cfg.get("candidate_alignment_bonus_max") or 3.0))
    for row in rows.values():
        side = 1.0 if row.get("direction") == "long" else -1.0 if row.get("direction") == "short" else 0.0
        adjustment = max_adjust * side * score / cap
        base = float(row.get("conviction") or 0.0)
        row["ma_structure_base_conviction"] = round(base, 4)
        row["ma_structure_adjustment"] = round(adjustment, 4)
        row["ma_structure_score"] = round(score, 4)
        row["conviction"] = round(max(0.0, base + adjustment), 4)
    return rows
