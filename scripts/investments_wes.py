#!/usr/bin/env python3
"""WES — Weekly Engine Star.

Governed paper-only layer for active NO TRADE monitoring, late-week trigger
admission and adaptive TP/SL. WES never sends broker orders. It cooperates with
investments_weekly_v5 by holding a re-entry lock while a neutral instrument is
waiting for a sufficiently strong trigger, then briefly authorizing v5 to make
its normal governed entry decision.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import investments_weekly as legacy
import investments_weekly_v2 as v2
import investments_weekly_v3 as v3
import investments_weekly_v4 as v4
import investments_weekly_v5 as v5
import investments_weekly_macro as macro

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "data/investments/methodology.json"
POLICY = ROOT / "data/investments/multi_instrument_exposure_policy.json"
WEEKLY = ROOT / "data/investments/weekly"
REPORT = ROOT / "data/investments/wes_report.json"
LEARNING = ROOT / "data/investments/wes_learning.json"
VERSION = "WES-1.0.0"

read, write, sf, parse_dt = v4.read, v4.write, v2.sf, v2.parse_dt


def clamp(x: float, low: float, high: float) -> float:
    return max(low, min(high, x))


def current_week_path(now: datetime) -> Path:
    return WEEKLY / f"{legacy.week_id_from_date(now)}.json"


def exit_time(week: Dict[str, Any], now: datetime) -> datetime:
    value = parse_dt((week.get("market_window") or {}).get("exit_target_local"))
    if value:
        return value
    return (now + timedelta(days=(4 - now.weekday()) % 7)).replace(hour=22, minute=0, second=0, microsecond=0)


def entry_class(now: datetime) -> str:
    if now.weekday() == 0 and now.hour < 12:
        return "monday_weekly"
    if now.weekday() == 4:
        return "friday_tactical"
    return "midweek_trigger"


def trigger_profile(now: datetime, remaining_minutes: float) -> Dict[str, Any]:
    """Increasing admission hurdle as the weekly holding horizon shrinks."""
    if now.weekday() == 4:
        if remaining_minutes < 45:
            return {"allowed": False, "reason": "too_little_time_before_friday_close", "raw": 999.0, "utility": 999.0, "confirmations": 99, "delta": 999.0}
        if remaining_minutes <= 90:
            return {"allowed": True, "raw": 80.0, "utility": 14.0, "confirmations": 3, "delta": 28.0}
        if now.hour >= 18:
            return {"allowed": True, "raw": 72.0, "utility": 12.0, "confirmations": 3, "delta": 24.0}
        return {"allowed": True, "raw": 65.0, "utility": 11.0, "confirmations": 3, "delta": 22.0}
    if now.weekday() == 3:
        return {"allowed": True, "raw": 55.0, "utility": 9.5, "confirmations": 2, "delta": 18.0}
    if now.weekday() in {1, 2}:
        return {"allowed": True, "raw": 48.0, "utility": 8.0, "confirmations": 2, "delta": 15.0}
    return {"allowed": True, "raw": 44.0, "utility": 7.5, "confirmations": 2, "delta": 15.0}


def decision_direction_from_score(score: float) -> str:
    return "long" if score > 0 else "short" if score < 0 else "neutral"


def confirmations(direction: str, fresh: Dict[str, Any], weekly: Dict[str, Any], macro_context: Dict[str, Any]) -> Tuple[int, List[str]]:
    names: List[str] = []
    fscore = float(fresh.get("score") or 0.0)
    if decision_direction_from_score(fscore) == direction and abs(fscore) >= 25:
        names.append("daily")
    if weekly.get("data_quality") == "passed":
        wscore = float(weekly.get("score") or 0.0)
        if decision_direction_from_score(wscore) == direction and abs(wscore) >= 15:
            names.append("weekly")
    if macro_context.get("data_quality") == "passed" and str(macro_context.get("direction")) == direction:
        names.append("macro")
    ma = macro_context.get("ma_structure") if isinstance(macro_context.get("ma_structure"), dict) else {}
    mscore = float(ma.get("score") or 0.0)
    if decision_direction_from_score(mscore) == direction and abs(mscore) >= 1:
        names.append("ma_structure")
    return len(set(names)), sorted(set(names))


def governed_candidate(iid: str, cfg: Dict[str, Any], p_cfg: Dict[str, Any], week: Dict[str, Any], policy: Dict[str, Any], method: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    fresh = v2.model_signal(cfg, method, str(week.get("week_id") or ""), now)
    weekly = v3.weekly_candle_signal(cfg, policy)
    regime = str(weekly.get("regime") or "unknown")
    macro_context = macro.context(iid, now, policy)
    selected_leg_learning = v4.learning_stats(iid, regime, policy)
    base = v4.candidate_methods(fresh, weekly, str(p_cfg.get("default_tie_direction") or "long"))
    candidates = macro.apply_to_candidates(iid, base, fresh, weekly, macro_context, policy)
    candidates, contextual = v5.apply_contextual_learning(
        iid, candidates, fresh, policy, weekly=weekly, macro_context=macro_context
    )
    choice_learning = v5.learning_with_candidate_observations(selected_leg_learning, contextual)
    decision = v4.choose(candidates, choice_learning, policy)
    count, sources = confirmations(str(decision.get("direction") or "neutral"), fresh, weekly, macro_context)
    return {
        "decision": decision,
        "fresh": fresh,
        "weekly": weekly,
        "macro": macro_context,
        "contextual": contextual,
        "learning": choice_learning,
        "selected_leg_learning": selected_leg_learning,
        "confirmations": count,
        "confirmation_sources": sources,
    }


def iter_wes_legs() -> Iterable[Dict[str, Any]]:
    for path in sorted(WEEKLY.glob("*.json")):
        week = read(path, {})
        for item in week.get("instruments") or []:
            for leg in item.get("position_legs") or []:
                if not isinstance(leg, dict):
                    continue
                plan = leg.get("risk_plan") if isinstance(leg.get("risk_plan"), dict) else {}
                if str(plan.get("wes_entry_class") or ""):
                    yield leg


def learning_stats() -> Dict[str, Any]:
    out: Dict[str, Any] = {"version": VERSION, "classes": {}}
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for leg in iter_wes_legs():
        plan = leg.get("risk_plan") or {}
        grouped.setdefault(str(plan.get("wes_entry_class")), []).append(leg)
    for cls, rows in grouped.items():
        vals = [float(x.get("net_result_percent")) for x in rows if sf(x.get("net_result_percent")) is not None]
        wins = sum(v > 0 for v in vals)
        tp = sum(str(x.get("exit_reason")) == "take_profit" for x in rows)
        sl = sum(str(x.get("exit_reason")) == "stop_loss" for x in rows)
        out["classes"][cls] = {
            "count": len(vals), "win_rate": round(wins / len(vals), 4) if vals else None,
            "mean_net_percent": round(sum(vals) / len(vals), 6) if vals else None,
            "tp_hit_rate": round(tp / len(rows), 4) if rows else None,
            "sl_hit_rate": round(sl / len(rows), 4) if rows else None,
        }
    out["updated_at"] = legacy.now_local().isoformat(timespec="seconds")
    return out


def learning_threshold_penalty(stats: Dict[str, Any], cls: str) -> float:
    row = (stats.get("classes") or {}).get(cls) or {}
    n = int(row.get("count") or 0)
    if n < 6:
        return 0.0
    mean = float(row.get("mean_net_percent") or 0.0)
    win = float(row.get("win_rate") or 0.0)
    if mean < 0 or win < 0.45:
        return 6.0
    if mean > 0 and win >= 0.58:
        return -3.0
    return 0.0


def set_monitoring(item: Dict[str, Any], now: datetime, until: datetime, candidate: Dict[str, Any], profile: Dict[str, Any]) -> None:
    item["wes_status"] = "no_trade_monitoring_trigger"
    item["wes_methodology"] = VERSION
    item["wes_last_review_at"] = now.isoformat(timespec="seconds")
    item["wes_trigger_candidate"] = candidate
    item["reentry_lock"] = {
        "active": True, "scope": "wes_no_trade_monitoring", "until": until.isoformat(timespec="seconds"),
        "reason": "wes_waiting_for_qualified_trigger", "policy": "NO_TRADE remains active monitoring, not forced exposure",
    }
    pl = "NO TRADE — obserwuję trigger do otwarcia pozycji. WES otworzy pozycję tylko po nowym, wystarczająco silnym i potwierdzonym sygnale; nic na siłę."
    en = "NO TRADE — monitoring for an entry trigger. WES will open only after a new, sufficiently strong and confirmed signal; no forced exposure."
    old_pl = [x for x in (item.get("rationale_pl") or []) if "NO TRADE — obserwuję trigger" not in str(x)]
    old_en = [x for x in (item.get("rationale_en") or []) if "NO TRADE — monitoring" not in str(x)]
    item["rationale_pl"] = [pl] + old_pl[:5]
    item["rationale_en"] = [en] + old_en[:5]
    item["wes_required_trigger"] = profile


def preflight() -> Dict[str, Any]:
    now = legacy.now_local(); policy = read(POLICY, {}); method = read(METHOD, {})
    path = current_week_path(now); report: Dict[str, Any] = {"version": VERSION, "mode": "preflight", "checked_at": now.isoformat(timespec="seconds"), "actions": []}
    if not path.exists() or now.weekday() > 4:
        report["status"] = "skipped"; report["reason"] = "no_active_week"; write(REPORT, report); return report
    week = read(path, {}); end = exit_time(week, now); remaining = max(0.0, (end - now).total_seconds() / 60.0)
    stats = learning_stats(); write(LEARNING, stats)
    items = {str(x.get("instrument_id")): x for x in week.get("instruments") or []}
    changed = False
    for p_cfg in v4.policy_instruments(policy):
        iid = str(p_cfg.get("instrument_id")); item = items.get(iid); cfg = v4.instrument_cfg(method, iid)
        if item is None or not cfg:
            continue
        if sf(item.get("entry_price")) is not None and sf(item.get("exit_price")) is None:
            continue
        # Closed positions stay governed by the existing same-week invalidation/re-entry policy.
        if sf(item.get("exit_price")) is not None:
            continue
        data = governed_candidate(iid, cfg, p_cfg, week, policy, method, now)
        decision = data["decision"]; direction = str(decision.get("direction") or "neutral")
        cls = entry_class(now); profile = trigger_profile(now, remaining)
        penalty = learning_threshold_penalty(stats, cls)
        profile = {**profile, "raw": float(profile["raw"]) + penalty, "learning_threshold_adjustment": penalty}
        raw = abs(float(decision.get("raw_score") or 0.0)); utility = float(decision.get("utility") or 0.0)
        initial = abs(float(item.get("forecast_score") if item.get("forecast_score") is not None else item.get("score") or 0.0))
        delta = max(0.0, raw - initial)
        approved = bool(profile.get("allowed")) and direction in {"long", "short"} and raw >= float(profile["raw"]) and utility >= float(profile["utility"]) and int(data["confirmations"]) >= int(profile["confirmations"]) and delta >= float(profile["delta"])
        candidate = {"direction": direction, "strategy_id": decision.get("strategy_id"), "raw_score": round(raw, 4), "utility": round(utility, 4), "signal_delta_from_initial": round(delta, 4), "confirmations": data["confirmations"], "confirmation_sources": data["confirmation_sources"], "entry_class": cls}
        if approved:
            item["reentry_lock"] = {"active": False, "scope": "wes_no_trade_monitoring", "released_at": now.isoformat(timespec="seconds"), "reason": "wes_trigger_qualified"}
            item["wes_status"] = "trigger_qualified_waiting_governed_v5_entry"
            item["wes_entry_authorization"] = {"authorized_at": now.isoformat(timespec="seconds"), "expires_at": (now + timedelta(minutes=20)).isoformat(timespec="seconds"), "candidate": candidate, "required": profile}
            report["actions"].append({"instrument_id": iid, "action": "authorize_trigger", **candidate})
        else:
            set_monitoring(item, now, end, candidate, profile)
            report["actions"].append({"instrument_id": iid, "action": "monitor_no_trade", **candidate, "required": profile})
        changed = True
    week["wes"] = {"version": VERSION, "objective": "maximize_total_net_profit_with_no_forced_trades", "no_trade_is_active_monitoring": True, "last_preflight_at": now.isoformat(timespec="seconds")}
    if changed: write(path, week)
    report["status"] = "completed"; report["week_id"] = week.get("week_id"); write(REPORT, report); return report


def base_distances(item: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    distance = item.get("risk_distance") if isinstance(item.get("risk_distance"), dict) else {}
    sl = sf(distance.get("stop_price_distance")); tp = sf(distance.get("take_price_distance"))
    if sl and tp and sl > 0 and tp > 0:
        return float(sl), float(tp)
    plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
    sl = sf(plan.get("stop_loss_distance")); tp = sf(plan.get("take_profit_distance"))
    if sl and tp and sl > 0 and tp > 0:
        return float(sl), float(tp)
    return None


def adaptive_distances(base_sl: float, base_tp: float, remaining_hours: float, conviction: float, cls: str, stats: Dict[str, Any]) -> Tuple[float, float, Dict[str, Any]]:
    horizon = clamp(remaining_hours / 110.0, 0.01, 1.0)
    time_factor = math.sqrt(horizon)
    c = clamp(conviction / 100.0, 0.0, 1.0)
    tp_mult = clamp(0.22 + 0.88 * time_factor + 0.22 * c, 0.18, 1.25)
    sl_mult = clamp(0.30 + 0.62 * time_factor - 0.10 * c, 0.24, 1.0)
    row = (stats.get("classes") or {}).get(cls) or {}
    n = int(row.get("count") or 0); mean = float(row.get("mean_net_percent") or 0.0); win = float(row.get("win_rate") or 0.0)
    learned_tp = 1.0
    if n >= 6 and mean > 0 and win >= 0.55: learned_tp = 1.10
    elif n >= 6 and (mean < 0 or win < 0.45): learned_tp = 0.88
    tp_mult *= learned_tp
    if cls == "friday_tactical":
        tp_mult = min(tp_mult, 0.35)
        sl_mult = min(sl_mult, 0.45)
    tp = max(base_tp * tp_mult, 1e-12); sl = max(base_sl * sl_mult, 1e-12)
    target_rr = 1.05 if cls == "friday_tactical" else 1.20
    # Preserve a positive-payoff shape by reducing risk rather than inflating a late-week TP.
    if tp / sl < target_rr:
        sl = tp / target_rr
    meta = {"time_factor": round(time_factor, 4), "conviction_factor": round(c, 4), "tp_multiplier": round(tp / base_tp, 4), "sl_multiplier": round(sl / base_sl, 4), "target_min_rr": target_rr, "learning_count": n, "learning_mean_net_percent": mean if n else None, "learning_win_rate": win if n else None}
    return sl, tp, meta


def build_wes_plan(item: Dict[str, Any], week: Dict[str, Any], now: datetime, stats: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entry = sf(item.get("entry_price")); base = base_distances(item)
    if entry is None or not base or str(item.get("direction")) not in {"long", "short"}:
        return None
    end = exit_time(week, now); remaining_hours = max(0.01, (end - now).total_seconds() / 3600.0)
    auth = item.get("wes_entry_authorization") if isinstance(item.get("wes_entry_authorization"), dict) else {}
    cand = auth.get("candidate") if isinstance(auth.get("candidate"), dict) else {}
    raw = abs(float(cand.get("raw_score") if cand.get("raw_score") is not None else item.get("score") or 0.0))
    cls = str(cand.get("entry_class") or entry_class(parse_dt(item.get("entry_captured_at")) or now))
    sl_dist, tp_dist, meta = adaptive_distances(base[0], base[1], remaining_hours, raw, cls, stats)
    if item.get("direction") == "long": sl, tp = entry - sl_dist, entry + tp_dist
    else: sl, tp = entry + sl_dist, entry - tp_dist
    return {"model_version": VERSION, "generated_at": now.isoformat(timespec="seconds"), "direction": item.get("direction"), "stop_loss_price": round(sl, 8), "take_profit_price": round(tp, 8), "stop_loss_distance": round(sl_dist, 8), "take_profit_distance": round(tp_dist, 8), "reward_to_risk": round(tp_dist / sl_dist, 4), "same_bar_rule": "stop_loss_first_conservative", "wes_entry_class": cls, "objective": "maximize_net_expectancy_not_trade_frequency", "scheduled_exit": end.isoformat(timespec="seconds"), "adaptive_inputs": meta, "friday_tactical_low_tp": cls == "friday_tactical"}


def postflight() -> Dict[str, Any]:
    now = legacy.now_local(); path = current_week_path(now); report = {"version": VERSION, "mode": "postflight", "checked_at": now.isoformat(timespec="seconds"), "actions": []}
    if not path.exists():
        report["status"] = "skipped"; write(REPORT, report); return report
    week = read(path, {}); stats = learning_stats(); write(LEARNING, stats); changed = False
    for item in week.get("instruments") or []:
        if sf(item.get("entry_price")) is not None and sf(item.get("exit_price")) is None and str(item.get("direction")) in {"long", "short"}:
            existing = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
            if existing.get("model_version") != VERSION:
                plan = build_wes_plan(item, week, now, stats)
                if plan:
                    item["risk_plan"] = plan; item["wes_status"] = "open_wes_governed_position"; item["wes_methodology"] = VERSION
                    item["entry_quality_status"] = f"wes_{plan['wes_entry_class']}"
                    report["actions"].append({"instrument_id": item.get("instrument_id"), "action": "freeze_adaptive_risk_plan", "entry_class": plan["wes_entry_class"], "rr": plan["reward_to_risk"], "tp_distance": plan["take_profit_distance"], "sl_distance": plan["stop_loss_distance"]})
                    changed = True
        elif str(item.get("direction") or "neutral") == "neutral":
            item["wes_status"] = "no_trade_monitoring_trigger"
    week.setdefault("wes", {}).update({"version": VERSION, "last_postflight_at": now.isoformat(timespec="seconds"), "dynamic_risk": True, "learning_by_entry_class": True})
    if changed: write(path, week)
    report["status"] = "completed"; report["week_id"] = week.get("week_id"); write(REPORT, report); return report


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--mode", choices=["preflight", "postflight", "learning"], default="preflight"); args = parser.parse_args()
    result = learning_stats() if args.mode == "learning" else postflight() if args.mode == "postflight" else preflight()
    if args.mode == "learning": write(LEARNING, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
