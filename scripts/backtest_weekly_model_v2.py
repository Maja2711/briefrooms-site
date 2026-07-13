#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rolling fixed-parameter validation for weekly model v2.

The report evaluates the saved weekly entry rules, frozen ATR risk levels and
the once-daily thesis review. It is not used to optimise thresholds. Daily exits
are approximated with each completed daily close; SL/TP remains conservative
(stop first if both levels are inside the same daily bar). Fixed round-trip costs
are subtracted from every closed scenario.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import investments_weekly_v2 as model

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "data" / "investments" / "methodology.json"
POLICY = ROOT / "data" / "investments" / "daily_review_policy.json"
OUT = ROOT / "data" / "investments" / "model_validation_v2.json"
TZ = ZoneInfo("Europe/Warsaw")


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def recent_report(cfg: Dict[str, Any], policy: Dict[str, Any]) -> bool:
    if os.getenv("FORCE_BACKTEST") == "1" or not OUT.exists():
        return False
    report = read(OUT, {})
    if str(report.get("model_version")) != str(cfg.get("method_version")):
        return False
    if str(report.get("daily_review_policy_version")) != str(policy.get("policy_version")):
        return False
    try:
        age = datetime.now(TZ) - datetime.fromisoformat(report.get("generated_at"))
        return age < timedelta(days=6)
    except Exception:
        return False


def col(df: Any, name: str) -> Any:
    values = df[name]
    if hasattr(values, "columns"):
        values = values.iloc[:, 0]
    return values


def signal_for_slice(df: Any, inst: Dict[str, Any], method_cfg: Dict[str, Any]) -> Dict[str, Any]:
    closes = [float(x) for x in col(df, "Close").dropna().tolist()]
    if len(closes) < 260:
        return {"direction": "neutral", "score": 0, "positive_groups": 0, "negative_groups": 0}
    last = closes[-1]
    ema20 = model.ema(closes, 20)[-1]
    ema50 = model.ema(closes, 50)[-1]
    atr = model.atr14_from_df(df)
    vol20 = model.realized_vol(closes, 20)
    vol60 = model.realized_vol(closes, 60)
    if not atr or not vol20 or not vol60:
        return {"direction": "neutral", "score": 0, "positive_groups": 0, "negative_groups": 0}
    ret5 = model.pct_change(closes, 5)
    ret20 = model.pct_change(closes, 20)
    daily_vol = vol20 / math.sqrt(252.0)
    trend_ema = 20.0 * model.clip(((ema20 - ema50) / atr) / 1.5, -1.0, 1.0)
    trend_price = 10.0 * model.clip((last - ema20) / atr, -1.0, 1.0)
    mom5 = 15.0 * model.clip((ret5 / max(daily_vol * math.sqrt(5.0), 1e-9)) / 1.5, -1.0, 1.0)
    mom20 = 20.0 * model.clip((ret20 / max(daily_vol * math.sqrt(20.0), 1e-9)) / 1.5, -1.0, 1.0)
    prior = closes[-56:-1]
    pos = model.clip((last - min(prior)) / max(max(prior) - min(prior), 1e-12), 0.0, 1.0)
    breakout = 15.0 * (2.0 * pos - 1.0)
    vol_ratio = vol20 / vol60
    scale = 0.70 if vol_ratio >= 1.60 else 0.82 if vol_ratio >= 1.30 else 0.92 if vol_ratio >= 1.10 else 1.0
    score = int(round(model.clip((trend_ema + trend_price + mom5 + mom20 + breakout) * scale, -100.0, 100.0)))
    groups = [trend_ema + trend_price, mom5 + mom20, breakout]
    pos_agree = sum(1 for x in groups if x > 3)
    neg_agree = sum(1 for x in groups if x < -3)
    long_t, short_t = model.entry_thresholds(method_cfg, str(inst["id"]))
    direction = "long" if score >= long_t and pos_agree >= 2 else "short" if score <= short_t and neg_agree >= 2 else "neutral"
    expected = atr * math.sqrt(5.0)
    stop = expected * float((method_cfg.get("risk_model") or {}).get("stop_atr_week_multiplier", 0.55))
    take = expected * float((method_cfg.get("risk_model") or {}).get("take_atr_week_multiplier", 0.90))
    limits = model.risk_limits(method_cfg, str(inst["id"]))
    stop_units = model.price_distance_to_units(str(inst["id"]), stop, last)
    take_units = model.price_distance_to_units(str(inst["id"]), take, last)
    if limits:
        stop_units = model.clip(stop_units, limits.get("min_stop_units", stop_units), limits.get("max_stop_units", stop_units))
        take_units = model.clip(take_units, limits.get("min_take_units", take_units), limits.get("max_take_units", take_units))
    return {
        "direction": direction,
        "score": score,
        "positive_groups": pos_agree,
        "negative_groups": neg_agree,
        "stop_distance": model.units_to_price_distance(str(inst["id"]), stop_units, last),
        "take_distance": model.units_to_price_distance(str(inst["id"]), take_units, last),
    }


def daily_exit(side: str, fresh: Dict[str, Any], policy: Dict[str, Any]) -> str:
    rules = policy.get("rules") if isinstance(policy.get("rules"), dict) else {}
    invalidation = rules.get("directional_invalidation") if isinstance(rules.get("directional_invalidation"), dict) else {}
    # The saved policy text is descriptive; numeric constants are frozen here and
    # mirrored in daily_position_review.py.
    threshold = 15
    required = 2
    direction = str(fresh.get("direction") or "neutral")
    score = int(fresh.get("score") or 0)
    pos = int(fresh.get("positive_groups") or 0)
    neg = int(fresh.get("negative_groups") or 0)
    if side == "long":
        if direction == "short":
            return "daily_confirmed_opposite_signal"
        if score <= -threshold and neg >= required:
            return "daily_directional_invalidation"
    if side == "short":
        if direction == "long":
            return "daily_confirmed_opposite_signal"
        if score >= threshold and pos >= required:
            return "daily_directional_invalidation"
    return ""


def cost_return(inst_id: str, entry: float, cfg: Dict[str, Any]) -> float:
    costs = ((cfg.get("validation") or {}).get("transaction_cost_assumptions") or {})
    if inst_id == "eurusd":
        return float(costs.get("eurusd_round_trip_pips", 1.5)) * 0.0001 / entry
    if inst_id == "sp500_futures":
        return float(costs.get("sp500_futures_round_trip_points", 1.0)) / entry
    return float(costs.get("btcusd_round_trip_percent", 0.2)) / 100.0


def max_drawdown(returns: List[float]) -> float:
    equity = peak = 1.0
    worst = 0.0
    for r in returns:
        equity *= 1.0 + r
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def validate_instrument(inst: Dict[str, Any], cfg: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    df = model.download_daily(str(inst["symbol"]), period="5y")
    if df is None or len(df) < 300:
        return {"instrument_id": inst["id"], "status": "insufficient_data", "trades": 0}
    rows: List[float] = []
    gross_wins = gross_losses = 0.0
    wins = losses = 0
    exit_reasons: Counter[str] = Counter()
    for i in range(260, len(df) - 6):
        cutoff = df.index[i]
        if getattr(cutoff, "weekday", lambda: -1)() != 4:
            continue
        hist = df.iloc[: i + 1]
        sig = signal_for_slice(hist, inst, cfg)
        side = sig.get("direction")
        if side not in {"long", "short"}:
            continue
        future = df.iloc[i + 1 : i + 6]
        if future.empty:
            continue
        entry = float(col(future.iloc[:1], "Open").dropna().iloc[0])
        scheduled = float(col(future, "Close").dropna().iloc[-1])
        stop_d = float(sig["stop_distance"])
        take_d = float(sig["take_distance"])
        sl = entry - stop_d if side == "long" else entry + stop_d
        tp = entry + take_d if side == "long" else entry - take_d
        exit_price = scheduled
        exit_reason = "scheduled_week_close"
        for day_index, (_, bar) in enumerate(future.iterrows()):
            high = float(bar["High"].iloc[0] if hasattr(bar["High"], "iloc") else bar["High"])
            low = float(bar["Low"].iloc[0] if hasattr(bar["Low"], "iloc") else bar["Low"])
            close = float(bar["Close"].iloc[0] if hasattr(bar["Close"], "iloc") else bar["Close"])
            sl_hit, tp_hit = ((low <= sl, high >= tp) if side == "long" else (high >= sl, low <= tp))
            if sl_hit:
                exit_price = sl
                exit_reason = "stop_loss"
                break
            if tp_hit:
                exit_price = tp
                exit_reason = "take_profit"
                break
            # Monday-Thursday: approximate the 23:00 review with the completed
            # daily bar. Friday is already governed by the scheduled close.
            if day_index < 4:
                fresh_hist = df.iloc[: i + 2 + day_index]
                fresh = signal_for_slice(fresh_hist, inst, cfg)
                reason = daily_exit(str(side), fresh, policy)
                if reason:
                    exit_price = close
                    exit_reason = reason
                    break
        gross = (exit_price - entry) / entry if side == "long" else (entry - exit_price) / entry
        net = gross - cost_return(str(inst["id"]), entry, cfg)
        rows.append(net)
        exit_reasons[exit_reason] += 1
        if net > 0:
            wins += 1
            gross_wins += net
        elif net < 0:
            losses += 1
            gross_losses += abs(net)
    if not rows:
        return {"instrument_id": inst["id"], "status": "no_trades", "trades": 0}
    stdev = statistics.stdev(rows) if len(rows) > 1 else 0.0
    sharpe = statistics.fmean(rows) / stdev * math.sqrt(52.0) if stdev > 0 else 0.0
    return {
        "instrument_id": inst["id"],
        "status": "evaluated",
        "trades": len(rows),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(rows), 4),
        "mean_net_return_percent": round(statistics.fmean(rows) * 100.0, 4),
        "median_net_return_percent": round(statistics.median(rows) * 100.0, 4),
        "profit_factor": round(gross_wins / gross_losses, 4) if gross_losses else None,
        "annualized_weekly_sharpe": round(sharpe, 4),
        "maximum_drawdown_percent": round(max_drawdown(rows) * 100.0, 4),
        "exit_reasons": dict(exit_reasons),
    }


def main() -> None:
    cfg = read(METHOD, {})
    policy = read(POLICY, {})
    if recent_report(cfg, policy):
        print("Validation report is recent and matches the saved daily-review policy; skipped")
        return
    results = [validate_instrument(inst, cfg, policy) for inst in cfg.get("instruments", [])]
    trades = sum(int(x.get("trades") or 0) for x in results)
    requirements = ((cfg.get("validation") or {}).get("minimum_requirements_before_claiming_validation") or {})
    min_total = int(requirements.get("aggregate_closed_trades", 150))
    min_each = int(requirements.get("minimum_trades_per_instrument", 40))
    pf_floor = float(requirements.get("profit_factor_above", 1.05))
    dd_limit = float(requirements.get("maximum_drawdown_below_percent", 20))
    passed = trades >= min_total
    for row in results:
        passed = passed and int(row.get("trades") or 0) >= min_each
        passed = passed and float(row.get("mean_net_return_percent") or -999) > 0
        passed = passed and float(row.get("profit_factor") or 0) > pf_floor
        passed = passed and abs(float(row.get("maximum_drawdown_percent") or 999)) < dd_limit
    report = {
        "model_version": str(cfg.get("method_version") or model.MODEL_VERSION),
        "daily_review_policy_version": str(policy.get("policy_version") or "unknown"),
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "validation_status": "passed_fixed_rule_historical_test" if passed else "not_yet_validated",
        "warning": "Historical validation is not a guarantee of future performance and is not used to optimise the saved thresholds.",
        "daily_review_backtest_assumption": "Daily thesis review is approximated with each completed Monday-Thursday daily close; live execution uses the last completed 5-minute bar after 23:00 Europe/Warsaw.",
        "aggregate_trades": trades,
        "requirements": requirements,
        "results": results,
    }
    write(OUT, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
