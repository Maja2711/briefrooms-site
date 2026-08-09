#!/usr/bin/env python3
"""Research-only EUR/USD MA30/60/100/200 multi-timeframe study for weekly positions.

Timeframes: H1, H4, D1, W1, M1. The study separates:
- long-history standalone context for D1/W1/M1;
- a common recent sample for H1/H4/D1/W1/M1 and multi-timeframe combinations;
- trend confirmation, trigger, support/resistance hold and timeframe agreement.

Signals use only bars completed before the Monday weekly entry timestamp. Outcomes use
Monday-to-Friday hourly prices. This script never changes production signals or sends orders.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import fmean, median
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/investments/eurusd_ma_multitimeframe_research.json"
SYMBOL = "EURUSD=X"
TZ = "Europe/Warsaw"
WINDOWS = (30, 60, 100, 200)


def _series(df: pd.DataFrame, name: str) -> pd.Series:
    value = df[name]
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, 0]
    return value.astype(float)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = pd.DataFrame({k.lower(): _series(df, k) for k in ("Open", "High", "Low", "Close") if k in df})
    out = out.dropna().copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    out.index = idx.tz_convert(TZ)
    return out[~out.index.duplicated(keep="last")].sort_index()


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for w in WINDOWS:
        out[f"ma{w}"] = out["close"].rolling(w).mean()
    prev_close = out["close"].shift(1)
    tr = pd.concat([
        out["high"] - out["low"],
        (out["high"] - prev_close).abs(),
        (out["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["atr14"] = tr.rolling(14).mean()
    out["ma30_prev"] = out["ma30"].shift(1)
    out["ma60_prev"] = out["ma60"].shift(1)
    out["close_prev"] = out["close"].shift(1)
    return out.dropna()


def row_features(row: pd.Series) -> Dict[str, Any]:
    p = float(row.close); m30 = float(row.ma30); m60 = float(row.ma60); m100 = float(row.ma100); m200 = float(row.ma200)
    atr = max(float(row.atr14), 1e-12)
    stack = 1 if m30 > m60 > m100 > m200 else -1 if m30 < m60 < m100 < m200 else 0
    fast = 1 if m30 > m60 else -1 if m30 < m60 else 0
    slow = 1 if m100 > m200 else -1 if m100 < m200 else 0
    price_all = 1 if p > max(m30, m60, m100, m200) else -1 if p < min(m30, m60, m100, m200) else 0
    nearest = min((abs(p - ma), name, ma) for name, ma in [("ma30", m30), ("ma60", m60), ("ma100", m100), ("ma200", m200)])
    support_long = bool(slow == 1 and p >= nearest[2] and nearest[0] <= 0.35 * atr and float(row.low) <= nearest[2] + 0.10 * atr)
    resistance_short = bool(slow == -1 and p <= nearest[2] and nearest[0] <= 0.35 * atr and float(row.high) >= nearest[2] - 0.10 * atr)
    reclaim30_long = bool(float(row.close_prev) <= float(row.ma30_prev) and p > m30 and slow == 1)
    reclaim30_short = bool(float(row.close_prev) >= float(row.ma30_prev) and p < m30 and slow == -1)
    cross3060_long = bool(float(row.ma30_prev) <= float(row.ma60_prev) and m30 > m60)
    cross3060_short = bool(float(row.ma30_prev) >= float(row.ma60_prev) and m30 < m60)
    return {
        "stack": stack, "fast": fast, "slow": slow, "price_all": price_all,
        "support_hold_long": support_long, "resistance_hold_short": resistance_short,
        "reclaim_ma30_long": reclaim30_long, "reclaim_ma30_short": reclaim30_short,
        "cross_30_60_long": cross3060_long, "cross_30_60_short": cross3060_short,
        "nearest_ma": nearest[1], "nearest_ma_distance_atr": round(nearest[0] / atr, 6),
    }


def asof_features(df: pd.DataFrame, ts: pd.Timestamp) -> Optional[Dict[str, Any]]:
    eligible = df[df.index < ts]
    if eligible.empty:
        return None
    row = eligible.iloc[-1]
    value = row_features(row)
    value["bar_at"] = eligible.index[-1].isoformat()
    return value


def signed_summary(returns: List[float], side: str) -> Dict[str, Any]:
    if not returns:
        return {"count": 0, "mean_week_percent": None, "median_week_percent": None, "hit_rate": None}
    signed = returns if side == "long" else [-x for x in returns]
    return {
        "count": len(signed),
        "mean_week_percent": round(fmean(signed), 6),
        "median_week_percent": round(median(signed), 6),
        "hit_rate": round(sum(1 for x in signed if x > 0) / len(signed), 6),
    }


def collect(records: List[Dict[str, Any]], predicate, side: str) -> Dict[str, Any]:
    vals = [float(r["return_pct"]) for r in records if predicate(r)]
    return signed_summary(vals, side)


def weekly_records(h1: pd.DataFrame, frames: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    if h1.empty:
        return []
    local_start = h1.index.min().normalize()
    local_end = h1.index.max().normalize()
    mondays = pd.date_range(local_start, local_end, freq="W-MON", tz=TZ)
    records: List[Dict[str, Any]] = []
    for day in mondays:
        entry_target = day + pd.Timedelta(hours=8)
        exit_target = day + pd.Timedelta(days=4, hours=22)
        entry_rows = h1[h1.index >= entry_target]
        exit_rows = h1[h1.index >= exit_target]
        if entry_rows.empty or exit_rows.empty:
            continue
        entry_ts = entry_rows.index[0]; exit_ts = exit_rows.index[0]
        if entry_ts > entry_target + pd.Timedelta(hours=3) or exit_ts > exit_target + pd.Timedelta(hours=4):
            continue
        entry = float(entry_rows.iloc[0].close); exitp = float(exit_rows.iloc[0].close)
        features = {name: asof_features(df, entry_ts) for name, df in frames.items()}
        if any(features.get(x) is None for x in ("H1", "H4", "D1", "W1", "M1")):
            continue
        records.append({
            "week": day.date().isoformat(), "entry_at": entry_ts.isoformat(), "exit_at": exit_ts.isoformat(),
            "entry": entry, "exit": exitp, "return_pct": (exitp / entry - 1.0) * 100.0,
            "tf": features,
        })
    return records


def tf_tests(records: List[Dict[str, Any]], tf: str) -> Dict[str, Any]:
    return {
        "full_stack": {
            "long": collect(records, lambda r: r["tf"][tf]["stack"] == 1, "long"),
            "short": collect(records, lambda r: r["tf"][tf]["stack"] == -1, "short"),
        },
        "fast_with_slow_filter": {
            "long": collect(records, lambda r: r["tf"][tf]["fast"] == 1 and r["tf"][tf]["slow"] == 1, "long"),
            "short": collect(records, lambda r: r["tf"][tf]["fast"] == -1 and r["tf"][tf]["slow"] == -1, "short"),
        },
        "price_vs_all": {
            "long": collect(records, lambda r: r["tf"][tf]["price_all"] == 1, "long"),
            "short": collect(records, lambda r: r["tf"][tf]["price_all"] == -1, "short"),
        },
        "support_or_resistance_hold": {
            "long": collect(records, lambda r: r["tf"][tf]["support_hold_long"], "long"),
            "short": collect(records, lambda r: r["tf"][tf]["resistance_hold_short"], "short"),
        },
        "ma30_reclaim_trigger": {
            "long": collect(records, lambda r: r["tf"][tf]["reclaim_ma30_long"], "long"),
            "short": collect(records, lambda r: r["tf"][tf]["reclaim_ma30_short"], "short"),
        },
        "ma30_60_cross_trigger": {
            "long": collect(records, lambda r: r["tf"][tf]["cross_30_60_long"], "long"),
            "short": collect(records, lambda r: r["tf"][tf]["cross_30_60_short"], "short"),
        },
    }


def combo_tests(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    def aligned(r, names, side, field="fast"):
        sign = 1 if side == "long" else -1
        return all(r["tf"][n][field] == sign for n in names)
    return {
        "D1_W1_trend_confirmation": {
            s: collect(records, lambda r, s=s: aligned(r, ["D1", "W1"], s, "fast") and aligned(r, ["D1", "W1"], s, "slow"), s)
            for s in ("long", "short")
        },
        "H4_trigger_D1_W1_trend": {
            "long": collect(records, lambda r: r["tf"]["H4"]["reclaim_ma30_long"] and aligned(r, ["D1", "W1"], "long", "slow"), "long"),
            "short": collect(records, lambda r: r["tf"]["H4"]["reclaim_ma30_short"] and aligned(r, ["D1", "W1"], "short", "slow"), "short"),
        },
        "H1_trigger_H4_D1_trend": {
            "long": collect(records, lambda r: r["tf"]["H1"]["reclaim_ma30_long"] and aligned(r, ["H4", "D1"], "long", "fast") and aligned(r, ["H4", "D1"], "long", "slow"), "long"),
            "short": collect(records, lambda r: r["tf"]["H1"]["reclaim_ma30_short"] and aligned(r, ["H4", "D1"], "short", "fast") and aligned(r, ["H4", "D1"], "short", "slow"), "short"),
        },
        "H4_support_D1_W1_trend": {
            "long": collect(records, lambda r: r["tf"]["H4"]["support_hold_long"] and aligned(r, ["D1", "W1"], "long", "slow"), "long"),
            "short": collect(records, lambda r: r["tf"]["H4"]["resistance_hold_short"] and aligned(r, ["D1", "W1"], "short", "slow"), "short"),
        },
        "H1_H4_D1_W1_unanimous_stack": {
            s: collect(records, lambda r, s=s: aligned(r, ["H1", "H4", "D1", "W1"], s, "stack"), s)
            for s in ("long", "short")
        },
        "D1_W1_with_M1_macro_filter": {
            s: collect(records, lambda r, s=s: aligned(r, ["D1", "W1"], s, "fast") and aligned(r, ["D1", "W1", "M1"], s, "slow"), s)
            for s in ("long", "short")
        },
        "H4_trigger_D1_W1_M1_confirmation": {
            "long": collect(records, lambda r: r["tf"]["H4"]["reclaim_ma30_long"] and aligned(r, ["D1", "W1", "M1"], "long", "slow"), "long"),
            "short": collect(records, lambda r: r["tf"]["H4"]["reclaim_ma30_short"] and aligned(r, ["D1", "W1", "M1"], "short", "slow"), "short"),
        },
    }


def long_history_tests(d1: pd.DataFrame) -> Dict[str, Any]:
    frames = {"D1": add_features(d1), "W1": add_features(resample_ohlc(d1, "W-FRI")), "M1": add_features(resample_ohlc(d1, "ME"))}
    out: Dict[str, Any] = {}
    for name, df in frames.items():
        rows = []
        step = 5 if name == "D1" else 1
        for i in range(0, len(df) - step, step):
            row = df.iloc[i]; future = df.iloc[i + step]
            rows.append({"return_pct": (float(future.close) / float(row.close) - 1.0) * 100.0, "tf": {name: row_features(row)}})
        out[name] = tf_tests(rows, name)
    return out


def rank_edges(tree: Dict[str, Any], prefix: str = "") -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key, value in tree.items():
        path = f"{prefix}/{key}" if prefix else key
        if isinstance(value, dict) and {"count", "mean_week_percent", "hit_rate"}.issubset(value):
            c = int(value.get("count") or 0); hit = value.get("hit_rate"); mean = value.get("mean_week_percent")
            if c >= 12 and hit is not None and mean is not None:
                score = (float(hit) - 0.5) * math.sqrt(c) + max(min(float(mean) / 0.15, 1.5), -1.5) * 0.25
                rows.append({"path": path, "count": c, "hit_rate": hit, "mean_week_percent": mean, "score": round(score, 6)})
        elif isinstance(value, dict):
            rows.extend(rank_edges(value, path))
    return sorted(rows, key=lambda x: x["score"], reverse=True)


def main() -> None:
    raw_h1 = yf.download(SYMBOL, period="720d", interval="1h", progress=False, auto_adjust=False, prepost=True, threads=False)
    raw_d1 = yf.download(SYMBOL, period="15y", interval="1d", progress=False, auto_adjust=False, threads=False)
    h1 = clean(raw_h1); d1 = clean(raw_d1)
    if h1.empty or d1.empty:
        raise SystemExit("EURUSD H1 or D1 history unavailable")
    h4 = resample_ohlc(h1, "4h")
    frames = {
        "H1": add_features(h1),
        "H4": add_features(h4),
        "D1": add_features(d1),
        "W1": add_features(resample_ohlc(d1, "W-FRI")),
        "M1": add_features(resample_ohlc(d1, "ME")),
    }
    records = weekly_records(frames["H1"], frames)
    standalone = {tf: tf_tests(records, tf) for tf in frames}
    combos = combo_tests(records)
    ranking = rank_edges({"standalone": standalone, "combinations": combos})
    payload = {
        "status": "completed", "research_only": True, "symbol": SYMBOL,
        "ma_windows": list(WINDOWS), "timeframes": ["H1", "H4", "D1", "W1", "M1"],
        "data_scope": {
            "H1_bars": len(h1), "H4_bars": len(h4), "D1_bars": len(d1),
            "common_weekly_samples": len(records),
            "common_sample_first_week": records[0]["week"] if records else None,
            "common_sample_last_week": records[-1]["week"] if records else None,
            "note": "Intraday history is limited by the data vendor; cross-timeframe comparisons use only the common weekly sample. D1/W1/M1 also receive a separate long-history panel.",
        },
        "weekly_execution_definition": "Signals use only completed bars before Monday 08:00 Europe/Warsaw. Entry is the first H1 close at/after Monday 08:00; exit is first H1 close at/after Friday 22:00. No overlapping daily forward-return observations are used in the common weekly sample.",
        "feature_definitions": {
            "trend_confirmation": "MA30 vs MA60 plus MA100 vs MA200; full stack additionally requires 30/60/100/200 monotonic order.",
            "trigger": "MA30 reclaim/rejection or MA30/MA60 cross on H1/H4, using only the completed pre-entry bar.",
            "support_resistance_hold": "Price remains on the slow-trend side, is within 0.35 ATR14 of the nearest MA and the bar range touches a 0.10 ATR zone around it.",
            "timeframe_roles_tested": "M1 macro regime; W1 structural trend; D1 primary weekly trend; H4 setup/support; H1 timing trigger.",
        },
        "common_sample_standalone": standalone,
        "common_sample_combinations": combos,
        "long_history_D1_W1_M1": long_history_tests(d1),
        "top_common_sample_edges": ranking[:20],
        "governance": "Research results must not change production trading rules automatically. Promotion requires stable subperiod/holdout evidence, adequate sample size and a separate implementation review.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
