#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply per-instrument validation policy to model v2 forecasts.

An instrument that failed the fixed-rule historical test is forced to neutral
before the entry window. Existing legacy weeks are never rewritten by this gate.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / "data" / "investments" / "methodology.json"
WEEKLY = ROOT / "data" / "investments" / "weekly"
TZ = ZoneInfo("Europe/Warsaw")


def read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    method = read(METHOD, {})
    policy = {str(x.get("id")): x for x in method.get("instruments", [])}
    changed_files = 0
    for path in sorted(WEEKLY.glob("*.json"), reverse=True)[:4]:
        week = read(path, {})
        if not str(week.get("method_version") or "").startswith("2."):
            continue
        changed = False
        for item in week.get("instruments", []):
            inst_id = str(item.get("instrument_id") or "")
            cfg = policy.get(inst_id, {})
            enabled = bool(cfg.get("enabled_for_new_positions", False))
            if enabled:
                item["validation_gate"] = "enabled_for_paper_trading"
                continue
            if item.get("entry_price") is not None:
                raise SystemExit(f"Refusing to disable {inst_id} after entry was already recorded")
            reason = str(cfg.get("validation_gate_reason") or "instrument_not_validated")
            item["pre_gate_direction"] = item.get("direction")
            item["pre_gate_score"] = item.get("score")
            item["direction"] = "neutral"
            item["trade_status"] = "no_trade"
            item["entry_quality_status"] = "blocked_by_validation_gate"
            item["risk_plan"] = None
            item["result"] = "no_trade"
            item["result_value"] = 0.0
            item["result_percent"] = 0.0
            item["validation_gate"] = reason
            item["rationale_pl"] = [
                "Brak pozycji: instrument nie przeszedł zapisanych kryteriów walidacji po kosztach.",
                "Nie dostrajamy parametrów po wyniku testu; instrument pozostaje wyłączony do nowej, niezależnej wersji modelu."
            ]
            item["rationale_en"] = [
                "No position: the instrument did not pass the saved after-cost validation criteria.",
                "Parameters are not retuned after seeing the test result; the instrument stays disabled until a new independently specified model version."
            ]
            changed = True
        if changed:
            week["instrument_validation_gate_applied_at"] = datetime.now(TZ).isoformat(timespec="seconds")
            write(path, week)
            changed_files += 1
    print(f"Validation gate applied to {changed_files} forecast files")


if __name__ == "__main__":
    main()
