#!/usr/bin/env python3
"""Production WES runner with governed NO-TRADE and early-close re-entry admission."""
from __future__ import annotations
import argparse, json
from datetime import timedelta

import investments_weekly as legacy
import investments_weekly_v4 as v4
import investments_wes as wes
import investments_wes_guarded_lifecycle as lifecycle


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

        # WES 1.1: every new entry, including the first Monday entry, must
        # pass the same directional-admission authority. A directional forecast
        # is evidence, not an execution authorization.
        initial_plan = (
            not early_reentry
            and wes.sf(item.get("entry_price")) is None
            and item.get("wes_initial_admission_evaluated") is not True
            and str(item.get("forecast_direction") or item.get("direction") or "neutral") in {"long", "short"}
        )

        data = wes.governed_candidate(iid, cfg, p_cfg, week, policy, method, now)
        decision = data["decision"]; direction = str(decision.get("direction") or "neutral")
        admission_meta = decision.get("directional_admission") if isinstance(decision.get("directional_admission"), dict) else {}

        if early_reentry:
            cls = lifecycle.AUTHORIZATION_TYPE
            profile = lifecycle.early_reentry_trigger_profile(now)
            learning_class = "midweek_trigger"
            authorization_type = lifecycle.AUTHORIZATION_TYPE
        elif initial_plan:
            cls = "monday_weekly"
            base_profile = (policy.get("directional_admission") or {}).get("initial_weekly_profile") or {}
            profile = {
                "allowed": now.weekday() <= 4,
                "raw": float(base_profile.get("raw") or 35.0),
                "utility": float(base_profile.get("utility") or 6.0),
                "confirmations": int(base_profile.get("confirmations") or 2),
                "delta": float(base_profile.get("delta") or 0.0),
                "profile": "initial_weekly_entry",
            }
            learning_class = "monday_weekly"
            authorization_type = "initial_weekly_entry"
        else:
            cls = wes.entry_class(now)
            profile = wes.trigger_profile(now, remaining)
            learning_class = cls
            authorization_type = "no_trade_trigger"

        penalty = wes.learning_threshold_penalty(stats, learning_class)
        profile = {**profile, "raw": float(profile["raw"]) + penalty, "learning_threshold_adjustment": penalty}
        raw = abs(float(decision.get("raw_score") or 0.0)); utility = float(decision.get("utility") or 0.0)

        if early_reentry or initial_plan:
            baseline = 0.0
        else:
            baseline = item.get("wes_initial_no_trade_score")
            if baseline is None:
                baseline = abs(float(item.get("score") or 0.0))
                item["wes_initial_no_trade_score"] = baseline
        delta = max(0.0, raw - abs(float(baseline or 0.0)))

        approved = (
            bool(profile.get("allowed"))
            and direction in {"long", "short"}
            and str(decision.get("execution_authority") or "") == "champion_execution"
            and admission_meta.get("passed") is True
            and raw >= float(profile["raw"])
            and utility >= float(profile["utility"])
            and int(data["confirmations"]) >= int(profile["confirmations"])
            and delta >= float(profile["delta"])
        )
        candidate = {
            "direction": direction,
            "strategy_id": decision.get("strategy_id"),
            "raw_score": round(raw, 4),
            "utility": round(utility, 4),
            "signal_delta_from_initial": round(delta, 4),
            "confirmations": data["confirmations"],
            "confirmation_sources": data["confirmation_sources"],
            "entry_class": cls,
            "execution_authority": decision.get("execution_authority"),
            "directional_admission": admission_meta,
        }

        if initial_plan:
            item["wes_initial_admission_evaluated"] = True
            item["wes_initial_admission_at"] = now.isoformat(timespec="seconds")

        if approved:
            item["reentry_lock"] = {
                "active": False,
                "scope": "wes_directional_admission",
                "released_at": now.isoformat(timespec="seconds"),
                "reason": "wes_1_1_directional_admission_qualified",
            }
            item["wes_status"] = (
                "initial_directional_admission_authorized"
                if initial_plan
                else "trigger_qualified_waiting_governed_v5_entry"
            )
            item["wes_entry_authorization"] = {
                "authorized_at": now.isoformat(timespec="seconds"),
                "expires_at": (now + timedelta(minutes=int((policy.get("directional_admission") or {}).get("authorization_ttl_minutes") or 20))).isoformat(timespec="seconds"),
                "authorization_type": authorization_type,
                "directional_admission_passed": True,
                "candidate": candidate,
                "required": profile,
                "source_exit_at": item.get("wes_early_reentry_source_exit_at") if early_reentry else None,
                "source_exit_reason": item.get("wes_early_reentry_source_exit_reason") if early_reentry else None,
            }
            report["actions"].append({
                "instrument_id": iid,
                "action": "authorize_initial_entry" if initial_plan else "authorize_early_reentry" if early_reentry else "authorize_trigger",
                **candidate,
            })
        else:
            wes.set_monitoring(item, now, end, candidate, profile)
            item["wes_entry_authorization"] = None
            if initial_plan:
                item["direction"] = "neutral"
                item["trade_status"] = "no_trade"
                item["next_entry_status"] = "no_trade"
                item["wes_status"] = "initial_directional_admission_rejected_monitoring"
            elif early_reentry:
                item["wes_status"] = "early_close_reentry_monitoring_trigger"
                item["next_entry_status"] = "wes_early_reentry_waiting_for_signal"
            report["actions"].append({
                "instrument_id": iid,
                "action": "reject_initial_entry" if initial_plan else "monitor_early_reentry" if early_reentry else "monitor_no_trade",
                **candidate,
                "decision_reason_codes": decision.get("reason_codes"),
                "required": profile,
            })
        changed = True
    week["wes"] = {
        "version":wes.VERSION,
        "objective":"maximize_total_net_profit_with_no_forced_trades",
        "directional_admission_for_all_entries":True,
        "champion_challenger_hardening":True,
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


def postflight():
    """Use rolling-plan postflight only for a newly opened replacement leg."""
    now = legacy.now_local(); path = wes.current_week_path(now)
    if not path.exists():
        return wes.postflight()
    week = wes.read(path, {})
    for item in week.get("instruments") or []:
        if not isinstance(item, dict):
            continue
        open_position = wes.sf(item.get("entry_price")) is not None and wes.sf(item.get("exit_price")) is None and str(item.get("direction") or "") in {"long", "short"}
        if not open_position:
            continue
        auth = item.get("wes_entry_authorization") if isinstance(item.get("wes_entry_authorization"), dict) else {}
        plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
        if str(auth.get("authorization_type") or "") == lifecycle.AUTHORIZATION_TYPE and plan.get("model_version") != wes.VERSION:
            return lifecycle.postflight()
    return wes.postflight()


def main():
    p=argparse.ArgumentParser(); p.add_argument('--mode',choices=['preflight','postflight','learning'],default='preflight'); a=p.parse_args()
    if a.mode=='preflight': result=preflight()
    elif a.mode=='postflight': result=postflight()
    else: result=wes.learning_stats(); wes.write(wes.LEARNING,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
