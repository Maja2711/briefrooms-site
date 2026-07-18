#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Walk-forward validation for the experimental continuous-exposure layer.

The test is deliberately separate from model v2.  It uses only information
available before each simulated entry, subtracts one S&P point for every closed
leg, applies the saved ATR SL/TP distances and approximates same-week re-entry
with the next daily open.  Live paper execution uses completed 5-minute bars, so
this report is a conservative daily-bar approximation, not a claim of live
validation.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import backtest_weekly_model_v2 as base_test
import investments_weekly_v2 as v2
import investments_weekly_v3 as v3

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "data" / "investments" / "methodology.json"
POLICY = ROOT / "data" / "investments" / "continuous_exposure_policy.json"
OUT = ROOT / "data" / "investments" / "continuous_exposure_validation_v3.json"
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
    return base_test.col(df, name)


def weekly_signal_for_slice(df: Any, policy_cfg: Dict[str, Any]) -> Dict[str, Any]:
    try:
        import pandas as pd

        frame = pd.concat(
            {
                "Open": col(df, "Open").astype(float),
                "High": col(df, "High").astype(float),
                "Low": col(df, "Low").astype(float),
                "Close": col(df, "Close").astype(float),
            },
            axis=1,
        ).dropna()
        weekly = frame.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    except Exception:
        return {"data_quality": "failed", "score": 0, "regime": "unknown"}

    rules = policy_cfg.get("weekly_candle_model") or {}
    minimum = int(rules.get("minimum_weekly_bars") or 40)
    if len(weekly) < minimum:
        return {"data_quality": "failed", "score": 0, "regime": "unknown"}
    closes = [float(x) for x in weekly["Close"].tolist()]
    opens = [float(x) for x in weekly["Open"].tolist()]
    highs = [float(x) for x in weekly["High"].tolist()]
    lows = [float(x) for x in weekly["Low"].tolist()]
    fast = int(rules.get("ema_fast") or 10)
    slow = int(rules.get("ema_slow") or 30)
    mom_fast_n = int(rules.get("momentum_fast_weeks") or 4)
    mom_slow_n = int(rules.get("momentum_slow_weeks") or 13)
    breakout_n = int(rules.get("breakout_lookback_weeks") or 26)
    weights = rules.get("weights") or {}

    ema_fast = v2.ema(closes, fast)[-1]
    ema_slow = v2.ema(closes, slow)[-1]
    atr = v3._weekly_atr(weekly, 13)
    vol13 = v3._weekly_vol(closes, 13)
    vol52 = v3._weekly_vol(closes, 52)
    if not atr or not vol13 or not vol52:
        return {"data_quality": "failed", "score": 0, "regime": "unknown"}
    ret4 = v3._pct(closes, mom_fast_n)
    ret13 = v3._pct(closes, mom_slow_n)
    sigma = max(vol13 / math.sqrt(52.0), 1e-9)
    prior = closes[-breakout_n - 1 : -1]
    if not prior:
        return {"data_quality": "failed", "score": 0, "regime": "unknown"}
    range_pos = v3.clip((closes[-1] - min(prior)) / max(max(prior) - min(prior), 1e-12), 0.0, 1.0)
    candle_body = v3.clip((closes[-1] - opens[-1]) / max(highs[-1] - lows[-1], 1e-12), -1.0, 1.0)
    trend = float(weights.get("trend") or 35) * v3.clip(((ema_fast - ema_slow) / atr) / 1.5, -1.0, 1.0)
    fast_m = float(weights.get("momentum_fast") or 25) * v3.clip((ret4 / (sigma * math.sqrt(max(mom_fast_n, 1)))) / 1.5, -1.0, 1.0)
    slow_m = float(weights.get("momentum_slow") or 20) * v3.clip((ret13 / (sigma * math.sqrt(max(mom_slow_n, 1)))) / 1.5, -1.0, 1.0)
    breakout = float(weights.get("breakout") or 15) * (2.0 * range_pos - 1.0)
    candle = float(weights.get("last_candle") or 5) * candle_body
    vol_ratio = vol13 / vol52
    scale = 0.78 if vol_ratio >= 1.50 else 0.88 if vol_ratio >= 1.20 else 1.0
    score = int(round(v3.clip((trend + fast_m + slow_m + breakout + candle) * scale, -100.0, 100.0)))
    trend_regime = "trend_up" if ema_fast - ema_slow > 0.15 * atr else "trend_down" if ema_slow - ema_fast > 0.15 * atr else "trend_flat"
    regime = f"{trend_regime}:{'vol_high' if vol_ratio >= 1.20 else 'vol_normal'}"
    return {"data_quality": "passed", "score": score, "regime": regime}


def adaptive_state_from_legs(legs: List[Dict[str, Any]], policy_cfg: Dict[str, Any]) -> Dict[str, Any]:
    adaptive = policy_cfg.get("adaptive_learning") or {}
    rolling = int(adaptive.get("rolling_legs") or 60)
    minimum = int(adaptive.get("minimum_observations_per_regime_direction") or 8)
    cap = abs(float(adaptive.get("max_score_adjustment") or 15))
    buckets: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: {"long": [], "short": []})
    for leg in legs[-rolling:]:
        direction = str(leg.get("direction") or "")
        if direction in {"long", "short"}:
            buckets[str(leg.get("regime") or "unknown")][direction].append(float(leg.get("net_return_percent") or 0.0))
    regimes: Dict[str, Any] = {}
    for regime, sides in buckets.items():
        long_values, short_values = sides["long"], sides["short"]
        eligible = len(long_values) >= minimum and len(short_values) >= minimum
        adjustment = 0.0
        if eligible:
            adjustment = v3.clip((statistics.fmean(long_values) - statistics.fmean(short_values)) * 20.0, -cap, cap)
        regimes[regime] = {"eligible": eligible, "score_adjustment": adjustment}
    return {"regimes": regimes}


def record_leg(
    rows: List[float],
    legs: List[Dict[str, Any]],
    exit_reasons: Counter[str],
    direction: str,
    entry: float,
    exit_price: float,
    reason: str,
    regime: str,
    cost_points: float,
) -> None:
    gross = (exit_price - entry) / entry if direction == "long" else (entry - exit_price) / entry
    net = gross - cost_points / entry
    rows.append(net)
    exit_reasons[reason] += 1
    legs.append({"direction": direction, "regime": regime, "net_return_percent": net * 100.0})


def max_drawdown(returns: List[float]) -> float:
    equity = peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def run_backtest() -> Dict[str, Any]:
    method_cfg = read(METHOD, {})
    policy_cfg = read(POLICY, {})
    instrument_id = str(policy_cfg.get("instrument_id") or "sp500_futures")
    inst = next((x for x in method_cfg.get("instruments") or [] if str(x.get("id") or "") == instrument_id), None)
    if not inst:
        return {"status": "instrument_missing", "instrument_id": instrument_id}
    df = v2.download_daily(str(inst.get("symbol") or ""), period="5y")
    if df is None or len(df) < 320:
        return {"status": "insufficient_data", "instrument_id": instrument_id}

    cost_points = float(((policy_cfg.get("risk_and_costs") or {}).get("sp500_round_trip_cost_points") or 1.0))
    returns: List[float] = []
    legs: List[Dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    weeks = 0

    for friday_index in range(260, len(df) - 6):
        cutoff = df.index[friday_index]
        if getattr(cutoff, "weekday", lambda: -1)() != 4:
            continue
        future = df.iloc[friday_index + 1 : friday_index + 6]
        if len(future) < 5:
            continue
        weeks += 1
        current_entry: Optional[float] = None
        current_direction = ""
        current_regime = "unknown"
        stop_distance = take_distance = 0.0

        for day_index, (_, bar) in enumerate(future.iterrows()):
            history_end = friday_index + day_index + 1
            hist = df.iloc[:history_end]
            if current_entry is None:
                base = base_test.signal_for_slice(hist, inst, method_cfg)
                weekly = weekly_signal_for_slice(hist, policy_cfg)
                state = adaptive_state_from_legs(legs, policy_cfg)
                fresh = {
                    "direction": base.get("direction"),
                    "score": base.get("score"),
                    "data_quality": "passed",
                }
                decision = v3.choose_direction(fresh, weekly, state, policy_cfg)
                current_direction = decision["direction"]
                current_regime = decision["regime"]
                open_value = bar["Open"]
                if hasattr(open_value, "iloc"):
                    open_value = open_value.iloc[0]
                current_entry = float(open_value)
                stop_distance = float(base.get("stop_distance") or 0.0)
                take_distance = float(base.get("take_distance") or 0.0)
                if stop_distance <= 0 or take_distance <= 0:
                    current_entry = None
                    continue

            high_value, low_value, close_value = bar["High"], bar["Low"], bar["Close"]
            if hasattr(high_value, "iloc"):
                high_value, low_value, close_value = high_value.iloc[0], low_value.iloc[0], close_value.iloc[0]
            high, low, close = float(high_value), float(low_value), float(close_value)
            sl = current_entry - stop_distance if current_direction == "long" else current_entry + stop_distance
            tp = current_entry + take_distance if current_direction == "long" else current_entry - take_distance
            sl_hit, tp_hit = ((low <= sl, high >= tp) if current_direction == "long" else (high >= sl, low <= tp))
            if sl_hit:
                record_leg(returns, legs, reasons, current_direction, current_entry, sl, "stop_loss", current_regime, cost_points)
                current_entry = None
                continue
            if tp_hit:
                record_leg(returns, legs, reasons, current_direction, current_entry, tp, "take_profit", current_regime, cost_points)
                current_entry = None
                continue

            if day_index < 4:
                fresh_hist = df.iloc[: history_end + 1]
                fresh = base_test.signal_for_slice(fresh_hist, inst, method_cfg)
                invalidation = base_test.daily_exit(current_direction, fresh, read(ROOT / "data" / "investments" / "daily_review_policy.json", {}))
                if invalidation:
                    record_leg(returns, legs, reasons, current_direction, current_entry, close, invalidation, current_regime, cost_points)
                    current_entry = None
                    continue
            if day_index == 4:
                record_leg(returns, legs, reasons, current_direction, current_entry, close, "scheduled_week_close", current_regime, cost_points)
                current_entry = None

    if not returns:
        return {"status": "no_trades", "instrument_id": instrument_id}
    wins = [x for x in returns if x > 0]
    losses = [x for x in returns if x < 0]
    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))
    stdev = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = statistics.fmean(returns) / stdev * math.sqrt(52.0) if stdev else 0.0
    result = {
        "status": "evaluated",
        "layer_version": v3.LAYER_VERSION,
        "policy_version": policy_cfg.get("policy_version"),
        "instrument_id": instrument_id,
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "assumption": "Daily-bar walk-forward approximation; live paper re-entry uses completed 5-minute bars.",
        "weeks": weeks,
        "closed_legs": len(returns),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(returns), 4),
        "mean_net_return_percent_per_leg": round(statistics.fmean(returns) * 100.0, 4),
        "median_net_return_percent_per_leg": round(statistics.median(returns) * 100.0, 4),
        "profit_factor": round(gross_wins / gross_losses, 4) if gross_losses else None,
        "annualized_leg_sharpe_approximation": round(sharpe, 4),
        "maximum_drawdown_percent": round(max_drawdown(returns) * 100.0, 4),
        "exit_reasons": dict(reasons),
        "transaction_cost_points_per_leg": cost_points,
    }
    passed = (
        len(returns) >= 100
        and result["mean_net_return_percent_per_leg"] > 0
        and float(result.get("profit_factor") or 0) > 1.05
        and abs(result["maximum_drawdown_percent"]) < 20
    )
    result["validation_status"] = "passed_historical_daily_approximation_not_live_validated" if passed else "failed_or_insufficient_historical_daily_approximation"
    return result


def main() -> None:
    report = run_backtest()
    write(OUT, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
