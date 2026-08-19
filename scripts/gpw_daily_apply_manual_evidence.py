#!/usr/bin/env python3
"""Apply explicit user-supplied broker evidence to a pending GPW paper outcome.

Evidence files are immutable receipts, not price forecasts.  A TARGET receipt is
accepted only when the frozen selection was activated in its entry zone and the
observed executable bid is at/above the frozen target.  The paper exit remains
at the frozen target, never at the later observed price.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from scripts import gpw_daily_pick as gpw
except ModuleNotFoundError:
    import gpw_daily_pick as gpw

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "data/internal/gpw_daily_pick_evidence"
COST_PERCENT = 0.38


def apply_receipt(receipt: dict[str, Any]) -> bool:
    day = str(receipt.get("date") or "")
    history_path = gpw.HISTORY_DIR / f"{day}.json"
    payload = gpw.load_json(history_path)
    if not isinstance(payload, dict) or payload.get("decision") != "TRANSAKCJA":
        return False
    if (payload.get("outcome") or {}).get("status") == "RESOLVED":
        return False
    selection = payload.get("selection") or {}
    if str(selection.get("symbol")) != str(receipt.get("symbol")):
        raise AssertionError("Manual evidence symbol does not match frozen selection")
    snapshot = selection.get("market_snapshot") or {}
    entry = float(snapshot.get("last"))
    entry_low, entry_high = map(float, selection.get("entry_zone") or [])
    if not entry_low <= entry <= entry_high:
        raise AssertionError("Frozen selection snapshot did not activate inside entry zone")
    target = float(selection.get("target"))
    observed_bid = float(receipt.get("observed_bid"))
    if str(receipt.get("event")) != "TARGET_CONFIRMED" or observed_bid < target:
        raise AssertionError("Manual evidence does not prove target was executable")
    stop = float(selection.get("stop"))
    risk = max(entry - stop, 0.01)
    gross = (target / entry - 1.0) * 100.0
    payload["outcome"] = {
        "status": "RESOLVED",
        "activated": True,
        "activated_at": snapshot.get("observed_at"),
        "activation_evidence": "frozen_selection_market_snapshot",
        "entry_price": gpw.round2(entry),
        "exit_price": gpw.round2(target),
        "exit_reason": "target",
        "return_percent": round(gross - COST_PERCENT, 3),
        "gross_return_percent": round(gross, 3),
        "r_multiple": round((target - entry) / risk, 3),
        "cost_assumption_percent": COST_PERCENT,
        "settlement_policy": "frozen_target_confirmed_by_user_broker_evidence",
        "resolved_at": receipt.get("observed_at"),
        "settlement_evidence": {
            "source": receipt.get("source"),
            "observed_at": receipt.get("observed_at"),
            "observed_bid": observed_bid,
            "observed_ask": receipt.get("observed_ask"),
            "frozen_target": target,
            "note": receipt.get("note"),
        },
    }
    gpw.atomic_json(history_path, payload)
    return True


def main() -> None:
    applied = 0
    for path in sorted(EVIDENCE_DIR.glob("*.json")):
        receipt = gpw.load_json(path)
        if isinstance(receipt, dict) and apply_receipt(receipt):
            applied += 1
    print(json.dumps({"manual_evidence_applied": applied}, ensure_ascii=False))


if __name__ == "__main__":
    main()
