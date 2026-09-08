#!/usr/bin/env python3
"""WES lifecycle rules for early-week replacement entries and rolling exits.

A position closed on Monday or Tuesday may become eligible for a fresh WES
replacement entry later in the same trading week. The replacement is never
forced: it still needs a new qualified WES signal, the shared validation gate
and a non-NO_TRADE decision. A qualified replacement receives a seven-calendar-
day holding deadline measured from its actual entry timestamp, so it may remain
open through the weekend. Material-event exits remain fail-closed and are not
eligible for this re-entry path.

This module wraps the v5 paper runtime instead of weakening its common admission
and execution checks. It also makes settlement deadline-aware per position so a
normal weekly leg still closes at the frozen Friday deadline while a qualified
early-close replacement uses its own rolling deadline.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import investments_weekly as legacy
import investments_weekly_v2 as v2
import investments_weekly_v4 as v4
import investments_weekly_v5 as v5
import investments_wes as wes

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
HOLDING_DAYS = 7
EARLY_CLOSE_WEEKDAYS = {0, 1}  # Monday, Tuesday in Europe/Warsaw.
HOLDING_POLICY = "early_close_reentry_7_calendar_days"
AUTHORIZATION_TYPE = "early_close_reentry"
_BASE_LOCK_REENTRY = v5.lock_reentry

sf = v2.sf
parse_dt = v2.parse_dt


def _material_event_exit(item: Dict[str, Any]) -> bool:
    reason = str(item.get("exit_reason") or "")
    status = str(item.get("risk_status") or "")
    return reason.startswith("event_review_") or status == "closed_by_material_event_review"


def early_close_reentry_candidate(item: Dict[str, Any], week: Dict[str, Any], now: datetime) -> bool:
    """Return True for a Monday/Tuesday close that may re-enter via fresh WES admission."""
    if sf(item.get("entry_price")) is None or sf(item.get("exit_price")) is None:
        return False
    if _material_event_exit(item):
        return False
    exit_at = parse_dt(item.get("exit_captured_at"))
    if exit_at is None or exit_at.weekday() not in EARLY_CLOSE_WEEKDAYS:
        return False
    week_id = str(week.get("week_id") or "")
    if week_id and legacy.week_id_from_date(exit_at) != week_id:
        return False
    end = parse_dt((week.get("market_window") or {}).get("exit_target_local"))
    local_now = now.astimezone(v2.TZ) if now.tzinfo else now.replace(tzinfo=v2.TZ)
    if local_now.weekday() > 4 or (end is not None and local_now >= end):
        return False
    return True


def early_reentry_trigger_profile(now: datetime) -> Dict[str, Any]:
    """Full-horizon trigger profile for an early-close replacement.

    The replacement can live for seven days, therefore Friday is not treated as
    a short-horizon tactical trade and its TP is not compressed solely because
    the original weekly Friday deadline is near.
    """
    return {
        "allowed": now.weekday() <= 4,
        "raw": 48.0,
        "utility": 8.0,
        "confirmations": 2,
        "delta": 15.0,
        "profile": AUTHORIZATION_TYPE,
        "holding_horizon_days": HOLDING_DAYS,
    }


def _authorization(item: Dict[str, Any]) -> Dict[str, Any]:
    value = item.get("wes_entry_authorization")
    return value if isinstance(value, dict) else {}


def early_reentry_authorized(item: Dict[str, Any], now: datetime) -> bool:
    auth = _authorization(item)
    if str(auth.get("authorization_type") or "") != AUTHORIZATION_TYPE:
        return False
    expires = parse_dt(auth.get("expires_at"))
    if expires is None or now >= expires:
        return False
    candidate = auth.get("candidate") if isinstance(auth.get("candidate"), dict) else {}
    return str(candidate.get("direction") or "") in {"long", "short"}


def governed_lock_reentry(item: Dict[str, Any], week: Dict[str, Any], now: datetime) -> Tuple[bool, bool]:
    """Bypass the old same-week invalidation lock only after fresh WES authorization."""
    if item.get("wes_early_reentry_eligible") is True and early_reentry_authorized(item, now):
        changed = False
        lock = item.get("reentry_lock") if isinstance(item.get("reentry_lock"), dict) else {}
        if lock.get("active"):
            item["reentry_lock"] = {
                **lock,
                "active": False,
                "released_at": now.isoformat(timespec="seconds"),
                "release_reason": "fresh_wes_early_close_reentry_authorization",
            }
            changed = True
        if item.get("next_entry_status") != "wes_early_reentry_authorized":
            item["next_entry_status"] = "wes_early_reentry_authorized"
            changed = True
        return False, changed
    return _BASE_LOCK_REENTRY(item, week, now)


def install_v5_hooks() -> None:
    if v5.lock_reentry is not governed_lock_reentry:
        v5.lock_reentry = governed_lock_reentry


def rolling_deadline(entry_at: datetime) -> datetime:
    return entry_at + timedelta(days=HOLDING_DAYS)


def valid_rolling_deadline(item: Dict[str, Any]) -> Optional[datetime]:
    if item.get("wes_weekend_carry_allowed") is not True:
        return None
    if str(item.get("wes_holding_policy") or "") != HOLDING_POLICY:
        return None
    if item.get("wes_early_reentry_qualified") is not True:
        return None
    entry_at = parse_dt(item.get("entry_captured_at"))
    deadline = parse_dt(item.get("wes_holding_deadline_local"))
    source_exit = parse_dt(item.get("wes_early_reentry_source_exit_at"))
    if entry_at is None or deadline is None or source_exit is None:
        return None
    if source_exit.weekday() not in EARLY_CLOSE_WEEKDAYS or source_exit >= entry_at:
        return None
    expected = rolling_deadline(entry_at)
    if abs((deadline - expected).total_seconds()) > 300:
        return None
    return deadline


def position_deadline(week: Dict[str, Any], item: Dict[str, Any]) -> Optional[datetime]:
    rolling = valid_rolling_deadline(item)
    if rolling is not None:
        return rolling
    return parse_dt((week.get("market_window") or {}).get("exit_target_local"))


def apply_open_reentry_metadata(path: Optional[Path] = None) -> bool:
    """Persist the rolling seven-day deadline immediately after a qualified entry."""
    path = path or v2.current_week_path()
    week = v4.read(path, {})
    if not week:
        return False
    changed = False
    for item in week.get("instruments") or []:
        if not isinstance(item, dict):
            continue
        if not v5.open_position(item):
            continue
        auth = _authorization(item)
        if str(auth.get("authorization_type") or "") != AUTHORIZATION_TYPE:
            continue
        entry_at = parse_dt(item.get("entry_captured_at"))
        source_exit = parse_dt(item.get("wes_early_reentry_source_exit_at"))
        if entry_at is None or source_exit is None or source_exit.weekday() not in EARLY_CLOSE_WEEKDAYS:
            continue
        deadline = rolling_deadline(entry_at)
        deadline_text = deadline.isoformat(timespec="seconds")
        updates = {
            "wes_early_reentry_eligible": True,
            "wes_early_reentry_qualified": True,
            "wes_weekend_carry_allowed": True,
            "wes_holding_policy": HOLDING_POLICY,
            "wes_holding_deadline_local": deadline_text,
            "wes_status": "open_wes_early_reentry_position",
            "next_entry_status": "open",
        }
        for key, value in updates.items():
            if item.get(key) != value:
                item[key] = value
                changed = True
        plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
        desired_plan = {
            **plan,
            "scheduled_exit": deadline_text,
            "holding_policy": HOLDING_POLICY,
            "weekend_carry_allowed": True,
        }
        if desired_plan != plan:
            item["risk_plan"] = desired_plan
            changed = True
    if changed:
        week.setdefault("wes", {}).update({
            "early_close_reentry": True,
            "early_close_days": ["monday", "tuesday"],
            "replacement_holding_days": HOLDING_DAYS,
            "replacement_weekend_carry": True,
            "material_event_reentry": False,
        })
        v4.write(path, week)
    return changed


def review_open_positions_all(now: Optional[datetime] = None) -> bool:
    """Keep SL/TP monitoring active for rolling positions after their source week ends."""
    _ = now or legacy.now_local()
    changed = False
    for path in sorted(WEEKLY_DIR.glob("*.json"))[-8:]:
        week = v4.read(path, {})
        if not any(v5.open_position(item) for item in week.get("instruments", []) if isinstance(item, dict)):
            continue
        changed = v2.review_open_positions(path) or changed
    return changed


def settle_due_positions(now: Optional[datetime] = None) -> bool:
    """Settle each open leg at its own governed deadline."""
    now = now or legacy.now_local()
    changed_any = False
    for path in sorted(WEEKLY_DIR.glob("*.json"))[-8:]:
        week = v4.read(path, {})
        weekly_deadline = parse_dt((week.get("market_window") or {}).get("exit_target_local"))
        if not week or weekly_deadline is None:
            continue
        changed = False
        for item in week.get("instruments") or []:
            if not isinstance(item, dict):
                continue
            deadline = position_deadline(week, item)
            if deadline is None or now < deadline:
                continue
            side = str(item.get("direction") or "neutral")
            if side == "neutral":
                if now >= weekly_deadline and item.get("result") != "no_trade":
                    item.update(result="no_trade", result_value=0.0, result_percent=0.0, trade_status="no_trade")
                    changed = True
                if now >= weekly_deadline and v2.mark_exposure_closed(item):
                    changed = True
                continue
            entry = sf(item.get("entry_price"))
            if entry is None:
                continue
            if sf(item.get("exit_price")) is not None:
                if v2.mark_exposure_closed(item):
                    changed = True
                continue
            symbol = v2.canonical_yahoo_symbol(str(item.get("instrument_id") or ""), str(item.get("symbol") or ""))
            point = v2.first_bar_at_or_after(symbol, deadline)
            if point is None:
                item["close_quality_status"] = "target_close_price_unavailable_no_live_fallback"
                changed = True
                continue
            is_rolling = valid_rolling_deadline(item) is not None
            item["exit_price"] = point["price"]
            item["exit_captured_at"] = point["timestamp"]
            item["exit_source"] = point["source"]
            item["exit_reason"] = "wes_rolling_holding_deadline" if is_rolling else "scheduled_week_close"
            item["exit_execution_model"] = "first_5m_bar_at_or_after_governed_deadline"
            item["close_quality_status"] = "valid_target_bar"
            item["trade_status"] = "closed"
            v2.mark_exposure_closed(item)
            v2.set_result(item, float(point["price"]))
            if is_rolling:
                item["wes_status"] = "closed_wes_early_reentry_deadline"
            changed = True
        if changed:
            week["weekly_close_audit"] = {
                "status": "applied_per_position_governed_deadline",
                "checked_at": now.isoformat(timespec="seconds"),
                "default_weekly_target": weekly_deadline.isoformat(timespec="seconds"),
                "rolling_wes_deadlines_supported": True,
            }
            v4.write(path, week)
            changed_any = True
    return changed_any


def build_wes_plan(item: Dict[str, Any], week: Dict[str, Any], now: datetime, stats: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entry = sf(item.get("entry_price"))
    base = wes.base_distances(item)
    if entry is None or not base or str(item.get("direction")) not in {"long", "short"}:
        return None
    end = position_deadline(week, item) or wes.exit_time(week, now)
    remaining_hours = max(0.01, (end - now).total_seconds() / 3600.0)
    auth = _authorization(item)
    cand = auth.get("candidate") if isinstance(auth.get("candidate"), dict) else {}
    raw = abs(float(cand.get("raw_score") if cand.get("raw_score") is not None else item.get("score") or 0.0))
    cls = str(cand.get("entry_class") or wes.entry_class(parse_dt(item.get("entry_captured_at")) or now))
    sl_dist, tp_dist, meta = wes.adaptive_distances(base[0], base[1], remaining_hours, raw, cls, stats)
    if item.get("direction") == "long":
        sl, tp = entry - sl_dist, entry + tp_dist
    else:
        sl, tp = entry + sl_dist, entry - tp_dist
    return {
        "model_version": wes.VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "direction": item.get("direction"),
        "stop_loss_price": round(sl, 8),
        "take_profit_price": round(tp, 8),
        "stop_loss_distance": round(sl_dist, 8),
        "take_profit_distance": round(tp_dist, 8),
        "reward_to_risk": round(tp_dist / sl_dist, 4),
        "same_bar_rule": "stop_loss_first_conservative",
        "wes_entry_class": cls,
        "objective": "maximize_net_expectancy_not_trade_frequency",
        "scheduled_exit": end.isoformat(timespec="seconds"),
        "holding_policy": HOLDING_POLICY if valid_rolling_deadline(item) else "frozen_weekly_deadline",
        "weekend_carry_allowed": valid_rolling_deadline(item) is not None,
        "adaptive_inputs": meta,
        "friday_tactical_low_tp": cls == "friday_tactical",
    }


def postflight() -> Dict[str, Any]:
    """WES postflight with risk distances calibrated to the effective holding deadline."""
    now = legacy.now_local()
    path = v2.current_week_path(now)
    report: Dict[str, Any] = {"version": wes.VERSION, "mode": "postflight", "checked_at": now.isoformat(timespec="seconds"), "actions": []}
    if not path.exists():
        report["status"] = "skipped"
        wes.write(wes.REPORT, report)
        return report
    apply_open_reentry_metadata(path)
    week = wes.read(path, {})
    stats = wes.learning_stats()
    wes.write(wes.LEARNING, stats)
    changed = False
    for item in week.get("instruments") or []:
        if not isinstance(item, dict):
            continue
        if sf(item.get("entry_price")) is not None and sf(item.get("exit_price")) is None and str(item.get("direction")) in {"long", "short"}:
            plan = build_wes_plan(item, week, now, stats)
            if not plan:
                continue
            existing = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
            if existing != plan:
                item["risk_plan"] = plan
                item["wes_status"] = "open_wes_early_reentry_position" if valid_rolling_deadline(item) else "open_wes_governed_position"
                item["wes_methodology"] = wes.VERSION
                item["entry_quality_status"] = f"wes_{plan['wes_entry_class']}"
                report["actions"].append({
                    "instrument_id": item.get("instrument_id"),
                    "action": "freeze_adaptive_risk_plan",
                    "entry_class": plan["wes_entry_class"],
                    "rr": plan["reward_to_risk"],
                    "scheduled_exit": plan["scheduled_exit"],
                    "weekend_carry_allowed": plan["weekend_carry_allowed"],
                })
                changed = True
        elif str(item.get("direction") or "neutral") == "neutral":
            if item.get("wes_status") != "no_trade_monitoring_trigger":
                item["wes_status"] = "no_trade_monitoring_trigger"
                changed = True
    wes_state = week.setdefault("wes", {})
    desired_wes = {
        "version": wes.VERSION,
        "last_postflight_at": now.isoformat(timespec="seconds"),
        "dynamic_risk": True,
        "learning_by_entry_class": True,
        "early_close_reentry": True,
        "replacement_holding_days": HOLDING_DAYS,
        "replacement_weekend_carry": True,
        "material_event_reentry": False,
    }
    wes_state.update(desired_wes)
    changed = True
    if changed:
        wes.write(path, week)
    report["status"] = "completed"
    report["week_id"] = week.get("week_id")
    wes.write(wes.REPORT, report)
    return report


def run_v5(mode: str) -> None:
    """Run canonical v5 with WES rolling-deadline semantics installed."""
    install_v5_hooks()
    if mode == "forecast":
        v2.make_forecast()
        legacy.capture_live_prices()
        return
    if mode == "render":
        legacy.capture_live_prices()
        return

    legacy.capture_live_prices()
    now = legacy.now_local()
    method = v5.read(v5.METHOD, {})
    if mode == "auto":
        if now.weekday() == 6:
            v2.make_forecast()
        elif now.weekday() <= 4 and not v2.current_week_path(now).exists():
            v4.emergency_current_week(now, method)
        review_open_positions_all(now)
        settle_due_positions(now)
        v5.ensure_all()
        apply_open_reentry_metadata()
        return
    if mode == "close":
        review_open_positions_all(now)
        settle_due_positions(now)
        v5.ensure_all()
        apply_open_reentry_metadata()
        return
    if mode == "ensure-exposure":
        v5.ensure_all()
        apply_open_reentry_metadata()
        return
    raise ValueError(f"unsupported mode: {mode}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["auto", "forecast", "close", "ensure-exposure", "render"], default="auto")
    args = parser.parse_args()
    run_v5(args.mode)
    print(json.dumps({"status": "completed", "mode": args.mode, "lifecycle": HOLDING_POLICY}, ensure_ascii=False))


if __name__ == "__main__":
    main()
