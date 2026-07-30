#!/usr/bin/env python3
"""Apply governed corrections for verified W31 stop-loss breaches.

This is intentionally narrow and idempotent. It corrects the published paper
ledger for EUR/USD and S&P 500 futures after their frozen stop levels were
breached but the watcher failed to persist the exits. Exact first-hit intraday
bar timestamps were not reconstructed, so the audit metadata states that
explicitly. Execution remains at the frozen SL level.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WEEK = ROOT / "data" / "investments" / "weekly" / "2026-W31.json"
RECORDED_AT = "2026-07-30T10:09:00+02:00"
OBSERVED_HOUR = "2026-07-30T09:00:00+02:00"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def close_at_stop(item: dict[str, Any], week: dict[str, Any], evidence: str) -> bool:
    plan = item.get("risk_plan") if isinstance(item.get("risk_plan"), dict) else {}
    sl = plan.get("stop_loss_price")
    entry = item.get("entry_price")
    side = str(item.get("direction") or "")
    if sl is None or entry is None or side not in {"long", "short"}:
        raise RuntimeError(f"Missing governed risk data for {item.get('instrument_id')}")

    sl = float(sl)
    entry = float(entry)
    iid = str(item.get("instrument_id") or "")
    move = (sl - entry) if side == "long" else (entry - sl)
    if iid == "eurusd":
        value = move * float(item.get("notional_eur") or 10000.0)
        units = move / 0.0001
    else:
        value = move / entry * float(item.get("notional_usd") or 10000.0)
        units = move

    desired = {
        "exit_price": sl,
        "exit_captured_at": OBSERVED_HOUR,
        "exit_source": "governed_manual_correction:verified_stop_breach",
        "exit_reason": "stop_loss",
        "exit_execution_model": "frozen_planned_level_manual_governance_correction",
        "risk_status": "stop_loss_hit",
        "trade_status": "closed",
        "continuous_exposure_active": False,
        "continuous_exposure_status": "closed_by_risk_exit",
        "next_entry_status": "blocked_after_risk_exit",
        "result": "loss" if value < 0 else "profit" if value > 0 else "flat",
        "result_value": round(value, 8),
        "result_percent": round(move / entry * 100.0, 4),
        "result_units": round(units, 8),
        "result_currency": "USD",
        "risk_exit_correction": {
            "recorded_at": RECORDED_AT,
            "reason": "published position remained open after frozen stop level was breached",
            "evidence": evidence,
            "exact_first_hit_bar": "not_reconstructed_in_manual_correction",
            "execution_price_policy": "frozen_stop_level",
        },
        "reentry_lock": {
            "active": True,
            "scope": "same_week",
            "until": str((week.get("market_window") or {}).get("exit_target_local") or "2026-07-31T22:00:00+02:00"),
            "reason": "stop_loss",
            "policy": "sl_tp_exit_blocks_same_week_reentry",
            "created_from_exit_at": OBSERVED_HOUR,
        },
    }

    changed = any(item.get(k) != v for k, v in desired.items())
    item.update(desired)
    return changed


def main() -> None:
    week = read_json(WEEK)
    changed = False
    found: set[str] = set()
    for item in week.get("instruments") or []:
        iid = str(item.get("instrument_id") or "")
        if iid == "eurusd":
            changed |= close_at_stop(
                item,
                week,
                "Reported EUR/USD hourly high 1.1475 exceeded frozen SL 1.14596006.",
            )
            found.add(iid)
        elif iid == "sp500_futures":
            changed |= close_at_stop(
                item,
                week,
                "Reported S&P 500 futures market move breached frozen SL 7411.23786673.",
            )
            found.add(iid)

    missing = {"eurusd", "sp500_futures"} - found
    if missing:
        raise RuntimeError(f"Missing W31 instruments: {sorted(missing)}")

    if changed:
        write_json(WEEK, week)
    print(json.dumps({"status": "completed", "changed": changed, "corrected": sorted(found)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
