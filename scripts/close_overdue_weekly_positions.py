#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Close overdue weekly investment positions before rendering public pages.

A weekly position must not remain "w trakcie" after its planned weekly close.
This script scans all weekly files and closes every active item whose
forecast/market end time has passed, even if exit_price contains an old text
placeholder such as "week_in_progress".
"""
from __future__ import annotations

import json
import math
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
LIVE_PRICE_PATH = ROOT / "data" / "investments" / "live_prices.json"
TZ = ZoneInfo("Europe/Warsaw")


def now_local() -> datetime:
    return datetime.now(TZ)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def f(value):
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except Exception:
        return None


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        return None


def close_deadline(week: dict) -> datetime | None:
    window = week.get("market_window") if isinstance(week.get("market_window"), dict) else {}
    target = parse_dt(window.get("exit_target_local"))
    if target:
        return target
    end = str(week.get("forecast_for_week_end") or "")
    try:
        d = datetime.fromisoformat(end).date()
        return datetime.combine(d, time(22, 0), TZ)
    except Exception:
        return None


def result_for(item: dict, exit_price: float) -> tuple[str, float, float | None, float]:
    entry = f(item.get("entry_price"))
    if entry is None or entry == 0:
        return "no_trade", 0.0, 0.0, 0.0
    direction = str(item.get("direction") or item.get("effective_direction") or "neutral")
    if direction not in {"long", "short"}:
        return "no_trade", 0.0, 0.0, 0.0
    raw_move = exit_price - entry
    signed_move = raw_move if direction == "long" else -raw_move
    pct = signed_move / entry * 100.0
    if item.get("instrument_id") == "eurusd":
        notional = f(item.get("notional_eur")) or 10000.0
        usd_value = signed_move * notional
        units = signed_move / 0.0001
    else:
        notional = f(item.get("notional_usd")) or 10000.0
        usd_value = pct / 100.0 * notional
        units = signed_move
    label = "flat" if abs(usd_value) < 0.05 else "profit" if usd_value > 0 else "loss"
    return label, round(usd_value, 2), round(pct, 4), round(units, 4)


def close_price(item: dict, live_prices: dict) -> tuple[float | None, str, str | None]:
    inst_id = str(item.get("instrument_id") or "")
    rec = (live_prices.get("prices", {}) or {}).get(inst_id, {}) if isinstance(live_prices, dict) else {}
    if isinstance(rec, dict):
        price = f(rec.get("price"))
        if price is not None:
            ts = str(rec.get("current_price_updated_at") or rec.get("timestamp") or live_prices.get("updated_at") or now_local().isoformat(timespec="seconds"))
            src = str(rec.get("source") or "live_prices.json")
            return price, src, ts
    observed = f(item.get("exit_observed_price"))
    if observed is not None:
        return observed, "saved exit_observed_price", str(item.get("exit_captured_at") or now_local().isoformat(timespec="seconds"))
    return None, "no close price available", None


def close_item(item: dict, week: dict, live_prices: dict) -> bool:
    if f(item.get("exit_price")) is not None:
        return False
    direction = str(item.get("direction") or item.get("effective_direction") or "neutral")
    if direction not in {"long", "short"}:
        return False
    price, source, timestamp = close_price(item, live_prices)
    if price is None:
        item["close_quality_status"] = "close_due_but_no_price"
        item["exit_reason"] = item.get("exit_reason") or "missing_close_price"
        return True
    result, usd_value, pct, units = result_for(item, price)
    item["exit_price"] = price
    item["exit_captured_at"] = timestamp or now_local().isoformat(timespec="seconds")
    item["exit_source"] = source
    item["exit_reason"] = item.get("exit_reason") or "scheduled_week_close"
    item["exit_execution_model"] = item.get("exit_execution_model") or "automatic_week_close_after_deadline"
    item["result"] = result
    item["result_value"] = usd_value
    item["result_percent"] = pct
    item["result_currency"] = "USD"
    item["result_units"] = units
    item["effective_direction"] = direction
    item["close_quality_status"] = "closed_after_week_deadline"
    return True


def process() -> bool:
    live = load_json(LIVE_PRICE_PATH, {"prices": {}})
    changed_any = False
    now = now_local()
    for path in sorted(WEEKLY_DIR.glob("*.json")):
        week = load_json(path, {})
        if not isinstance(week, dict):
            continue
        deadline = close_deadline(week)
        if deadline is None or now < deadline:
            continue
        changed = False
        for item in week.get("instruments", []) or []:
            if isinstance(item, dict):
                changed = close_item(item, week, live) or changed
        if changed:
            week["weekly_close_audit"] = {
                "status": "applied",
                "checked_at": now.isoformat(timespec="seconds"),
                "rule": "Positions with non-numeric exit_price are closed after market_window.exit_target_local / forecast_for_week_end.",
            }
            write_json(path, week)
            changed_any = True
            print(f"closed overdue weekly positions in {path}")
    return changed_any


if __name__ == "__main__":
    print("Overdue weekly closes applied" if process() else "No overdue weekly closes needed")
