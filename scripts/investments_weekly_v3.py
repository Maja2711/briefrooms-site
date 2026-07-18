#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Experimental continuous-exposure wrapper for the weekly paper model.

This module deliberately does not overwrite the fixed-rule v2 validation.  It
adds a separately versioned paper-trading execution layer with these rules:

* one S&P 500 futures paper position must be opened from Monday 08:00 Warsaw;
* after SL, TP, daily invalidation or a material-event close, the closed leg is
  archived and a new leg is opened on the first completed 5-minute bar available
  to the workflow, unless the scheduled Friday close has already passed;
* neutral v2 signals are resolved with a weekly-candle model;
* a capped adaptive tie-break uses only earlier closed paper legs from the same
  market regime.  It never rewrites history or optimises on the current bar;
* this script creates no broker order and must remain paper-trading only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import investments_weekly as legacy
import investments_weekly_v2 as v2

ROOT = Path(__file__).resolve().parents[1]
METHOD_PATH = ROOT / "data" / "investments" / "methodology.json"
POLICY_PATH = ROOT / "data" / "investments" / "continuous_exposure_policy.json"
STATE_PATH = ROOT / "data" / "investments" / "continuous_exposure_state.json"
REPORT_PATH = ROOT / "data" / "investments" / "continuous_exposure_report.json"
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
LAYER_VERSION = "3.0.0-experimental"


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def sf(value: Any) -> Optional[float]:
    return v2.sf(value)


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parse_dt(value: Any) -> Optional[datetime]:
    return v2.parse_dt(value)


def policy() -> Dict[str, Any]:
    return read(POLICY_PATH, {})


def method() -> Dict[str, Any]:
    return read(METHOD_PATH, {})


def instrument_cfg(method_cfg: Dict[str, Any], instrument_id: str) -> Optional[Dict[str, Any]]:
    for row in method_cfg.get("instruments") or []:
        if str(row.get("id") or "") == instrument_id:
            return row
    return None


def _series(df: Any, name: str) -> Any:
    return v2._series(df, name)


def _weekly_frame(symbol: str) -> Any:
    """Return Friday-labelled OHLC weekly bars built from daily data."""
    df = v2.download_daily(symbol, period="5y")
    if df is None:
        return None
    try:
        import pandas as pd

        frame = pd.concat(
            {
                "Open": _series(df, "Open").astype(float),
                "High": _series(df, "High").astype(float),
                "Low": _series(df, "Low").astype(float),
                "Close": _series(df, "Close").astype(float),
            },
            axis=1,
        ).dropna()
        if frame.empty:
            return None
        weekly = frame.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
        return weekly
    except Exception:
        return None


def _pct(values: List[float], periods: int) -> float:
    if len(values) <= periods or values[-periods - 1] == 0:
        return 0.0
    return values[-1] / values[-periods - 1] - 1.0


def _weekly_atr(weekly: Any, lookback: int = 13) -> Optional[float]:
    try:
        highs = [float(x) for x in weekly["High"].tolist()]
        lows = [float(x) for x in weekly["Low"].tolist()]
        closes = [float(x) for x in weekly["Close"].tolist()]
    except Exception:
        return None
    tr: List[float] = []
    for i in range(1, min(len(highs), len(lows), len(closes))):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    if len(tr) < lookback:
        return None
    return statistics.fmean(tr[-lookback:])


def _weekly_vol(closes: List[float], lookback: int) -> Optional[float]:
    if len(closes) <= lookback:
        return None
    returns: List[float] = []
    for i in range(len(closes) - lookback, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            returns.append(math.log(closes[i] / closes[i - 1]))
    if len(returns) < max(8, lookback // 2):
        return None
    return statistics.stdev(returns) * math.sqrt(52.0)


def weekly_candle_signal(cfg: Dict[str, Any], policy_cfg: Dict[str, Any]) -> Dict[str, Any]:
    rules = policy_cfg.get("weekly_candle_model") if isinstance(policy_cfg.get("weekly_candle_model"), dict) else {}
    weekly = _weekly_frame(str(cfg.get("symbol") or ""))
    minimum = int(rules.get("minimum_weekly_bars") or 40)
    if weekly is None or len(weekly) < minimum:
        return {"data_quality": "failed", "score": 0, "regime": "unknown", "reason": "weekly_history_unavailable"}

    closes = [float(x) for x in weekly["Close"].tolist()]
    opens = [float(x) for x in weekly["Open"].tolist()]
    highs = [float(x) for x in weekly["High"].tolist()]
    lows = [float(x) for x in weekly["Low"].tolist()]
    fast = int(rules.get("ema_fast") or 10)
    slow = int(rules.get("ema_slow") or 30)
    mom_fast_n = int(rules.get("momentum_fast_weeks") or 4)
    mom_slow_n = int(rules.get("momentum_slow_weeks") or 13)
    breakout_n = int(rules.get("breakout_lookback_weeks") or 26)
    weights = rules.get("weights") if isinstance(rules.get("weights"), dict) else {}

    ema_fast = v2.ema(closes, fast)[-1]
    ema_slow = v2.ema(closes, slow)[-1]
    atr = _weekly_atr(weekly, 13)
    vol13 = _weekly_vol(closes, 13)
    vol52 = _weekly_vol(closes, 52)
    if not atr or atr <= 0 or not vol13 or not vol52:
        return {"data_quality": "failed", "score": 0, "regime": "unknown", "reason": "weekly_atr_or_volatility_unavailable"}

    ret4 = _pct(closes, mom_fast_n)
    ret13 = _pct(closes, mom_slow_n)
    weekly_sigma = max(vol13 / math.sqrt(52.0), 1e-9)
    prior = closes[-breakout_n - 1 : -1]
    if len(prior) < breakout_n:
        prior = closes[:-1]
    low_range, high_range = min(prior), max(prior)
    range_pos = clip((closes[-1] - low_range) / max(high_range - low_range, 1e-12), 0.0, 1.0)
    candle_range = max(highs[-1] - lows[-1], 1e-12)
    candle_body = clip((closes[-1] - opens[-1]) / candle_range, -1.0, 1.0)

    trend = float(weights.get("trend") or 35) * clip(((ema_fast - ema_slow) / atr) / 1.5, -1.0, 1.0)
    momentum_fast = float(weights.get("momentum_fast") or 25) * clip((ret4 / (weekly_sigma * math.sqrt(max(mom_fast_n, 1)))) / 1.5, -1.0, 1.0)
    momentum_slow = float(weights.get("momentum_slow") or 20) * clip((ret13 / (weekly_sigma * math.sqrt(max(mom_slow_n, 1)))) / 1.5, -1.0, 1.0)
    breakout = float(weights.get("breakout") or 15) * (2.0 * range_pos - 1.0)
    candle = float(weights.get("last_candle") or 5) * candle_body
    vol_ratio = vol13 / vol52
    vol_scale = 0.78 if vol_ratio >= 1.50 else 0.88 if vol_ratio >= 1.20 else 1.0
    score = int(round(clip((trend + momentum_fast + momentum_slow + breakout + candle) * vol_scale, -100.0, 100.0)))

    trend_regime = "trend_up" if ema_fast - ema_slow > 0.15 * atr else "trend_down" if ema_slow - ema_fast > 0.15 * atr else "trend_flat"
    volatility_regime = "vol_high" if vol_ratio >= 1.20 else "vol_normal"
    regime = f"{trend_regime}:{volatility_regime}"
    return {
        "data_quality": "passed",
        "score": score,
        "regime": regime,
        "last_week_close": round(closes[-1], 8),
        "ema_fast": round(ema_fast, 8),
        "ema_slow": round(ema_slow, 8),
        "weekly_atr13": round(atr, 8),
        "return_4w_percent": round(ret4 * 100.0, 4),
        "return_13w_percent": round(ret13 * 100.0, 4),
        "volatility_ratio_13w_52w": round(vol_ratio, 4),
        "range_26w_position": round(range_pos, 4),
        "components": {
            "trend": round(trend, 2),
            "momentum_fast": round(momentum_fast, 2),
            "momentum_slow": round(momentum_slow, 2),
            "breakout": round(breakout, 2),
            "last_candle": round(candle, 2),
            "volatility_scale": vol_scale,
        },
    }


def _iter_archived_legs(instrument_id: str) -> Iterable[Dict[str, Any]]:
    for path in sorted(WEEKLY_DIR.glob("*.json")):
        week = read(path, {})
        for item in week.get("instruments") or []:
            if str(item.get("instrument_id") or "") != instrument_id:
                continue
            for leg in item.get("position_legs") or []:
                if isinstance(leg, dict) and leg.get("exit_captured_at"):
                    yield leg


def rebuild_adaptive_state(policy_cfg: Dict[str, Any], instrument_id: str) -> Dict[str, Any]:
    adaptive = policy_cfg.get("adaptive_learning") if isinstance(policy_cfg.get("adaptive_learning"), dict) else {}
    rolling = max(1, int(adaptive.get("rolling_legs") or 60))
    minimum = max(1, int(adaptive.get("minimum_observations_per_regime_direction") or 8))
    cap = abs(float(adaptive.get("max_score_adjustment") or 15))
    legs = list(_iter_archived_legs(instrument_id))[-rolling:]
    buckets: Dict[str, Dict[str, List[float]]] = {}
    for leg in legs:
        regime = str(leg.get("entry_regime") or "unknown")
        direction = str(leg.get("direction") or "")
        value = sf(leg.get("net_result_percent"))
        if direction not in {"long", "short"} or value is None:
            continue
        buckets.setdefault(regime, {"long": [], "short": []})[direction].append(value)

    regimes: Dict[str, Any] = {}
    for regime, sides in buckets.items():
        long_values, short_values = sides["long"], sides["short"]
        long_mean = statistics.fmean(long_values) if long_values else None
        short_mean = statistics.fmean(short_values) if short_values else None
        adjustment = 0.0
        eligible = len(long_values) >= minimum and len(short_values) >= minimum
        if eligible and long_mean is not None and short_mean is not None:
            # One percentage point of after-cost edge is worth 20 score points,
            # but the policy cap prevents runaway optimisation.
            adjustment = clip((long_mean - short_mean) * 20.0, -cap, cap)
        regimes[regime] = {
            "long": {"count": len(long_values), "mean_net_percent": round(long_mean, 6) if long_mean is not None else None},
            "short": {"count": len(short_values), "mean_net_percent": round(short_mean, 6) if short_mean is not None else None},
            "eligible": eligible,
            "score_adjustment": round(adjustment, 4),
        }

    state = {
        "policy_version": policy_cfg.get("policy_version"),
        "layer_version": LAYER_VERSION,
        "instrument_id": instrument_id,
        "generated_at": legacy.now_local().isoformat(timespec="seconds"),
        "closed_legs_used": len(legs),
        "rolling_legs": rolling,
        "minimum_observations_per_regime_direction": minimum,
        "regimes": regimes,
        "rule": "Only closed earlier paper legs are used; score adjustment is capped and history is never rewritten.",
    }
    write(STATE_PATH, state)
    return state


def adaptive_adjustment(state: Dict[str, Any], regime: str) -> float:
    row = (state.get("regimes") or {}).get(regime) or {}
    return float(row.get("score_adjustment") or 0.0) if row.get("eligible") else 0.0


def choose_direction(fresh: Dict[str, Any], weekly: Dict[str, Any], state: Dict[str, Any], policy_cfg: Dict[str, Any]) -> Dict[str, Any]:
    model_direction = str(fresh.get("direction") or "neutral")
    model_score = int(fresh.get("score") or 0)
    weekly_score = int(weekly.get("score") or 0) if weekly.get("data_quality") == "passed" else 0
    regime = str(weekly.get("regime") or "unknown")
    adjustment = adaptive_adjustment(state, regime)

    if model_direction in {"long", "short"} and fresh.get("data_quality") == "passed":
        direction = model_direction
        reason = "v2_directional_signal"
        combined = model_score
    else:
        combined = int(round(clip(0.55 * model_score + 0.45 * weekly_score + adjustment, -100.0, 100.0)))
        tie = str(((policy_cfg.get("weekly_candle_model") or {}).get("tie_break_direction") or "long"))
        direction = "long" if combined > 0 else "short" if combined < 0 else (tie if tie in {"long", "short"} else "long")
        reason = "neutral_resolved_by_weekly_candles_and_closed_leg_learning"

    return {
        "direction": direction,
        "reason": reason,
        "model_direction": model_direction,
        "model_score": model_score,
        "weekly_score": weekly_score,
        "adaptive_adjustment": round(adjustment, 4),
        "combined_score": combined,
        "regime": regime,
    }


def last_completed_5m_bar(symbol: str, now: datetime) -> Optional[Dict[str, Any]]:
    cutoff = now - timedelta(minutes=5)
    start = cutoff - timedelta(days=2)
    df = v2.intraday_bars(symbol, start, cutoff)
    if df is None:
        return None
    try:
        df = df[df.index <= cutoff]
        if df.empty:
            return None
        row = df.iloc[-1]
        value = row["Close"]
        if hasattr(value, "iloc"):
            value = value.iloc[0]
        price = sf(value)
        if price is None:
            return None
        ts = df.index[-1].to_pydatetime().astimezone(v2.TZ)
        return {
            "price": price,
            "timestamp": ts.isoformat(timespec="seconds"),
            "source": f"Yahoo Finance:{symbol}:5m:last_completed_bar_continuous_exposure",
        }
    except Exception:
        return None


def _leg_id(item: Dict[str, Any]) -> str:
    raw = "|".join(
        [
            str(item.get("instrument_id") or ""),
            str(item.get("entry_captured_at") or ""),
            str(item.get("exit_captured_at") or ""),
            str(item.get("direction") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def archive_closed_leg(item: Dict[str, Any], policy_cfg: Dict[str, Any]) -> bool:
    entry, exit_price = sf(item.get("entry_price")), sf(item.get("exit_price"))
    if entry is None or exit_price is None or not item.get("entry_captured_at") or not item.get("exit_captured_at"):
        return False
    leg_id = _leg_id(item)
    legs = item.get("position_legs") if isinstance(item.get("position_legs"), list) else []
    if any(str(row.get("leg_id") or "") == leg_id for row in legs if isinstance(row, dict)):
        return False

    gross_pct = sf(item.get("result_percent"))
    if gross_pct is None:
        side = str(item.get("direction") or "")
        gross_pct = ((exit_price - entry) / entry * 100.0) if side == "long" else ((entry - exit_price) / entry * 100.0)
    cost_points = float(((policy_cfg.get("risk_and_costs") or {}).get("sp500_round_trip_cost_points") or 1.0))
    cost_pct = cost_points / entry * 100.0 if entry else 0.0
    leg = {
        "leg_id": leg_id,
        "instrument_id": item.get("instrument_id"),
        "symbol": item.get("symbol"),
        "direction": item.get("direction"),
        "entry_price": entry,
        "entry_captured_at": item.get("entry_captured_at"),
        "entry_source": item.get("entry_source"),
        "exit_price": exit_price,
        "exit_captured_at": item.get("exit_captured_at"),
        "exit_source": item.get("exit_source"),
        "exit_reason": item.get("exit_reason"),
        "gross_result_percent": round(gross_pct, 6),
        "estimated_round_trip_cost_percent": round(cost_pct, 6),
        "net_result_percent": round(gross_pct - cost_pct, 6),
        "entry_regime": item.get("continuous_entry_regime") or "unknown",
        "entry_decision": item.get("continuous_entry_decision") or {},
        "risk_plan": item.get("risk_plan"),
        "archived_at": legacy.now_local().isoformat(timespec="seconds"),
    }
    legs.append(leg)
    item["position_legs"] = legs
    item["continuous_last_closed_leg_id"] = leg_id
    item["continuous_last_exit_at"] = item.get("exit_captured_at")
    return True


def _within_exposure_window(week: Dict[str, Any], now: datetime, policy_cfg: Dict[str, Any]) -> Tuple[bool, str]:
    if now.weekday() > 4:
        return False, "weekend"
    target = parse_dt((week.get("market_window") or {}).get("entry_target_local"))
    exit_target = parse_dt((week.get("market_window") or {}).get("exit_target_local"))
    if target and now < target:
        return False, "before_monday_08_00"
    if exit_target and now >= exit_target:
        return False, "after_scheduled_friday_close"
    return True, "active_week_window"


def _cooldown_passed(item: Dict[str, Any], now: datetime, policy_cfg: Dict[str, Any]) -> bool:
    last_exit = parse_dt(item.get("continuous_last_exit_at") or item.get("exit_captured_at"))
    if last_exit is None:
        return True
    minutes = max(0, int(policy_cfg.get("minimum_minutes_after_exit") or 5))
    return now >= last_exit + timedelta(minutes=minutes)


def _log(item: Dict[str, Any], action: str, **extra: Any) -> None:
    rows = item.get("continuous_exposure_log") if isinstance(item.get("continuous_exposure_log"), list) else []
    rows.append({"at": legacy.now_local().isoformat(timespec="seconds"), "action": action, **extra})
    item["continuous_exposure_log"] = rows[-80:]


def ensure_exposure() -> Dict[str, Any]:
    now = legacy.now_local()
    policy_cfg = policy()
    report: Dict[str, Any] = {
        "layer_version": LAYER_VERSION,
        "policy_version": policy_cfg.get("policy_version"),
        "checked_at": now.isoformat(timespec="seconds"),
        "actions": [],
        "status": "skipped",
    }
    if not policy_cfg.get("enabled"):
        report["reason"] = "policy_disabled"
        write(REPORT_PATH, report)
        return report
    if not ((policy_cfg.get("guardrails") or {}).get("paper_trading_only", True)):
        raise RuntimeError("Continuous exposure layer may run only in paper-trading mode")

    path = v2.current_week_path(now)
    week = read(path, {})
    if not week:
        report["reason"] = "current_week_missing"
        write(REPORT_PATH, report)
        return report
    active, reason = _within_exposure_window(week, now, policy_cfg)
    if not active:
        report["reason"] = reason
        write(REPORT_PATH, report)
        return report

    instrument_id = str(policy_cfg.get("instrument_id") or "sp500_futures")
    method_cfg = method()
    cfg = instrument_cfg(method_cfg, instrument_id)
    if not cfg:
        report["reason"] = "instrument_config_missing"
        write(REPORT_PATH, report)
        return report
    item = next((row for row in week.get("instruments") or [] if str(row.get("instrument_id") or "") == instrument_id), None)
    if item is None:
        report["reason"] = "instrument_forecast_missing"
        write(REPORT_PATH, report)
        return report

    changed = False
    if sf(item.get("entry_price")) is not None and sf(item.get("exit_price")) is not None:
        if archive_closed_leg(item, policy_cfg):
            changed = True
            report["actions"].append({"instrument_id": instrument_id, "action": "archive_closed_leg", "leg_id": item.get("continuous_last_closed_leg_id")})
            _log(item, "archive_closed_leg", leg_id=item.get("continuous_last_closed_leg_id"))

    # An existing open leg already satisfies continuous exposure.
    if sf(item.get("entry_price")) is not None and sf(item.get("exit_price")) is None and str(item.get("direction") or "") in {"long", "short"}:
        item["continuous_exposure_active"] = True
        item["continuous_exposure_status"] = "open"
        item["continuous_exposure_policy_version"] = policy_cfg.get("policy_version")
        week["continuous_exposure_layer"] = {"enabled": True, "version": LAYER_VERSION, "paper_trading_only": True, "last_checked_at": now.isoformat(timespec="seconds")}
        if changed:
            write(path, week)
        report["status"] = "already_open"
        report["open_direction"] = item.get("direction")
        write(REPORT_PATH, report)
        return report

    if not _cooldown_passed(item, now, policy_cfg):
        report["reason"] = "minimum_five_minute_reentry_cooldown"
        report["status"] = "deferred"
        if changed:
            write(path, week)
        write(REPORT_PATH, report)
        return report

    fresh = v2.model_signal(cfg, method_cfg, str(week.get("week_id") or ""), now)
    weekly = weekly_candle_signal(cfg, policy_cfg)
    state = rebuild_adaptive_state(policy_cfg, instrument_id)
    decision = choose_direction(fresh, weekly, state, policy_cfg)
    point = last_completed_5m_bar(str(cfg.get("symbol") or item.get("symbol") or ""), now)
    if point is None:
        report["reason"] = "completed_5m_entry_bar_unavailable"
        report["status"] = "deferred"
        report["decision"] = decision
        if changed:
            write(path, week)
        write(REPORT_PATH, report)
        return report

    if not week.get("base_method_version"):
        week["base_method_version"] = week.get("method_version") or v2.MODEL_VERSION
    if not week.get("base_forecast_hash"):
        week["base_forecast_hash"] = week.get("forecast_hash")
    week["method_version"] = LAYER_VERSION
    week["model_status"] = "experimental_continuous_exposure_paper_only"

    if not item.get("forecast_direction"):
        item["forecast_direction"] = item.get("direction")
        item["forecast_score"] = item.get("score")
    item["pre_continuous_direction"] = item.get("direction")
    item["direction"] = decision["direction"]
    item["score"] = decision["combined_score"]
    item["continuous_exposure_active"] = True
    item["continuous_exposure_status"] = "open"
    item["continuous_exposure_policy_version"] = policy_cfg.get("policy_version")
    item["continuous_entry_regime"] = decision["regime"]
    item["continuous_entry_decision"] = {**decision, "weekly_features": weekly, "fresh_v2_signal": fresh}
    item["risk_distance"] = fresh.get("risk_distance") or item.get("risk_distance")
    item["entry_price"] = point["price"]
    item["entry_captured_at"] = point["timestamp"]
    item["entry_source"] = point["source"]
    item["entry_quality_status"] = "continuous_monday_mandatory" if now.weekday() == 0 and not item.get("position_legs") else "continuous_same_week_reentry"
    item["trade_status"] = "open"
    item["exit_price"] = None
    item["exit_captured_at"] = None
    item["exit_source"] = None
    item["exit_reason"] = None
    item["result"] = None
    item["result_value"] = None
    item["result_percent"] = None
    item["risk_status"] = "open_continuous_exposure"
    item["last_risk_review_at"] = point["timestamp"]
    item["continuous_reentry_count"] = len(item.get("position_legs") or [])
    item["risk_plan"] = v2.build_risk_plan(item, float(point["price"]))
    if isinstance(item.get("risk_plan"), dict):
        item["risk_plan"]["execution_layer_version"] = LAYER_VERSION
        item["risk_plan"]["continuous_leg_index"] = item["continuous_reentry_count"] + 1
    _log(item, "open_continuous_leg", direction=item.get("direction"), entry_price=point["price"], reason=decision["reason"], regime=decision["regime"])

    daily_state = week.get("daily_position_review") if isinstance(week.get("daily_position_review"), dict) else {}
    exit_rules = daily_state.get("exit_rules") if isinstance(daily_state.get("exit_rules"), dict) else {}
    exit_rules["same_week_reentry"] = True
    exit_rules["reentry_policy"] = "first_completed_5m_bar_available_to_next_workflow_run"
    daily_state["exit_rules"] = exit_rules
    week["daily_position_review"] = daily_state
    week["continuous_exposure_layer"] = {
        "enabled": True,
        "version": LAYER_VERSION,
        "policy_version": policy_cfg.get("policy_version"),
        "paper_trading_only": True,
        "mandatory_monday_position": True,
        "reentry_after_any_close": True,
        "weekly_candles_used": True,
        "adaptive_closed_leg_learning": True,
        "last_checked_at": now.isoformat(timespec="seconds"),
    }
    write(path, week)

    report["status"] = "opened"
    report["week_id"] = week.get("week_id")
    report["instrument_id"] = instrument_id
    report["entry"] = point
    report["decision"] = decision
    report["actions"].append({"instrument_id": instrument_id, "action": "open_continuous_leg", "direction": decision["direction"], "entry_price": point["price"]})
    write(REPORT_PATH, report)
    return report


def auto_mode() -> None:
    legacy.capture_live_prices()
    if legacy.now_local().weekday() == 6:
        v2.make_forecast()
    # The continuous layer owns entries.  Do not call v2.capture_entries(),
    # because that function correctly enforces the old one-entry Monday window
    # and would therefore overwrite a later same-week re-entry.
    v2.review_open_positions()
    v2.close_due_weeks()
    ensure_exposure()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["auto", "forecast", "open", "close", "render", "ensure-exposure"], default="auto")
    args = parser.parse_args()
    if args.mode == "forecast":
        v2.make_forecast()
        legacy.capture_live_prices()
    elif args.mode in {"open", "ensure-exposure"}:
        legacy.capture_live_prices()
        ensure_exposure()
    elif args.mode == "close":
        legacy.capture_live_prices()
        v2.review_open_positions()
        v2.close_due_weeks()
        ensure_exposure()
    elif args.mode == "render":
        legacy.capture_live_prices()
    else:
        auto_mode()


if __name__ == "__main__":
    main()
