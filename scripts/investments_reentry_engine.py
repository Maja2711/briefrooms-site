#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import investments_weekly as base

ROOT = base.REPO
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
LIVE = ROOT / "data" / "investments" / "live_prices.json"
FACTORS = ROOT / "data" / "investments" / "learning" / "market_factor_research.json"
MACRO = ROOT / "data" / "investments" / "macro_events.json"
REPORT = ROOT / "data" / "investments" / "reentry_report.json"
MODEL = "reentry-engine-1.0"
MAX_REENTRIES_PER_INSTRUMENT_PER_WEEK = 1
MIN_TRIGGER_SCORE = 22.0


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sf(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def latest_week_path() -> Optional[Path]:
    files = sorted(WEEKLY_DIR.glob("*.json"), reverse=True)
    return files[0] if files else None


def instrument_id(item: Dict[str, Any]) -> str:
    return str(item.get("instrument_id") or "")


def live_price(inst: str, live: Dict[str, Any]) -> Optional[float]:
    prices = live.get("prices") if isinstance(live, dict) else {}
    rec = (prices or {}).get(inst, {}) if isinstance(prices, dict) else {}
    return sf(rec.get("price") or rec.get("regularMarketPrice") or rec.get("current_price"))


def live_source(inst: str, live: Dict[str, Any]) -> str:
    prices = live.get("prices") if isinstance(live, dict) else {}
    rec = (prices or {}).get(inst, {}) if isinstance(prices, dict) else {}
    return str(rec.get("source") or "Yahoo Finance")


def has_open_position(items: List[Dict[str, Any]], inst: str) -> bool:
    for item in items:
        if instrument_id(item) != inst:
            continue
        if sf(item.get("entry_price")) is not None and sf(item.get("exit_price")) is None:
            return True
    return False


def last_stopped_position(items: List[Dict[str, Any]], inst: str) -> Optional[Dict[str, Any]]:
    matches = [x for x in items if instrument_id(x) == inst and str(x.get("exit_reason") or "") == "stop_loss"]
    return matches[-1] if matches else None


def reentry_count(items: List[Dict[str, Any]], inst: str) -> int:
    return sum(1 for x in items if instrument_id(x) == inst and x.get("position_type") == "reentry")


def brace_signal(inst: str, factors: Dict[str, Any]) -> Tuple[str, float, str]:
    row = ((factors.get("results") or {}).get(inst) or {}) if isinstance(factors, dict) else {}
    score = sf(row.get("brace_score")) or 0.0
    if score > MIN_TRIGGER_SCORE:
        return "long", score, "BRACE positive re-entry trigger"
    if score < -MIN_TRIGGER_SCORE:
        return "short", score, "BRACE negative re-entry trigger"
    return "none", score, "BRACE trigger too weak"


def macro_event_alert(macro: Dict[str, Any]) -> bool:
    if not isinstance(macro, dict):
        return False
    text = " ".join(str(e.get("title", "")) + " " + str(e.get("summary", "")) for e in (macro.get("events") or [])[:12]).lower()
    keys = ("cpi", "inflation", "nfp", "payroll", "fomc", "powell", "ecb", "fed", "lagarde")
    return sum(1 for k in keys if k in text) >= 3


def allowed_reentry(inst: str, factors: Dict[str, Any], macro: Dict[str, Any]) -> Tuple[bool, str, float, str]:
    side, score, note = brace_signal(inst, factors)
    if side == "none":
        return False, side, score, note
    # EUR/USD is very macro-sensitive. Do not reopen during a hot central-bank/data window.
    if inst == "eurusd" and macro_event_alert(macro):
        return False, side, score, "blocked by macro-event alert"
    return True, side, score, note


def make_reentry_item(base_item: Dict[str, Any], inst: str, side: str, entry: float, source: str, week: Dict[str, Any], count: int, trigger_score: float, trigger_note: str) -> Dict[str, Any]:
    item = deepcopy(base_item)
    now = base.now_local().isoformat(timespec="seconds")
    item["position_id"] = f"{inst}-reentry-{count + 1}"
    item["position_type"] = "reentry"
    item["reentry_after"] = str(base_item.get("position_id") or f"{week.get('week_id')}:{inst}")
    item["reentry_model_version"] = MODEL
    item["reentry_trigger_score"] = round(trigger_score, 2)
    item["reentry_trigger_note"] = trigger_note
    item["direction"] = side
    item["effective_direction"] = side
    item["score"] = round(trigger_score, 2) if side == "long" else round(-abs(trigger_score), 2)
    item["confidence"] = min(abs(trigger_score) / 100.0, 1.0)
    item["entry_price"] = entry
    item["entry_captured_at"] = now
    item["entry_source"] = source
    item["exit_price"] = None
    item["exit_observed_price"] = None
    item["exit_captured_at"] = None
    item["exit_reason"] = None
    item["exit_source"] = None
    item["result"] = None
    item["result_value"] = None
    item["result_percent"] = None
    item["result_units"] = None
    item["risk_status"] = "open_reentry"
    item.pop("risk_review", None)
    item.pop("scenario_grade_v2", None)
    item["rationale_pl"] = [
        "Re-entry po stop loss: poprzedni scenariusz został zamknięty, ale świeży filtr jakości dał nowy sygnał wejścia.",
        f"Kierunek re-entry: {side}.",
        f"Sygnał: {trigger_note}; score {trigger_score:.2f}."
    ]
    item["rationale_en"] = [
        "Re-entry after stop loss: the previous scenario was closed, but the fresh quality filter produced a new entry signal.",
        f"Re-entry direction: {side}.",
        f"Signal: {trigger_note}; score {trigger_score:.2f}."
    ]
    item["risk_plan"] = None
    return item


def run() -> None:
    path = latest_week_path()
    report: Dict[str, Any] = {"model_version": MODEL, "updated_at": base.now_local().isoformat(timespec="seconds"), "opened": [], "skipped": []}
    if path is None:
        write(REPORT, report)
        return
    week = read(path, {})
    items = week.get("instruments", []) if isinstance(week, dict) else []
    live = read(LIVE, {})
    factors = read(FACTORS, {})
    macro = read(MACRO, {})
    changed = False
    insts = []
    for item in items:
        inst = instrument_id(item)
        if inst and inst not in insts:
            insts.append(inst)
    for inst in insts:
        if has_open_position(items, inst):
            report["skipped"].append({"instrument_id": inst, "reason": "open_position_already_exists"})
            continue
        stopped = last_stopped_position(items, inst)
        if stopped is None:
            report["skipped"].append({"instrument_id": inst, "reason": "no_stop_loss_to_reenter_after"})
            continue
        count = reentry_count(items, inst)
        if count >= MAX_REENTRIES_PER_INSTRUMENT_PER_WEEK:
            report["skipped"].append({"instrument_id": inst, "reason": "weekly_reentry_limit_reached"})
            continue
        ok, side, score, note = allowed_reentry(inst, factors, macro)
        if not ok:
            report["skipped"].append({"instrument_id": inst, "reason": note, "score": round(score, 2), "side": side})
            continue
        entry = live_price(inst, live)
        if entry is None:
            report["skipped"].append({"instrument_id": inst, "reason": "no_live_price_for_reentry"})
            continue
        new_item = make_reentry_item(stopped, inst, side, entry, live_source(inst, live), week, count, score, note)
        items.append(new_item)
        report["opened"].append({"instrument_id": inst, "position_id": new_item.get("position_id"), "direction": side, "entry_price": entry, "trigger_score": round(score, 2), "trigger_note": note})
        changed = True
    if changed:
        week["instruments"] = items
        write(path, week)
    write(REPORT, report)
    print(f"Re-entry opened: {len(report['opened'])}; skipped: {len(report['skipped'])}")


if __name__ == "__main__":
    run()
