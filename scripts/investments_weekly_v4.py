#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adaptive continuous paper exposure for EUR/USD, S&P 500 and BTC/USD.

This is a separately versioned research layer. It never sends broker orders.
Every enabled instrument receives a paper position from Monday 08:00 Warsaw
until the scheduled Friday close. Closed legs are archived and re-entry is
attempted after the saved cooldown. Direction is selected by a small tournament
of pre-defined methods; only earlier closed paper legs may affect later choices.
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
import investments_weekly_v3 as v3

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data" / "investments" / "multi_instrument_exposure_policy.json"
STATE_PATH = ROOT / "data" / "investments" / "multi_instrument_exposure_state.json"
REPORT_PATH = ROOT / "data" / "investments" / "multi_instrument_exposure_report.json"
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
METHOD_PATH = ROOT / "data" / "investments" / "methodology.json"
LAYER_VERSION = "4.0.0-experimental"


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


def direction_from_score(score: float, tie: str = "long") -> str:
    if score > 0:
        return "long"
    if score < 0:
        return "short"
    return tie if tie in {"long", "short"} else "long"


def opposite(direction: str) -> str:
    return "short" if direction == "long" else "long"


def instrument_cfg(method: Dict[str, Any], instrument_id: str) -> Optional[Dict[str, Any]]:
    return next((x for x in method.get("instruments") or [] if str(x.get("id")) == instrument_id), None)


def policy_instruments(policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [x for x in policy.get("instruments") or [] if isinstance(x, dict) and x.get("enabled")]


def current_week_bounds(now: datetime) -> Tuple[datetime, datetime]:
    monday = (now - timedelta(days=now.weekday())).replace(hour=8, minute=0, second=0, microsecond=0)
    friday = (monday + timedelta(days=4)).replace(hour=22, minute=0, second=0, microsecond=0)
    return monday, friday


def emergency_current_week(now: datetime, method: Dict[str, Any]) -> Path:
    path = v2.current_week_path(now)
    if path.exists():
        return path
    monday, friday = current_week_bounds(now)
    week_id = legacy.week_id_from_date(now)
    items: List[Dict[str, Any]] = []
    for cfg in method.get("instruments") or []:
        signal = v2.model_signal(cfg, method, week_id, now)
        items.append({
            "instrument_id": cfg.get("id"), "symbol": cfg.get("symbol"),
            "label_pl": cfg.get("label_pl"), "label_en": cfg.get("label_en"),
            **signal, "entry_price": None, "entry_captured_at": None,
            "entry_source": None, "exit_price": None, "exit_captured_at": None,
            "exit_source": None, "exit_reason": None, "risk_plan": None,
            "trade_status": "planned", "result": None, "result_value": None,
            "result_percent": None,
        })
    data: Dict[str, Any] = {
        "week_id": week_id,
        "method_version": v2.MODEL_VERSION,
        "model_status": "emergency_current_week_forecast_created_after_week_start",
        "forecast_created_at": now.isoformat(timespec="seconds"),
        "forecast_locked_at": now.isoformat(timespec="seconds"),
        "forecast_for_week_start": monday.date().isoformat(),
        "forecast_for_week_end": friday.date().isoformat(),
        "timezone": "Europe/Warsaw",
        "market_window": {
            "entry_target_local": monday.isoformat(timespec="seconds"),
            "entry_latest_local": (monday + timedelta(hours=2)).isoformat(timespec="seconds"),
            "exit_target_local": friday.isoformat(timespec="seconds"),
        },
        "instruments": items,
        "forecast_timing_warning": "Created after the weekly window began; not treated as a pre-week frozen forecast.",
    }
    data["forecast_hash"] = v2.forecast_hash(data)
    write(path, data)
    return path


def ensure_week(now: datetime, method: Dict[str, Any]) -> Optional[Path]:
    path = v2.current_week_path(now)
    if path.exists():
        return path
    if now.weekday() == 6:
        made = v2.make_forecast()
        return made if made and made.exists() else None
    if now.weekday() <= 4:
        return emergency_current_week(now, method)
    return None


def active_window(week: Dict[str, Any], now: datetime) -> Tuple[bool, str]:
    if now.weekday() > 4:
        return False, "weekend"
    start = v2.parse_dt((week.get("market_window") or {}).get("entry_target_local"))
    end = v2.parse_dt((week.get("market_window") or {}).get("exit_target_local"))
    if start and now < start:
        return False, "before_monday_entry"
    if end and now >= end:
        return False, "after_friday_close"
    return True, "active"


def candidate_methods(fresh: Dict[str, Any], weekly: Dict[str, Any], tie: str) -> Dict[str, Dict[str, Any]]:
    daily_score = float(fresh.get("score") or 0.0)
    weekly_score = float(weekly.get("score") or 0.0) if weekly.get("data_quality") == "passed" else 0.0
    base_direction = str(fresh.get("direction") or "neutral")
    if base_direction not in {"long", "short"}:
        base_direction = direction_from_score(daily_score, tie)
    signals = fresh.get("signals") if isinstance(fresh.get("signals"), dict) else {}
    last = sf(signals.get("last_close"))
    ema20 = sf(signals.get("ema20"))
    atr14 = sf(signals.get("atr14"))
    mean_rev_score = 0.0
    if last is not None and ema20 is not None and atr14 and atr14 > 0:
        mean_rev_score = -40.0 * clip((last - ema20) / atr14, -1.0, 1.0)
    elif daily_score:
        mean_rev_score = -daily_score
    blend = 0.60 * daily_score + 0.40 * weekly_score
    rows = {
        "base_v2": {"direction": base_direction, "raw_score": daily_score},
        "inverse_v2": {"direction": opposite(base_direction), "raw_score": -daily_score},
        "weekly_trend": {"direction": direction_from_score(weekly_score, tie), "raw_score": weekly_score},
        "daily_weekly_blend": {"direction": direction_from_score(blend, tie), "raw_score": blend},
        "ema_mean_reversion": {"direction": direction_from_score(mean_rev_score, tie), "raw_score": mean_rev_score},
    }
    for row in rows.values():
        row["conviction"] = round(abs(float(row["raw_score"])) * 0.15, 4)
    return rows


def iter_legs(instrument_id: str, limit: int) -> Iterable[Dict[str, Any]]:
    legs: List[Dict[str, Any]] = []
    for path in sorted(WEEKLY_DIR.glob("*.json")):
        week = read(path, {})
        for item in week.get("instruments") or []:
            if str(item.get("instrument_id")) != instrument_id:
                continue
            for leg in item.get("position_legs") or []:
                if isinstance(leg, dict) and leg.get("exit_captured_at"):
                    legs.append(leg)
    return legs[-max(1, limit):]


def learning_stats(instrument_id: str, regime: str, policy: Dict[str, Any]) -> Dict[str, Any]:
    cfg = policy.get("strategy_tournament") or {}
    limit = int(cfg.get("rolling_closed_legs") or 120)
    prior_n = float(cfg.get("prior_observations") or 5)
    minimum = int(cfg.get("minimum_observations_before_performance_weight") or 6)
    perf_weight = float(cfg.get("performance_weight") or 18.0)
    cap = abs(float(cfg.get("maximum_learning_adjustment") or 18.0))
    methods = list(cfg.get("candidate_methods") or [])
    legs = list(iter_legs(instrument_id, limit))
    result: Dict[str, Any] = {}
    for method_id in methods:
        values = [float(x.get("net_result_percent")) for x in legs if x.get("strategy_id") == method_id and x.get("entry_regime") == regime and sf(x.get("net_result_percent")) is not None]
        if len(values) < minimum:
            values = [float(x.get("net_result_percent")) for x in legs if x.get("strategy_id") == method_id and sf(x.get("net_result_percent")) is not None]
        mean = statistics.fmean(values) if values else 0.0
        shrunk = mean * len(values) / (len(values) + prior_n) if values else 0.0
        adjustment = clip(shrunk * perf_weight, -cap, cap) if len(values) >= minimum else 0.0
        result[method_id] = {"count": len(values), "mean_net_percent": round(mean, 6), "shrunk_mean_percent": round(shrunk, 6), "adjustment": round(adjustment, 4)}
    return {"instrument_id": instrument_id, "regime": regime, "closed_legs": len(legs), "methods": result}


def choose(candidates: Dict[str, Dict[str, Any]], learning: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    cfg = policy.get("strategy_tournament") or {}
    explore = float(cfg.get("exploration_bonus") or 2.5)
    enabled = list(cfg.get("candidate_methods") or candidates.keys())
    ranked: List[Tuple[float, str, Dict[str, Any]]] = []
    for method_id in enabled:
        row = dict(candidates.get(method_id) or {})
        if not row:
            continue
        stat = ((learning.get("methods") or {}).get(method_id) or {})
        count = int(stat.get("count") or 0)
        bonus = explore / math.sqrt(count + 1.0)
        utility = float(row.get("conviction") or 0.0) + float(stat.get("adjustment") or 0.0) + bonus
        row.update({"strategy_id": method_id, "learning_count": count, "learning_adjustment": stat.get("adjustment", 0.0), "exploration_bonus": round(bonus, 4), "utility": round(utility, 4)})
        ranked.append((utility, method_id, row))
    ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
    if not ranked:
        return {"strategy_id": "fallback_long", "direction": "long", "raw_score": 0, "utility": 0, "candidates": {}}
    winner = dict(ranked[0][2])
    winner["candidates"] = {method_id: row for _, method_id, row in ranked}
    return winner


def cost_percent(instrument_id: str, entry: float, policy_row: Dict[str, Any]) -> float:
    cost = float(policy_row.get("round_trip_cost") or 0.0)
    unit = str(policy_row.get("cost_unit") or "")
    if instrument_id == "eurusd" or unit == "pips":
        return cost * 0.0001 / entry * 100.0 if entry else 0.0
    if instrument_id == "sp500_futures" or unit == "points":
        return cost / entry * 100.0 if entry else 0.0
    return cost if unit == "percent" else 0.0


def leg_id(item: Dict[str, Any]) -> str:
    raw = "|".join(str(item.get(x) or "") for x in ("instrument_id", "entry_captured_at", "exit_captured_at", "direction"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def archive_leg(item: Dict[str, Any], policy_row: Dict[str, Any]) -> bool:
    entry, exit_price = sf(item.get("entry_price")), sf(item.get("exit_price"))
    if entry is None or exit_price is None or not item.get("entry_captured_at") or not item.get("exit_captured_at"):
        return False
    lid = leg_id(item)
    legs = item.get("position_legs") if isinstance(item.get("position_legs"), list) else []
    if any(str(x.get("leg_id")) == lid for x in legs if isinstance(x, dict)):
        return False
    gross = sf(item.get("result_percent"))
    if gross is None:
        gross = ((exit_price - entry) / entry * 100.0) if item.get("direction") == "long" else ((entry - exit_price) / entry * 100.0)
    decision = item.get("continuous_entry_decision") if isinstance(item.get("continuous_entry_decision"), dict) else {}
    cost = cost_percent(str(item.get("instrument_id")), entry, policy_row)
    legs.append({
        "leg_id": lid, "instrument_id": item.get("instrument_id"), "symbol": item.get("symbol"),
        "direction": item.get("direction"), "strategy_id": decision.get("strategy_id"),
        "entry_regime": decision.get("regime") or "unknown", "entry_decision": decision,
        "entry_price": entry, "entry_captured_at": item.get("entry_captured_at"), "entry_source": item.get("entry_source"),
        "exit_price": exit_price, "exit_captured_at": item.get("exit_captured_at"), "exit_source": item.get("exit_source"),
        "exit_reason": item.get("exit_reason"), "gross_result_percent": round(gross, 6),
        "estimated_round_trip_cost_percent": round(cost, 6), "net_result_percent": round(gross - cost, 6),
        "risk_plan": item.get("risk_plan"), "archived_at": legacy.now_local().isoformat(timespec="seconds"),
    })
    item["position_legs"] = legs
    item["continuous_last_exit_at"] = item.get("exit_captured_at")
    item["continuous_last_closed_leg_id"] = lid
    return True


def cooldown_passed(item: Dict[str, Any], now: datetime, policy: Dict[str, Any]) -> bool:
    last = v2.parse_dt(item.get("continuous_last_exit_at") or item.get("exit_captured_at"))
    minutes = max(0, int(policy.get("minimum_minutes_after_exit") or 5))
    return last is None or now >= last + timedelta(minutes=minutes)


def latest_mark(cfg: Dict[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
    return v3.last_completed_5m_bar(str(cfg.get("symbol") or ""), now)


def analysis_row(item: Dict[str, Any], decision: Dict[str, Any], fresh: Dict[str, Any], weekly: Dict[str, Any], point: Optional[Dict[str, Any]], now: datetime, reason: str) -> Dict[str, Any]:
    entry, mark = sf(item.get("entry_price")), sf((point or {}).get("price"))
    unrealized = None
    if entry and mark and item.get("direction") in {"long", "short"}:
        unrealized = ((mark - entry) / entry * 100.0) if item.get("direction") == "long" else ((entry - mark) / entry * 100.0)
    method_id = str(decision.get("strategy_id") or "unknown")
    direction = str(decision.get("direction") or "neutral")
    return {
        "review_date": now.date().isoformat(), "reviewed_at": now.isoformat(timespec="seconds"),
        "reason": reason, "selected_method": method_id, "selected_direction": direction,
        "current_open_direction": item.get("direction"), "current_price": mark,
        "unrealized_percent": round(unrealized, 4) if unrealized is not None else None,
        "base_v2_direction": fresh.get("direction"), "base_v2_score": fresh.get("score"),
        "weekly_score": weekly.get("score"), "weekly_regime": weekly.get("regime"),
        "selected_utility": decision.get("utility"), "learning_adjustment": decision.get("learning_adjustment"),
        "summary_pl": f"Metoda {method_id} wskazuje {direction}. Score bazowy: {fresh.get('score', 0)}, tygodniowy: {weekly.get('score', 0)}. Reżim: {weekly.get('regime', 'unknown')}.",
        "summary_en": f"Method {method_id} indicates {direction}. Base score: {fresh.get('score', 0)}, weekly score: {weekly.get('score', 0)}. Regime: {weekly.get('regime', 'unknown')}.",
    }


def save_analysis(item: Dict[str, Any], row: Dict[str, Any], policy: Dict[str, Any], force: bool = False) -> bool:
    cfg = policy.get("daily_analysis") or {}
    if not cfg.get("enabled", True):
        return False
    rows = item.get("daily_trading_analysis") if isinstance(item.get("daily_trading_analysis"), list) else []
    same_day = rows and str(rows[-1].get("review_date")) == str(row.get("review_date"))
    if same_day and not force:
        return False
    rows.append(row)
    item["daily_trading_analysis"] = rows[-max(1, int(cfg.get("max_saved_reviews_per_instrument") or 30)):]
    item["latest_daily_analysis"] = row
    return True


def close_for_switch(item: Dict[str, Any], point: Dict[str, Any], now: datetime) -> None:
    item["exit_price"] = point["price"]
    item["exit_captured_at"] = point["timestamp"]
    item["exit_source"] = point["source"]
    item["exit_reason"] = "v4_daily_strategy_direction_switch"
    item["trade_status"] = "closed"
    item["risk_status"] = "closed_by_v4_daily_strategy_review"
    v2.set_result(item, float(point["price"]))


def open_leg(item: Dict[str, Any], cfg: Dict[str, Any], decision: Dict[str, Any], fresh: Dict[str, Any], weekly: Dict[str, Any], point: Dict[str, Any], now: datetime) -> None:
    item["forecast_direction"] = item.get("forecast_direction", item.get("direction"))
    item["forecast_score"] = item.get("forecast_score", item.get("score"))
    item["direction"] = decision["direction"]
    item["score"] = round(float(decision.get("raw_score") or 0.0), 4)
    item["continuous_entry_decision"] = {**decision, "regime": weekly.get("regime") or "unknown", "weekly_features": weekly, "fresh_v2_signal": fresh}
    item["continuous_entry_regime"] = weekly.get("regime") or "unknown"
    item["entry_price"] = point["price"]
    item["entry_captured_at"] = point["timestamp"]
    item["entry_source"] = point["source"]
    item["entry_quality_status"] = "v4_mandatory_monday" if now.weekday() == 0 and not item.get("position_legs") else "v4_same_week_reentry"
    item["trade_status"] = "open"
    item["exit_price"] = None; item["exit_captured_at"] = None; item["exit_source"] = None; item["exit_reason"] = None
    item["result"] = None; item["result_value"] = None; item["result_percent"] = None
    item["risk_distance"] = fresh.get("risk_distance") or item.get("risk_distance")
    item["risk_plan"] = v2.build_risk_plan(item, float(point["price"]))
    item["risk_status"] = "open_multi_instrument_continuous_exposure"
    item["continuous_exposure_active"] = True
    item["continuous_exposure_status"] = "open"
    item["continuous_exposure_policy_version"] = "4.0.0"
    item["continuous_reentry_count"] = len(item.get("position_legs") or [])
    item["last_risk_review_at"] = point["timestamp"]


def ensure_all() -> Dict[str, Any]:
    now = legacy.now_local()
    policy = read(POLICY_PATH, {})
    method = read(METHOD_PATH, {})
    report: Dict[str, Any] = {"layer_version": LAYER_VERSION, "checked_at": now.isoformat(timespec="seconds"), "actions": [], "status": "skipped"}
    if not policy.get("enabled"):
        report["reason"] = "policy_disabled"; write(REPORT_PATH, report); return report
    if not ((policy.get("learning_guardrails") or {}).get("paper_trading_only", True)):
        raise RuntimeError("v4 may run only in paper-trading mode")
    path = ensure_week(now, method)
    if path is None or not path.exists():
        report["reason"] = "current_week_missing"; write(REPORT_PATH, report); return report
    week = read(path, {})
    active, reason = active_window(week, now)
    if not active:
        report["reason"] = reason; write(REPORT_PATH, report); return report
    if not week.get("base_method_version"):
        week["base_method_version"] = week.get("method_version")
        week["base_forecast_hash"] = week.get("forecast_hash")
    week["method_version"] = LAYER_VERSION
    week["model_status"] = "experimental_multi_instrument_continuous_paper_only"
    by_id = {str(x.get("instrument_id")): x for x in week.get("instruments") or []}
    changed = False
    state: Dict[str, Any] = {"layer_version": LAYER_VERSION, "generated_at": now.isoformat(timespec="seconds"), "instruments": {}}
    review_hour = int((policy.get("daily_analysis") or {}).get("review_after_local_hour") or 23)

    for p_row in policy_instruments(policy):
        instrument_id = str(p_row.get("instrument_id"))
        cfg = instrument_cfg(method, instrument_id)
        item = by_id.get(instrument_id)
        if not cfg or item is None:
            report["actions"].append({"instrument_id": instrument_id, "action": "skip", "reason": "config_or_forecast_missing"})
            continue
        if sf(item.get("entry_price")) is not None and sf(item.get("exit_price")) is not None:
            if archive_leg(item, p_row):
                changed = True
                report["actions"].append({"instrument_id": instrument_id, "action": "archive_closed_leg", "leg_id": item.get("continuous_last_closed_leg_id")})
        fresh = v2.model_signal(cfg, method, str(week.get("week_id") or ""), now)
        weekly = v3.weekly_candle_signal(cfg, policy)
        regime = str(weekly.get("regime") or "unknown")
        learn = learning_stats(instrument_id, regime, policy)
        candidates = candidate_methods(fresh, weekly, str(p_row.get("default_tie_direction") or "long"))
        decision = choose(candidates, learn, policy)
        state["instruments"][instrument_id] = {"regime": regime, "learning": learn, "decision": decision}
        point = latest_mark(cfg, now)
        is_open = sf(item.get("entry_price")) is not None and sf(item.get("exit_price")) is None and item.get("direction") in {"long", "short"}
        latest_review = item.get("latest_daily_analysis") if isinstance(item.get("latest_daily_analysis"), dict) else {}
        due_daily = now.hour >= review_hour and str(latest_review.get("review_date") or "") != now.date().isoformat()

        if is_open and due_daily and decision.get("direction") != item.get("direction") and point is not None:
            row = analysis_row(item, decision, fresh, weekly, point, now, "daily_strategy_direction_switch")
            save_analysis(item, row, policy, force=True)
            close_for_switch(item, point, now)
            archive_leg(item, p_row)
            changed = True
            is_open = False
            report["actions"].append({"instrument_id": instrument_id, "action": "close_for_daily_strategy_switch", "new_direction": decision.get("direction"), "price": point.get("price")})
        elif due_daily:
            row = analysis_row(item, decision, fresh, weekly, point, now, "scheduled_daily_analysis")
            if save_analysis(item, row, policy):
                changed = True
                report["actions"].append({"instrument_id": instrument_id, "action": "save_daily_analysis", "selected_method": decision.get("strategy_id")})

        if is_open:
            item["continuous_exposure_active"] = True
            item["continuous_exposure_status"] = "open"
            continue
        if not cooldown_passed(item, now, policy):
            report["actions"].append({"instrument_id": instrument_id, "action": "defer_reentry", "reason": "cooldown"})
            continue
        if point is None:
            report["actions"].append({"instrument_id": instrument_id, "action": "defer_entry", "reason": "completed_5m_bar_unavailable"})
            continue
        open_leg(item, cfg, decision, fresh, weekly, point, now)
        row = analysis_row(item, decision, fresh, weekly, point, now, "entry_or_reentry")
        save_analysis(item, row, policy, force=True)
        changed = True
        report["actions"].append({"instrument_id": instrument_id, "action": "open", "direction": decision.get("direction"), "strategy_id": decision.get("strategy_id"), "price": point.get("price")})

    week["multi_instrument_exposure_layer"] = {
        "enabled": True, "version": LAYER_VERSION, "policy_version": policy.get("policy_version"),
        "paper_trading_only": True, "mandatory_monday_positions": True,
        "reentry_after_any_close": True, "weekly_candles_used": True,
        "strategy_tournament": True, "daily_analysis": True,
        "last_checked_at": now.isoformat(timespec="seconds"),
    }
    if changed:
        write(path, week)
    write(STATE_PATH, state)
    report["status"] = "completed"
    report["week_id"] = week.get("week_id")
    write(REPORT_PATH, report)
    return report


def auto_mode() -> None:
    legacy.capture_live_prices()
    now = legacy.now_local()
    method = read(METHOD_PATH, {})
    if now.weekday() == 6:
        v2.make_forecast()
    elif now.weekday() <= 4 and not v2.current_week_path(now).exists():
        emergency_current_week(now, method)
    v2.review_open_positions()
    v2.close_due_weeks()
    ensure_all()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["auto", "forecast", "close", "ensure-exposure", "render"], default="auto")
    args = parser.parse_args()
    if args.mode == "forecast":
        v2.make_forecast(); legacy.capture_live_prices()
    elif args.mode == "close":
        legacy.capture_live_prices(); v2.review_open_positions(); v2.close_due_weeks(); ensure_all()
    elif args.mode == "ensure-exposure":
        legacy.capture_live_prices(); ensure_all()
    elif args.mode == "render":
        legacy.capture_live_prices()
    else:
        auto_mode()


if __name__ == "__main__":
    print(json.dumps(ensure_all() if False else (auto_mode() or {"status": "auto_completed"}), ensure_ascii=False))
