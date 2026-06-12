#!/usr/bin/env python3
"""Dynamic volatility thresholds for BriefRooms educational market scenarios."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

try:
    import yfinance as yf
except Exception:
    yf = None


def safe_float(value: Any) -> Optional[float]:
    try:
        f = float(value)
        if math.isfinite(f):
            return f
    except Exception:
        return None
    return None


def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _series_values(series: Any) -> List[float]:
    values = [safe_float(x) for x in series.dropna().tolist()]
    return [x for x in values if x is not None]


def _select_column(df: Any, name: str) -> Optional[Any]:
    if df is None or df.empty:
        return None
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        if name not in df.columns.get_level_values(0):
            return None
        obj = df[name]
        return obj.iloc[:, 0] if hasattr(obj, "columns") else obj
    if name not in df.columns:
        return None
    return df[name]


def download_ohlc_series(symbol: str, period: str, interval: str) -> Dict[str, List[float]]:
    if yf is None:
        return {"high": [], "low": [], "close": []}
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False, threads=False)
        high = _select_column(df, "High")
        low = _select_column(df, "Low")
        close = _select_column(df, "Close")
        if high is None or low is None or close is None:
            return {"high": [], "low": [], "close": []}
        return {"high": _series_values(high), "low": _series_values(low), "close": _series_values(close)}
    except Exception:
        return {"high": [], "low": [], "close": []}


def average_true_range(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    count = min(len(highs), len(lows), len(closes))
    if count < period + 1:
        return None
    highs = highs[-count:]
    lows = lows[-count:]
    closes = closes[-count:]
    ranges: List[float] = []
    for i in range(1, count):
        ranges.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    recent = ranges[-period:]
    return sum(recent) / len(recent) if recent else None


def calculate_dynamic_thresholds(inst: Dict[str, Any], method: Dict[str, Any]) -> Dict[str, Any]:
    model = method.get("volatility_threshold_model", {})
    inst_id = inst.get("id") or inst.get("instrument_id")
    unit_size = safe_float(inst.get("pip_size")) or safe_float(inst.get("point_size")) or 1.0
    unit_label = inst.get("result_unit") or ("pips" if inst_id == "eurusd" else "points")
    fallback = model.get("fallback_static_thresholds", {}).get(inst_id, {})
    fallback_up = safe_float(fallback.get("favorable_units"))
    fallback_down = safe_float(fallback.get("adverse_units"))

    if not model.get("enabled") or yf is None:
        return {"source": "static_fallback", "model": "static_fallback", "favorable_units": fallback_up, "adverse_units": fallback_down, "unit": unit_label}

    period = str(model.get("price_history_period", "6mo"))
    interval = str(model.get("price_history_interval", "1d"))
    ohlc = download_ohlc_series(str(inst.get("symbol")), period=period, interval=interval)
    atr = average_true_range(ohlc.get("high", []), ohlc.get("low", []), ohlc.get("close", []), 14)
    if atr is None:
        return {"source": "static_fallback_no_atr", "model": "static_fallback", "favorable_units": fallback_up, "adverse_units": fallback_down, "unit": unit_label}

    expected_week_move = atr * math.sqrt(5)
    up_multiplier = safe_float(model.get("favorable_multiplier")) or 0.9
    down_multiplier = safe_float(model.get("adverse_multiplier")) or 0.6
    raw_up_units = (expected_week_move * up_multiplier) / unit_size
    raw_down_units = (expected_week_move * down_multiplier) / unit_size

    limits = model.get("unit_limits", {}).get(inst_id, {})
    min_up = safe_float(limits.get("min_favorable_units")) or raw_up_units
    max_up = safe_float(limits.get("max_favorable_units")) or raw_up_units
    min_down = safe_float(limits.get("min_adverse_units")) or raw_down_units
    max_down = safe_float(limits.get("max_adverse_units")) or raw_down_units
    up_units = clip(raw_up_units, min_up, max_up)
    down_units = clip(raw_down_units, min_down, max_down)

    return {
        "source": "dynamic_atr14",
        "model": "ATR14",
        "period": period,
        "interval": interval,
        "formula": model.get("expected_week_move_formula", "ATR14 * sqrt(5)"),
        "atr14_price": round(atr, 6),
        "expected_week_move_price": round(expected_week_move, 6),
        "favorable_multiplier": up_multiplier,
        "adverse_multiplier": down_multiplier,
        "favorable_units_raw": round(raw_up_units, 2),
        "adverse_units_raw": round(raw_down_units, 2),
        "favorable_units": round(up_units, 2),
        "adverse_units": round(down_units, 2),
        "unit": unit_label,
        "minmax_applied": bool(round(raw_up_units, 2) != round(up_units, 2) or round(raw_down_units, 2) != round(down_units, 2))
    }
