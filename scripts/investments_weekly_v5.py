#!/usr/bin/env python3
"""Governed weekly paper-trading runtime. Never sends broker orders."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import investments_weekly as legacy
import investments_weekly_v2 as v2
import investments_weekly_v3 as v3
import investments_weekly_v4 as v4

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "data/investments/methodology.json"
POLICY = ROOT / "data/investments/multi_instrument_exposure_policy.json"
STATE = ROOT / "data/investments/multi_instrument_exposure_state_v5.json"
REPORT = ROOT / "data/investments/multi_instrument_exposure_report_v5.json"
VERSION = "5.0.0-experimental"

read, write, sf, parse_dt = v4.read, v4.write, v2.sf, v2.parse_dt


def open_position(item: Dict[str, Any]) -> bool:
    return sf(item.get("entry_price")) is not None and sf(item.get("exit_price")) is None and item.get("direction") in {"long", "short"}


def closed_position(item: Dict[str, Any]) -> bool:
    return sf(item.get("entry_price")) is not None and sf(item.get("exit_price")) is not None


def method_row(method: Dict[str, Any], instrument_id: str) -> Dict[str, Any]:
    return next((x for x in method.get("instruments", []) if str(x.get("id")) == instrument_id), {})


def gate(item: Dict[str, Any], method: Dict[str, Any], instrument_id: str) -> Tuple[bool, bool]:
    cfg = method_row(method, instrument_id)
    allowed = bool(cfg.get("enabled_for_new_positions", False))
    reason = str(cfg.get("validation_gate_reason") or "instrument_not_validated")
    if allowed:
        changed = item.get("validation_gate") != "enabled_for_paper_trading" or item.get("validation_gate_reason") is not None
        item["validation_gate"] = "enabled_for_paper_trading"
        item.pop("validation_gate_reason", None)
        return True, changed
    if open_position(item):
        changed = item.get("validation_gate") != "grandfathered_existing_position_no_new_entries"
        item.update(validation_gate="grandfathered_existing_position_no_new_entries", validation_gate_reason=reason)
        return False, changed
    changed = item.get("validation_gate") != reason or item.get("next_entry_status") != "no_trade"
    item.update(validation_gate=reason, validation_gate_reason=reason, next_entry_status="no_trade", pending_entry_decision=None)
    if not closed_position(item):
        item.update(direction="neutral", trade_status="no_trade", entry_price=None, entry_captured_at=None,
                    entry_source=None, risk_plan=None, result="no_trade", result_value=0.0, result_percent=0.0)
    return False, changed


def invalidation_exit(item: Dict[str, Any]) -> bool:
    reason = str(item.get("exit_reason") or "")
    status = str(item.get("risk_status") or "")
    return reason.startswith(("event_review_", "daily_model_")) or status in {
        "closed_by_material_event_review", "closed_by_daily_model_review"
    }


def lock_reentry(item: Dict[str, Any], week: Dict[str, Any], now: datetime) -> Tuple[bool, bool]:
    lock = item.get("reentry_lock") if isinstance(item.get("reentry_lock"), dict) else {}
    until = parse_dt(lock.get("until"))
    if lock.get("active") and until and now < until:
        return True, False
    if lock.get("active"):
        item["reentry_lock"] = {**lock, "active": False, "released_at": now.isoformat(timespec="seconds")}
    if closed_position(item) and invalidation_exit(item):
        end = parse_dt((week.get("market_window") or {}).get("exit_target_local")) or now.replace(hour=23, minute=59, second=59)
        item["reentry_lock"] = {
            "active": True, "scope": "same_week", "until": end.isoformat(timespec="seconds"),
            "reason": item.get("exit_reason") or item.get("risk_status"),
            "policy": "thesis_or_material_event_exit_blocks_same_week_reentry",
        }
        item.update(pending_entry_decision=None, next_entry_status="blocked_after_thesis_invalidation")
        return True, True
    return False, bool(lock.get("active"))


def no_trade(decision: Dict[str, Any], fresh: Dict[str, Any], weekly: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    cfg = policy.get("no_trade") or {}
    if not cfg.get("enabled", True):
        return decision
    base = float(fresh.get("score") or 0)
    week = float(weekly.get("score") or 0) if weekly.get("data_quality") == "passed" else 0.0
    raw = abs(float(decision.get("raw_score") or 0))
    utility = float(decision.get("utility") or 0)
    floor = float(cfg.get("minimum_directional_raw_score") or 35)
    utility_floor = float(cfg.get("minimum_directional_utility") or 6)
    reasons = []
    if fresh.get("data_quality") != "passed" and weekly.get("data_quality") != "passed":
        reasons.append("insufficient_data_quality")
    if max(raw, abs(base), abs(week)) < floor:
        reasons.append("directional_edge_below_threshold")
    if utility < utility_floor:
        reasons.append("selected_utility_below_threshold")
    if base * week < 0 and max(abs(base), abs(week)) < float(cfg.get("conflict_no_trade_below_raw_score") or 45):
        reasons.append("daily_weekly_conflict_without_dominant_edge")
    if not reasons:
        return decision
    return {"strategy_id": "no_trade", "direction": "neutral", "raw_score": 0.0,
            "utility": utility, "reason_codes": reasons, "candidates": decision.get("candidates", {})}


def abstain(item: Dict[str, Any], decision: Dict[str, Any]) -> None:
    item.update(pending_entry_decision=None, next_entry_status="no_trade", no_trade_decision=decision,
                no_trade_reason=",".join(decision.get("reason_codes", [])), continuous_exposure_active=False,
                continuous_exposure_status="no_trade")
    if not closed_position(item):
        item.update(direction="neutral", score=0, trade_status="no_trade", entry_price=None,
                    entry_captured_at=None, entry_source=None, risk_plan=None, result="no_trade",
                    result_value=0.0, result_percent=0.0)


def freeze_decision(item: Dict[str, Any], decision: Dict[str, Any], fresh: Dict[str, Any], weekly: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    frozen = dict(decision)
    frozen.update(decided_at=now.isoformat(timespec="seconds"), validation_gate=item.get("validation_gate"))
    pending = {
        "decided_at": frozen["decided_at"], "entry_not_before": frozen["decided_at"],
        "decision": frozen, "fresh_signal": fresh, "weekly_signal": weekly,
        "rule": "entry_timestamp_must_be_on_or_after_decision_timestamp",
    }
    item.update(pending_entry_decision=pending, trade_status="planned",
                entry_quality_status="waiting_for_first_completed_5m_bar_after_decision")
    return pending


def entry_point(symbol: str, pending: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    decided = parse_dt(pending.get("entry_not_before"))
    if not decided:
        return None
    point = v2.first_bar_at_or_after(symbol, decided, tolerance=timedelta(hours=3))
    captured = parse_dt((point or {}).get("timestamp"))
    return point if point and captured and captured >= decided else None


def ensure_all() -> Dict[str, Any]:
    now = legacy.now_local(); policy = read(POLICY, {}); method = read(METHOD, {})
    report = {"layer_version": VERSION, "checked_at": now.isoformat(timespec="seconds"), "actions": [], "status": "skipped"}
    if not policy.get("enabled"):
        report["reason"] = "policy_disabled"; write(REPORT, report); return report
    path = v4.ensure_week(now, method)
    if not path or not path.exists():
        report["reason"] = "current_week_missing"; write(REPORT, report); return report
    week = read(path, {}); active, reason = v4.active_window(week, now)
    if not active:
        report["reason"] = reason; write(REPORT, report); return report
    week.setdefault("base_method_version", week.get("method_version")); week.setdefault("base_forecast_hash", week.get("forecast_hash"))
    week.update(method_version=VERSION, model_status="experimental_governed_execution_parity_paper_only")
    items = {str(x.get("instrument_id")): x for x in week.get("instruments", [])}
    state = {"layer_version": VERSION, "generated_at": now.isoformat(timespec="seconds"), "instruments": {}}
    changed = False
    for p_cfg in v4.policy_instruments(policy):
        iid = str(p_cfg.get("instrument_id")); cfg = v4.instrument_cfg(method, iid); item = items.get(iid)
        if not cfg or item is None:
            report["actions"].append({"instrument_id": iid, "action": "skip", "reason": "missing_config"}); continue
        if closed_position(item) and v4.archive_leg(item, p_cfg): changed = True
        allowed, c = gate(item, method, iid); changed |= c
        blocked, c = lock_reentry(item, week, now); changed |= c
        if not allowed or blocked:
            state["instruments"][iid] = {"validation_gate": item.get("validation_gate"), "reentry_lock": item.get("reentry_lock")}
            report["actions"].append({"instrument_id": iid, "action": "no_trade", "reason": "gate_or_reentry_lock"}); continue
        fresh = v2.model_signal(cfg, method, str(week.get("week_id") or ""), now)
        weekly = v3.weekly_candle_signal(cfg, policy); regime = str(weekly.get("regime") or "unknown")
        learning = v4.learning_stats(iid, regime, policy)
        candidates = v4.candidate_methods(fresh, weekly, str(p_cfg.get("default_tie_direction") or "long"))
        decision = no_trade(v4.choose(candidates, learning, policy), fresh, weekly, policy)
        state["instruments"][iid] = {"regime": regime, "learning": learning, "decision": decision,
                                     "validation_gate": item.get("validation_gate"), "reentry_lock": item.get("reentry_lock")}
        if open_position(item):
            item.update(continuous_exposure_active=True, continuous_exposure_status="open"); continue
        if decision.get("direction") not in {"long", "short"}:
            abstain(item, decision); changed = True
            report["actions"].append({"instrument_id": iid, "action": "no_trade", "reason_codes": decision.get("reason_codes")}); continue
        saved_pending = item.get("pending_entry_decision")
        pending = saved_pending if isinstance(saved_pending, dict) and isinstance(saved_pending.get("decision"), dict) and saved_pending.get("entry_not_before") else freeze_decision(item, decision, fresh, weekly, now)
        changed = True
        point = entry_point(str(cfg.get("symbol") or ""), pending)
        if not point:
            report["actions"].append({"instrument_id": iid, "action": "defer_entry", "entry_not_before": pending.get("entry_not_before")}); continue
        frozen = pending["decision"]; v4.open_leg(item, cfg, frozen, pending["fresh_signal"], pending["weekly_signal"], point, now)
        item.update(entry_decision_at=pending["decided_at"], entry_execution_rule="first_completed_5m_bar_on_or_after_frozen_decision",
                    pending_entry_decision=None, next_entry_status="open")
        report["actions"].append({"instrument_id": iid, "action": "open", "direction": frozen.get("direction"),
                                  "decision_at": item["entry_decision_at"], "entry_at": item.get("entry_captured_at")})
    week["multi_instrument_exposure_layer"] = {
        "enabled": True, "version": VERSION, "common_validation_gate": True,
        "retroactive_entries_forbidden": True, "same_week_reentry_block_after_invalidation": True,
        "no_trade_first_class": True, "execution_parity_report": "data/investments/weekly_execution_parity_v5.json",
        "last_checked_at": now.isoformat(timespec="seconds"),
    }
    if changed: write(path, week)
    write(STATE, state); report.update(status="completed", week_id=week.get("week_id")); write(REPORT, report); return report


def auto() -> None:
    legacy.capture_live_prices(); now = legacy.now_local(); method = read(METHOD, {})
    if now.weekday() == 6: v2.make_forecast()
    elif now.weekday() <= 4 and not v2.current_week_path(now).exists(): v4.emergency_current_week(now, method)
    v2.review_open_positions(); v2.close_due_weeks(); ensure_all()


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--mode", choices=["auto", "forecast", "close", "ensure-exposure", "render"], default="auto")
    mode = parser.parse_args().mode
    if mode == "forecast": v2.make_forecast(); legacy.capture_live_prices()
    elif mode == "close": legacy.capture_live_prices(); v2.review_open_positions(); v2.close_due_weeks(); ensure_all()
    elif mode == "ensure-exposure": legacy.capture_live_prices(); ensure_all()
    elif mode == "render": legacy.capture_live_prices()
    else: auto()


if __name__ == "__main__": main()
