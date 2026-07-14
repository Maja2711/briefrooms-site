#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Review open weekly positions and close invalidated theses.

Normal thesis review runs once per trading day after 23:00 Europe/Warsaw.
A separately recorded material-event exit request may be processed immediately,
using the next available completed 5-minute bar. Event exits are close-only:
no automatic reversal and no same-week re-entry.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import investments_weekly as legacy
import investments_weekly_v2 as model

ROOT = Path(__file__).resolve().parents[1]
METHOD_PATH = ROOT / "data" / "investments" / "methodology.json"
REPORT_PATH = ROOT / "data" / "investments" / "daily_review_report.json"
EVENT_REQUESTS_PATH = ROOT / "data" / "investments" / "event_exit_requests.json"
REVIEW_HOUR_LOCAL = 23
INVALIDATION_SCORE = 15
OPPOSING_GROUPS_REQUIRED = 2


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def sf(value: Any) -> Optional[float]:
    return model.sf(value)


def last_completed_5m_bar(symbol: str, now: datetime) -> Optional[Dict[str, Any]]:
    cutoff = now - timedelta(minutes=5)
    start = cutoff - timedelta(days=2)
    df = model.intraday_bars(symbol, start, cutoff)
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
        ts = df.index[-1].to_pydatetime().astimezone(model.TZ)
        return {
            "price": price,
            "timestamp": ts.isoformat(timespec="seconds"),
            "source": f"Yahoo Finance:{symbol}:5m:last_completed_bar_position_review",
        }
    except Exception:
        return None


def agreement(fresh: Dict[str, Any]) -> Tuple[int, int]:
    signals = fresh.get("signals") if isinstance(fresh.get("signals"), dict) else {}
    row = signals.get("agreement") if isinstance(signals.get("agreement"), dict) else {}
    return int(row.get("positive_groups") or 0), int(row.get("negative_groups") or 0)


def exit_decision(side: str, fresh: Dict[str, Any]) -> Tuple[bool, str]:
    if fresh.get("data_quality") != "passed":
        return False, "keep_data_quality_failed"
    score = int(fresh.get("score") or 0)
    direction = str(fresh.get("direction") or "neutral")
    positive, negative = agreement(fresh)

    if side == "long":
        if direction == "short":
            return True, "confirmed_opposite_signal"
        if score <= -INVALIDATION_SCORE and negative >= OPPOSING_GROUPS_REQUIRED:
            return True, "directional_invalidation"
    elif side == "short":
        if direction == "long":
            return True, "confirmed_opposite_signal"
        if score >= INVALIDATION_SCORE and positive >= OPPOSING_GROUPS_REQUIRED:
            return True, "directional_invalidation"
    return False, "keep_original_thesis_not_invalidated"


def pending_event_request(data: Dict[str, Any], week_id: str, instrument_id: str) -> Optional[Dict[str, Any]]:
    for row in data.get("requests") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("status")) != "pending" or str(row.get("action")) != "close_only":
            continue
        if str(row.get("week_id")) == week_id and str(row.get("instrument_id")) == instrument_id:
            return row
    return None


def has_pending_event(data: Dict[str, Any], week: Dict[str, Any]) -> bool:
    week_id = str(week.get("week_id") or "")
    for item in week.get("instruments") or []:
        if pending_event_request(data, week_id, str(item.get("instrument_id") or "")):
            return True
    return False


def safe_fresh_signal(cfg: Dict[str, Any], method: Dict[str, Any], week_id: str, now: datetime) -> Dict[str, Any]:
    try:
        return model.model_signal(cfg, method, week_id, now)
    except Exception as exc:
        return {
            "direction": "unavailable",
            "score": 0,
            "signal_strength": 0.0,
            "signals": {},
            "data_quality": f"failed:{type(exc).__name__}",
        }


def review() -> Dict[str, Any]:
    now = legacy.now_local()
    report: Dict[str, Any] = {
        "model_version": model.MODEL_VERSION,
        "reviewed_at": now.isoformat(timespec="seconds"),
        "review_date": now.date().isoformat(),
        "review_hour_local": REVIEW_HOUR_LOCAL,
        "closed": [],
        "kept": [],
        "skipped": [],
    }

    if now.weekday() > 4:
        report["status"] = "skipped_weekend"
        write(REPORT_PATH, report)
        return report

    path = model.current_week_path(now)
    week = read(path, {})
    if not week:
        report["status"] = "skipped_no_current_week"
        write(REPORT_PATH, report)
        return report

    event_data = read(EVENT_REQUESTS_PATH, {"requests": []})
    event_pending = has_pending_event(event_data, week)
    if now.hour < REVIEW_HOUR_LOCAL and not event_pending:
        report["status"] = "skipped_before_daily_review_time"
        write(REPORT_PATH, report)
        return report

    state = week.get("daily_position_review") if isinstance(week.get("daily_position_review"), dict) else {}
    if now.hour >= REVIEW_HOUR_LOCAL and state.get("last_review_date") == now.date().isoformat() and not event_pending:
        report["status"] = "skipped_already_reviewed_today"
        write(REPORT_PATH, report)
        return report

    method = read(METHOD_PATH, {})
    cfg_by_id = {str(x.get("id")): x for x in method.get("instruments", [])}
    week_id = str(week.get("week_id") or "")
    changed = False
    event_changed = False

    for item in week.get("instruments", []):
        inst_id = str(item.get("instrument_id") or "")
        side = str(item.get("direction") or "neutral")
        entry = sf(item.get("entry_price"))
        exit_price = sf(item.get("exit_price"))
        request = pending_event_request(event_data, week_id, inst_id)

        if side not in {"long", "short"} or entry is None or exit_price is not None:
            report["skipped"].append({"instrument_id": inst_id, "reason": "no_open_directional_position"})
            if request:
                request["status"] = "no_action_position_not_open"
                request["processed_at"] = now.isoformat(timespec="seconds")
                event_changed = True
            continue

        cfg = cfg_by_id.get(inst_id)
        if not cfg:
            report["skipped"].append({"instrument_id": inst_id, "reason": "instrument_config_missing"})
            continue

        fresh = safe_fresh_signal(cfg, method, week_id, now)
        if request:
            should_close = True
            reason = str(request.get("reason") or "material_event_exit_request")
            trigger = "material_event_request"
        else:
            should_close, reason = exit_decision(side, fresh)
            trigger = "scheduled_daily_model_review"

        positive, negative = agreement(fresh)
        review_row: Dict[str, Any] = {
            "review_date": now.date().isoformat(),
            "reviewed_at": now.isoformat(timespec="seconds"),
            "review_trigger": trigger,
            "original_direction": side,
            "fresh_direction": fresh.get("direction"),
            "fresh_score": fresh.get("score"),
            "fresh_signal_strength": fresh.get("signal_strength"),
            "positive_groups": positive,
            "negative_groups": negative,
            "data_quality": fresh.get("data_quality"),
            "decision": "close" if should_close else "keep",
            "reason": reason,
        }
        if request:
            review_row["event_request_id"] = request.get("request_id")
            review_row["event"] = request.get("event")
            review_row["event_policy"] = request.get("policy")

        if should_close:
            point = last_completed_5m_bar(str(item.get("symbol") or cfg.get("symbol") or ""), now)
            if point is None:
                review_row["decision"] = "defer_close"
                review_row["reason"] = f"{reason}_but_exit_bar_unavailable"
                report["kept"].append({"instrument_id": inst_id, **review_row})
            else:
                item["exit_price"] = point["price"]
                item["exit_captured_at"] = point["timestamp"]
                item["exit_source"] = point["source"]
                item["exit_reason"] = f"event_review_{reason}" if request else f"daily_model_{reason}"
                item["exit_execution_model"] = "last_completed_5m_bar_at_review"
                item["trade_status"] = "closed"
                item["risk_status"] = "closed_by_material_event_review" if request else "closed_by_daily_model_review"
                model.set_result(item, float(point["price"]))
                review_row["exit_price"] = point["price"]
                review_row["exit_captured_at"] = point["timestamp"]
                review_row["exit_source"] = point["source"]
                report["closed"].append({"instrument_id": inst_id, **review_row})
                changed = True
                if request:
                    request["status"] = "executed"
                    request["executed_at"] = now.isoformat(timespec="seconds")
                    request["exit_price"] = point["price"]
                    request["exit_captured_at"] = point["timestamp"]
                    request["exit_source"] = point["source"]
                    event_changed = True
        else:
            report["kept"].append({"instrument_id": inst_id, **review_row})

        reviews = item.get("daily_reviews") if isinstance(item.get("daily_reviews"), list) else []
        reviews.append(review_row)
        item["daily_reviews"] = reviews[-10:]
        item["last_daily_review_at"] = now.isoformat(timespec="seconds")
        item["last_daily_review_decision"] = review_row["decision"]
        item["last_daily_review_reason"] = review_row["reason"]
        changed = True

    if now.hour >= REVIEW_HOUR_LOCAL:
        week["daily_position_review"] = {
            "enabled": True,
            "last_review_date": now.date().isoformat(),
            "last_reviewed_at": now.isoformat(timespec="seconds"),
            "local_time": f"{REVIEW_HOUR_LOCAL:02d}:00 Europe/Warsaw",
            "exit_rules": {
                "confirmed_opposite_signal": True,
                "directional_invalidation_score": INVALIDATION_SCORE,
                "opposing_groups_required": OPPOSING_GROUPS_REQUIRED,
                "material_event_close_only": True,
                "data_quality_failure_action": "keep_position_unless_material_event_request",
                "execution": "last_completed_5m_bar",
                "same_week_reentry": False,
            },
        }
    else:
        state["enabled"] = True
        state["last_material_event_review_at"] = now.isoformat(timespec="seconds")
        state["material_event_close_only"] = True
        week["daily_position_review"] = state

    if changed:
        write(path, week)
    if event_changed:
        event_data["updated_at"] = now.isoformat(timespec="seconds")
        write(EVENT_REQUESTS_PATH, event_data)

    report["status"] = "completed_material_event_review" if event_pending and now.hour < REVIEW_HOUR_LOCAL else "completed"
    report["week_id"] = week.get("week_id")
    write(REPORT_PATH, report)
    return report


if __name__ == "__main__":
    print(json.dumps(review(), ensure_ascii=False))
