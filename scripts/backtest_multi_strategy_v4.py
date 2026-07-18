#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Walk-forward validation for the v4 multi-instrument paper strategy tournament.

The test compares base, inverse, weekly trend, blended trend and mean-reversion
methods separately. Method selection for each simulated week uses only outcomes
from earlier weeks. It subtracts saved transaction costs and uses frozen ATR
SL/TP distances. Same-week live re-entry is not reconstructed from daily data,
so the report is an approximation rather than a claim of live validation.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import backtest_weekly_model_v2 as base
import investments_weekly_v2 as v2
import investments_weekly_v4 as v4

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "data" / "investments" / "methodology.json"
POLICY = ROOT / "data" / "investments" / "multi_instrument_exposure_policy.json"
OUT = ROOT / "data" / "investments" / "multi_instrument_validation_v4.json"
TZ = ZoneInfo("Europe/Warsaw")


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def col(df: Any, name: str) -> Any:
    return base.col(df, name)


def weekly_signal(df: Any, policy: Dict[str, Any]) -> Dict[str, Any]:
    try:
        import pandas as pd
        frame = pd.concat({
            "Open": col(df, "Open").astype(float), "High": col(df, "High").astype(float),
            "Low": col(df, "Low").astype(float), "Close": col(df, "Close").astype(float),
        }, axis=1).dropna()
        weekly = frame.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    except Exception:
        return {"data_quality": "failed", "score": 0, "regime": "unknown"}
    rules = policy.get("weekly_candle_model") or {}
    if len(weekly) < int(rules.get("minimum_weekly_bars") or 40):
        return {"data_quality": "failed", "score": 0, "regime": "unknown"}
    closes = [float(x) for x in weekly["Close"].tolist()]
    opens = [float(x) for x in weekly["Open"].tolist()]
    highs = [float(x) for x in weekly["High"].tolist()]
    lows = [float(x) for x in weekly["Low"].tolist()]
    fast, slow = int(rules.get("ema_fast") or 10), int(rules.get("ema_slow") or 30)
    m4, m13 = int(rules.get("momentum_fast_weeks") or 4), int(rules.get("momentum_slow_weeks") or 13)
    br = int(rules.get("breakout_lookback_weeks") or 26)
    weights = rules.get("weights") or {}
    ema_fast, ema_slow = v2.ema(closes, fast)[-1], v2.ema(closes, slow)[-1]
    atr = weekly_atr(highs, lows, closes, 13)
    vol13, vol52 = weekly_vol(closes, 13), weekly_vol(closes, 52)
    if not atr or not vol13 or not vol52:
        return {"data_quality": "failed", "score": 0, "regime": "unknown"}
    ret4, ret13 = pct(closes, m4), pct(closes, m13)
    sigma = max(vol13 / math.sqrt(52.0), 1e-9)
    prior = closes[-br-1:-1] if len(closes) > br else closes[:-1]
    pos = v2.clip((closes[-1] - min(prior)) / max(max(prior) - min(prior), 1e-12), 0, 1)
    body = v2.clip((closes[-1] - opens[-1]) / max(highs[-1] - lows[-1], 1e-12), -1, 1)
    trend = float(weights.get("trend") or 35) * v2.clip(((ema_fast - ema_slow) / atr) / 1.5, -1, 1)
    mom4 = float(weights.get("momentum_fast") or 25) * v2.clip((ret4 / (sigma * math.sqrt(max(m4, 1)))) / 1.5, -1, 1)
    mom13 = float(weights.get("momentum_slow") or 20) * v2.clip((ret13 / (sigma * math.sqrt(max(m13, 1)))) / 1.5, -1, 1)
    breakout = float(weights.get("breakout") or 15) * (2 * pos - 1)
    candle = float(weights.get("last_candle") or 5) * body
    ratio = vol13 / vol52
    scale = 0.78 if ratio >= 1.50 else 0.88 if ratio >= 1.20 else 1.0
    score = int(round(v2.clip((trend + mom4 + mom13 + breakout + candle) * scale, -100, 100)))
    regime = ("trend_up" if ema_fast - ema_slow > 0.15 * atr else "trend_down" if ema_slow - ema_fast > 0.15 * atr else "trend_flat") + (":vol_high" if ratio >= 1.20 else ":vol_normal")
    return {"data_quality": "passed", "score": score, "regime": regime}


def weekly_atr(highs: List[float], lows: List[float], closes: List[float], n: int) -> Optional[float]:
    tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, min(len(highs), len(lows), len(closes)))]
    return statistics.fmean(tr[-n:]) if len(tr) >= n else None


def weekly_vol(closes: List[float], n: int) -> Optional[float]:
    if len(closes) <= n:
        return None
    r = [math.log(closes[i]/closes[i-1]) for i in range(len(closes)-n, len(closes)) if closes[i] > 0 and closes[i-1] > 0]
    return statistics.stdev(r) * math.sqrt(52) if len(r) >= max(8, n//2) else None


def pct(values: List[float], n: int) -> float:
    return values[-1]/values[-n-1]-1 if len(values) > n and values[-n-1] else 0.0


def policy_row(policy: Dict[str, Any], instrument_id: str) -> Dict[str, Any]:
    return next((x for x in policy.get("instruments") or [] if x.get("instrument_id") == instrument_id), {})


def choose_walkforward(candidates: Dict[str, Dict[str, Any]], history: Dict[str, List[float]], policy: Dict[str, Any]) -> Dict[str, Any]:
    cfg = policy.get("strategy_tournament") or {}
    minimum = int(cfg.get("minimum_observations_before_performance_weight") or 6)
    prior = float(cfg.get("prior_observations") or 5)
    weight = float(cfg.get("performance_weight") or 18)
    cap = abs(float(cfg.get("maximum_learning_adjustment") or 18))
    explore = float(cfg.get("exploration_bonus") or 2.5)
    best = None
    for method_id in cfg.get("candidate_methods") or candidates.keys():
        row = dict(candidates.get(method_id) or {})
        if not row:
            continue
        values = history.get(method_id) or []
        mean = statistics.fmean(values) if values else 0.0
        shrunk = mean * len(values)/(len(values)+prior) if values else 0.0
        adjustment = v2.clip(shrunk * weight, -cap, cap) if len(values) >= minimum else 0.0
        utility = float(row.get("conviction") or 0) + adjustment + explore/math.sqrt(len(values)+1)
        row.update({"strategy_id": method_id, "utility": utility, "learning_count": len(values), "learning_adjustment": adjustment})
        if best is None or utility > best[0]:
            best = (utility, row)
    return best[1] if best else {"strategy_id": "fallback_long", "direction": "long", "raw_score": 0}


def trade_return(side: str, entry: float, future: Any, stop_d: float, take_d: float, cost_pct: float) -> float:
    sl = entry-stop_d if side == "long" else entry+stop_d
    tp = entry+take_d if side == "long" else entry-take_d
    exit_price = float(col(future, "Close").dropna().iloc[-1])
    for _, bar in future.iterrows():
        high = float(bar["High"].iloc[0] if hasattr(bar["High"], "iloc") else bar["High"])
        low = float(bar["Low"].iloc[0] if hasattr(bar["Low"], "iloc") else bar["Low"])
        sl_hit, tp_hit = ((low <= sl, high >= tp) if side == "long" else (high >= sl, low <= tp))
        if sl_hit:
            exit_price = sl; break
        if tp_hit:
            exit_price = tp; break
    gross = (exit_price-entry)/entry*100 if side == "long" else (entry-exit_price)/entry*100
    return gross-cost_pct


def metrics(rows: List[float]) -> Dict[str, Any]:
    if not rows:
        return {"trades": 0}
    wins = [x for x in rows if x > 0]
    losses = [-x for x in rows if x < 0]
    equity = peak = 1.0; dd = 0.0
    for r in rows:
        equity *= 1+r/100.0; peak = max(peak, equity); dd = min(dd, equity/peak-1)
    sd = statistics.stdev(rows) if len(rows) > 1 else 0.0
    return {
        "trades": len(rows), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins)/len(rows), 4),
        "mean_net_return_percent": round(statistics.fmean(rows), 4),
        "median_net_return_percent": round(statistics.median(rows), 4),
        "profit_factor": round(sum(wins)/sum(losses), 4) if losses else None,
        "annualized_weekly_sharpe": round(statistics.fmean(rows)/sd*math.sqrt(52), 4) if sd else 0.0,
        "maximum_drawdown_percent": round(dd*100, 4),
    }


def validate(inst: Dict[str, Any], method: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    df = v2.download_daily(str(inst.get("symbol")), period="5y")
    if df is None or len(df) < 300:
        return {"instrument_id": inst.get("id"), "status": "insufficient_data"}
    p_row = policy_row(policy, str(inst.get("id")))
    history: Dict[str, List[float]] = {m: [] for m in (policy.get("strategy_tournament") or {}).get("candidate_methods") or []}
    selected: List[float] = []
    selections: Counter[str] = Counter()
    for i in range(260, len(df)-6):
        cutoff = df.index[i]
        if getattr(cutoff, "weekday", lambda: -1)() != 4:
            continue
        hist, future = df.iloc[:i+1], df.iloc[i+1:i+6]
        if future.empty:
            continue
        fresh = base.signal_for_slice(hist, inst, method)
        closes = [float(x) for x in col(hist, "Close").dropna().tolist()]
        atr = v2.atr14_from_df(hist)
        fresh["signals"] = {"last_close": closes[-1], "ema20": v2.ema(closes, 20)[-1], "atr14": atr}
        weekly = weekly_signal(hist, policy)
        candidates = v4.candidate_methods(fresh, weekly, str(p_row.get("default_tie_direction") or "long"))
        decision = choose_walkforward(candidates, history, policy)
        entry = float(col(future.iloc[:1], "Open").dropna().iloc[0])
        stop_d, take_d = float(fresh.get("stop_distance") or 0), float(fresh.get("take_distance") or 0)
        if stop_d <= 0 or take_d <= 0:
            continue
        cost = v4.cost_percent(str(inst.get("id")), entry, p_row)
        week_results: Dict[str, float] = {}
        for method_id, row in candidates.items():
            r = trade_return(str(row.get("direction")), entry, future, stop_d, take_d, cost)
            week_results[method_id] = r
        chosen_id = str(decision.get("strategy_id"))
        if chosen_id not in week_results:
            continue
        selected.append(week_results[chosen_id]); selections[chosen_id] += 1
        for method_id, r in week_results.items():
            history.setdefault(method_id, []).append(r)
    return {
        "instrument_id": inst.get("id"), "status": "evaluated",
        "selected_tournament": metrics(selected),
        "selection_counts": dict(selections),
        "candidate_methods": {method_id: metrics(rows) for method_id, rows in history.items()},
    }


def main() -> None:
    method, policy = read(METHOD, {}), read(POLICY, {})
    enabled = {x.get("instrument_id") for x in policy.get("instruments") or [] if x.get("enabled")}
    results = [validate(inst, method, policy) for inst in method.get("instruments") or [] if inst.get("id") in enabled]
    report = {
        "model_version": "4.0.0-experimental", "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "status": "research_only_not_live_validated",
        "assumption": "One selected position per week with daily-bar SL/TP; live same-week re-entry is not reconstructed.",
        "warning": "A losing short method does not prove that the inverse long method is profitable. Candidate methods are reported separately.",
        "results": results,
    }
    write(OUT, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
