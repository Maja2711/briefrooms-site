#!/usr/bin/env python3
"""Repair/preserve the independent USD cash-yield correction audit receipt.

The USD builder deep-copies the PLN source before overlaying the independent USD
cash ledger, so source-level PLN correction metadata can leak into the USD file.
This script reconstructs the USD receipt from the same durable trade ledger and
replaces only metadata. It never changes the already-booked USD cash balance.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

try:
    from portfolio_10k_cash_yield import load_policy, parse_timestamp
    from portfolio_10k_cash_yield_backfill import CORRECTION_ID, correction_from_ledger
except ModuleNotFoundError:
    from scripts.portfolio_10k_cash_yield import load_policy, parse_timestamp
    from scripts.portfolio_10k_cash_yield_backfill import CORRECTION_ID, correction_from_ledger

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PATH = ROOT / "data/investments/portfolio_10k.json"
PAPER_PATH = ROOT / "data/portfolio10k/paper_portfolio.json"
USD_PATH = ROOT / "data/investments/portfolio_10k_usd.json"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        name = tmp.name
    os.replace(name, path)


def main() -> None:
    public = load(PUBLIC_PATH)
    paper = load(PAPER_PATH)
    usd = load(USD_PATH)
    pln_receipt = next(
        (
            row for row in public.get("cash_yield_corrections", []) or []
            if row.get("correction_id") == CORRECTION_ID and row.get("currency") == "PLN"
        ),
        None,
    )
    if pln_receipt is None:
        print(json.dumps({"status": "SKIP", "reason": "PLN correction not applied"}))
        return

    activation = parse_timestamp(pln_receipt.get("period_end"))
    if activation is None:
        raise AssertionError("Invalid correction period_end")
    policy = load_policy()
    usd_rate = float((policy.get("usd") or {}).get("annual_rate") or 0.0)
    amount, events, initial_cash = correction_from_ledger(
        public, paper, activation=activation, annual_rate=usd_rate
    )
    if float(usd.get("cash_interest_accrued_usd") or 0.0) + 1e-9 < amount:
        raise AssertionError("USD cash ledger does not contain the historical correction amount")

    receipt = {
        "correction_id": CORRECTION_ID,
        "basis": "durable paper_portfolio.transactions before cash-yield engine activation",
        "period_end": activation.isoformat(timespec="seconds"),
        "initial_cash": round(initial_cash, 8),
        "events": events,
        "retroactive_estimation": False,
        "idempotent": True,
        "currency": "USD",
        "annual_rate": usd_rate,
        "amount": round(amount, 8),
        "economic_booking_preserved": True,
        "metadata_repaired_after_usd_rebuild": True,
    }
    others = [
        row for row in usd.get("cash_yield_corrections", []) or []
        if row.get("correction_id") != CORRECTION_ID
    ]
    usd["cash_yield_corrections"] = [*others, receipt]
    yield_meta = dict(usd.get("cash_yield") or {})
    yield_meta["historical_correction_applied"] = CORRECTION_ID
    yield_meta["historical_correction_amount"] = round(amount, 8)
    usd["cash_yield"] = yield_meta
    write_atomic(USD_PATH, usd)
    print(json.dumps({"status": "REPAIRED", "currency": "USD", "amount": round(amount, 8)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
