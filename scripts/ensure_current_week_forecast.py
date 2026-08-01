#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create the current paper-trading week if the Sunday forecast run was missed.

A future-week placeholder may exist so the public page can expose the next tab.
On the first trading day that placeholder is replaced with actual model output.
A real existing forecast is never rewritten.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List

import investments_weekly as legacy
import investments_weekly_v2 as v2

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "data" / "investments" / "methodology.json"
WEEKLY = ROOT / "data" / "investments" / "weekly"


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def is_public_placeholder(data: Any) -> bool:
    return isinstance(data, dict) and data.get("forecast_status") == "scheduled" and data.get("model_status") == "paper_only_waiting_for_weekly_generation"


def main() -> None:
    now = legacy.now_local()
    if now.weekday() > 4:
        print("Current-week forecast recovery skipped: weekend")
        return
    week_id = legacy.week_id_from_date(now)
    path = WEEKLY / f"{week_id}.json"
    existing = read(path, {}) if path.exists() else {}
    replacing_placeholder = is_public_placeholder(existing)
    if path.exists() and not replacing_placeholder:
        print(f"Current-week forecast already exists: {path.name}")
        return

    method = read(METHOD, {})
    monday = legacy.monday_for_week(now)
    friday = (monday + timedelta(days=4)).replace(hour=22, minute=0, second=0, microsecond=0)
    items: List[Dict[str, Any]] = []
    for inst in method.get("instruments") or []:
        signal = v2.model_signal(inst, method, week_id, now)
        direction = str(signal.get("direction") or "neutral")
        long_threshold, short_threshold = v2.entry_thresholds(method, str(inst.get("id") or ""))
        items.append(
            {
                "instrument_id": inst.get("id"),
                "symbol": inst.get("symbol"),
                "label_pl": inst.get("label_pl"),
                "label_en": inst.get("label_en"),
                **signal,
                "entry_thresholds": {"long": long_threshold, "short": short_threshold},
                "entry_price": None,
                "entry_captured_at": None,
                "entry_source": None,
                "entry_quality_status": "late_forecast_recovery_waiting_for_continuous_entry",
                "trade_status": "planned" if direction in {"long", "short"} else "no_trade",
                "risk_plan": None,
                "exit_price": None,
                "exit_captured_at": None,
                "exit_source": None,
                "exit_reason": None,
                "result": "no_trade" if direction == "neutral" else None,
                "result_value": 0.0 if direction == "neutral" else None,
                "result_percent": 0.0 if direction == "neutral" else None,
                "rationale_pl": [
                    "Prognoza awaryjna utworzona w bieżącym tygodniu, ponieważ brakowało pełnego przebiegu niedzielnego.",
                    "Warstwa ciągłej ekspozycji pozostaje wyłącznie eksperymentem paper-trading.",
                ],
                "rationale_en": [
                    "Recovery forecast created during the current week because the full Sunday run was missing.",
                    "The continuous-exposure layer remains an experimental paper-trading exercise only.",
                ],
            }
        )

    data: Dict[str, Any] = {
        "week_id": week_id,
        "method_version": v2.MODEL_VERSION,
        "model_status": "paper_trading_late_forecast_recovery",
        "forecast_created_at": now.isoformat(timespec="seconds"),
        "forecast_locked_at": now.isoformat(timespec="seconds"),
        "forecast_for_week_start": monday.date().isoformat(),
        "forecast_for_week_end": friday.date().isoformat(),
        "timezone": "Europe/Warsaw",
        "late_forecast_recovery": True,
        "replaced_public_placeholder": replacing_placeholder,
        "forecast_integrity_note": "A public placeholder contains no signal or price. It was replaced only after actual model data became available.",
        "market_window": {
            "entry_target_local": monday.isoformat(timespec="seconds"),
            "entry_latest_local": (monday + timedelta(hours=2)).isoformat(timespec="seconds"),
            "exit_target_local": friday.isoformat(timespec="seconds"),
        },
        "execution_assumptions": {
            "entry": "continuous exposure layer uses the first completed 5-minute bar available after recovery",
            "scheduled_exit": "first available 5-minute bar at or after Friday 22:00 Europe/Warsaw",
            "same_bar_sl_tp": "stop_loss_first_conservative",
        },
        "instruments": items,
    }
    data["forecast_hash"] = v2.forecast_hash(data)
    write(path, data)
    verb = "Replaced public placeholder with" if replacing_placeholder else "Recovered missing"
    print(f"{verb} current-week paper forecast: {path.name}")


if __name__ == "__main__":
    main()
