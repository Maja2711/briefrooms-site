#!/usr/bin/env python3
"""Production WES runner with governed NO-TRADE and early-close re-entry admission."""
from __future__ import annotations
import argparse, json
from datetime import timedelta

import investments_weekly as legacy
import investments_weekly_v4 as v4
import investments_wes as wes
import investments_wes_lifecycle as lifecycle


def preflight():
    now = legacy.now_local(); policy = wes.read(wes.POLICY, {}); method = wes.read(wes.METHOD, {})
    path = wes.current_week_path(now)
    report = {"version": wes.VERSION, "mode": "preflight", "checked_at": now.isoformat(timespec="seconds"), "actions": []}
    if not path.exists() or now.weekday() > 4:
        report.update(status="skipped", reason="no_active_week"); wes.write(wes.REPORT, report); return report
    week = wes.read(path, {}); end = wes.exit_time(week, now); remaining = max(0.0, (end-now).total_seconds()/60.0)
    stats = wes.learning_stats(); wes.write(wes.LEARNING, stats)
    items = {str(x.get("instrument_id")): x for x in week.get("instruments") or []}
    changed = False
    for p_cfg in v4.policy_instruments(policy):
        iid = str(p_cfg.get("instrument_id")); item = items.get(iid); cfg = v4.instrument_cfg(method, iid)
        if item is None or not cfg: continue
        if wes.sf(item.get("entry_price")) is not None and wes.sf(item.get("exit_price")) is None: continue

        early_reentry = lifecycle.early_close_reentry_candidate(item, week, now)
        if wes.sf(item.get("exit_price")) is not None and not early_reentry:
            continue
        if early_reentry:
            item["wes_early_reentry_eligible"] = True
            item["wes_early_reentry_source_exit_at"] = item.get("exit_captured_at")
            item["wes_early_reentry_source_exit_reason"] = item.get("exit_reason")
            item["wes_early_reentry_source_risk_status"] = item.get("risk_status")
            item["next_entry_status"] = "wes_early_reentry_monitoring"

        # A normal directional Monday plan is not a late trigger. A Monday/Tuesday
        # close is the explicit exception: it may return to trigger monitoring.
        explicit_no_trade = early_reentry or str(item.get("direction") or "neutral") == "neutral" or str(item.get("trade_status") or "") == "no_trade" or str(item.get("wes_status") or "") in {"no_trade_monitoring_trigger", "trigger_qualified_waiting_governed_v5_entry"}
        if not explicit_no_trade:
            report["actions"].append({"instrument_id": iid, "action": "leave_normal_directional_plan_untouched"})
            continue

        data = wes.governed_candidate(iid, cfg, p_cfg, week, policy, method, now)
        decision = data["decision"]; direction = str(decision.get("direction") or "neutral")
        if early_reentry:
            cls = lifecycle.AUTHORIZATION_TYPE
            profile = lifecycle.early_reentry_trigger_profile(now)
            learning_class = "midweek_trigger"
        else:
            cls = wes.entry_class(now)
            profile = wes.trigger_profile(now, remaining)
            learning_class = cls
        penalty = wes.learning_threshold_penalty(stats, learning_class)
        profile = {**profile, "raw": float(profile["raw"]) + penalty, "learning_threshold_adjustment": penalty}
        raw = abs(float(decision.get("raw_score") or 0.0)); utility = float(decision.get("utility") or 0.0)

        # An early replacement is a genuinely new decision. It must pass the
        # absolute WES thresholds but is not penalized by the score of the leg
        # that was already closed under a different thesis.
        if early_reentry:
            baseline = 0.0
        else:
            baseline = item.get("wes_initial_no_trade_score")
            if baseline is None:
                baseline = abs(float(item.get("score") or 0.0)); item["wes_initial_no_trade_score"] = baseline
        delta = max(0.0, raw - abs(float(baseline or 0.0)))
        approved = bool(profile.get("allowed")) and direction in {"long","short"} and raw >= float(profile["raw"]) and utility >= float(profile["utility"]) and int(data["confirmations"]) >= int(profile["confirmations"]) and delta >= float(profile["delta"])
        candidate = {"direction": direction, "strategy_id": decision.get("strategy_id"), "raw_score": round(raw,4), "utility": round(utility,4), "signal_delta_from_initial": round(delta,4), "confirmations": data["confirmations"], "confirmation_sources": data["confirmation_sources"], "entry_class": cls}
        if approved:
            item["reentry_lock"] = {"active":False,"scope":"wes_no_trade_monitoring","released_at":now.isoformat(timespec="seconds"),"reason":"wes_trigger_qualified"}
            item["wes_status"] = "trigger_qualified_waiting_governed_v5_entry"
            item["wes_entry_authorization"] = {
                "authorized_at":now.isoformat(timespec="seconds"),
                "expires_at":(now+timedelta(minutes=20)).isoformat(timespec="seconds"),
                "authorization_type": lifecycle.AUTHORIZATION_TYPE if early_reentry else "no_trade_trigger",
                "candidate":candidate,
                "required":profile,
                "source_exit_at": item.get("wes_early_reentry_source_exit_at") if early_reentry else None,
                "source_exit_reason": item.get("wes_early_reentry_source_exit_reason") if early_reentry else None,
            }
            report["actions"].append({"instrument_id":iid,"action":"authorize_early_reentry" if early_reentry else "authorize_trigger",**candidate})
        else:
            wes.set_monitoring(item, now, end, candidate, profile)
            if early_reentry:
                item["wes_status"] = "early_close_reentry_monitoring_trigger"
                item["next_entry_status"] = "wes_early_reentry_waiting_for_signal"
            report["actions"].append({"instrument_id":iid,"action":"monitor_early_reentry" if early_reentry else "monitor_no_trade",**candidate,"required":profile})
        changed = True
    week["wes"] = {
        "version":wes.VERSION,
        "objective":"maximize_total_net_profit_with_no_forced_trades",
        "no_trade_is_active_monitoring":True,
        "early_close_reentry":True,
        "early_close_days":["monday","tuesday"],
        "replacement_holding_days":lifecycle.HOLDING_DAYS,
        "replacement_weekend_carry":True,
        "material_event_reentry":False,
        "last_preflight_at":now.isoformat(timespec="seconds")
    }
    if changed: wes.write(path, week)
    report.update(status="completed",week_id=week.get("week_id")); wes.write(wes.REPORT, report); return report


def main():
    p=argparse.ArgumentParser(); p.add_argument('--mode',choices=['preflight','postflight','learning'],default='preflight'); a=p.parse_args()
    if a.mode=='preflight': result=preflight()
    elif a.mode=='postflight': result=lifecycle.postflight()
    else: result=wes.learning_stats(); wes.write(wes.LEARNING,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
