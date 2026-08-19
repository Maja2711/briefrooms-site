#!/usr/bin/env python3
"""Synchronize fresh public Portfolio 10K MTM into the BRACE paper ledger.

The durable paper ledger remains the execution/accounting authority for trades.
The public portfolio is the market-data authority.  Before BRACE sizes any
ADD/REDUCE/REPLACE order, this bridge verifies holdings/cash parity and copies
fresh mark-to-market fields plus total portfolio value into the paper ledger.
It fails closed on stale public data or any quantity/cash mismatch.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PATH = ROOT / "data/investments/portfolio_10k.json"
PAPER_PATH = ROOT / "data/portfolio10k/paper_portfolio.json"
MAX_MTM_AGE_MINUTES = 30.0
QUANTITY_TOLERANCE = 1e-8
CASH_TOLERANCE_PLN = 0.02

MARKET_FIELDS = (
    "current_price",
    "current_price_updated_at",
    "current_price_source",
    "current_fx_to_pln",
    "current_fx_updated_at",
    "current_fx_source",
    "current_value_pln",
    "pnl_pln",
    "pnl_percent",
    "pnl_cost_basis_pln",
    "current_weight",
    "market_date",
    "market_status",
    "market_timezone",
    "quote_update_error",
)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        name = tmp.name
    os.replace(name, path)


def finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AssertionError(f"Non-finite accounting value: {value!r}")
    if not math.isfinite(number):
        raise AssertionError(f"Non-finite accounting value: {value!r}")
    return number


def parse_time(value: Any) -> datetime:
    raw = str(value or "").replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def active_map(payload: dict[str, Any], *, paper: bool) -> dict[str, dict[str, Any]]:
    allowed = {"active", "paper_active"} if paper else {"active"}
    return {
        str(row.get("id") or row.get("instrument_id")): row
        for row in payload.get("positions", []) or []
        if str(row.get("status") or "active") in allowed
    }


def sync(public: dict[str, Any], paper: dict[str, Any], now: datetime) -> dict[str, Any]:
    updated_at = parse_time(public.get("last_updated_at"))
    age_minutes = (now.astimezone(timezone.utc) - updated_at).total_seconds() / 60.0
    if age_minutes < -5 or age_minutes > MAX_MTM_AGE_MINUTES:
        raise AssertionError(f"Public MTM is stale: {age_minutes:.2f} minutes")

    public_positions = active_map(public, paper=False)
    paper_positions = active_map(paper, paper=True)
    if set(public_positions) != set(paper_positions):
        raise AssertionError(
            f"Holding IDs differ before execution: public={sorted(public_positions)} paper={sorted(paper_positions)}"
        )

    for instrument_id in sorted(public_positions):
        public_row = public_positions[instrument_id]
        paper_row = paper_positions[instrument_id]
        quantity_gap = abs(finite(public_row.get("quantity")) - finite(paper_row.get("quantity")))
        if quantity_gap > QUANTITY_TOLERANCE:
            raise AssertionError(f"Quantity mismatch {instrument_id}: {quantity_gap}")
        for field in MARKET_FIELDS:
            if field in public_row:
                paper_row[field] = public_row[field]

    public_deployable_cash = finite(public.get("base_cash_pln", public.get("cash_pln")))
    paper_cash = finite(paper.get("cash_pln"))
    if abs(public_deployable_cash - paper_cash) > CASH_TOLERANCE_PLN:
        raise AssertionError(
            f"Deployable cash mismatch: public={public_deployable_cash:.8f} paper={paper_cash:.8f}"
        )

    public_total = finite(public.get("total_value_pln"))
    paper["total_value_pln"] = round(public_total, 8)
    paper["last_market_session"] = public.get("last_market_session")
    paper["execution_mtm_sync"] = {
        "source": "data/investments/portfolio_10k.json",
        "source_last_updated_at": public.get("last_updated_at"),
        "synced_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "age_minutes": round(age_minutes, 4),
        "active_positions": len(public_positions),
        "public_total_value_pln": round(public_total, 8),
        "public_deployable_cash_pln": round(public_deployable_cash, 8),
        "quantity_parity": True,
        "cash_parity": True,
    }
    return paper["execution_mtm_sync"]


def main() -> None:
    public = load(PUBLIC_PATH)
    paper = load(PAPER_PATH)
    receipt = sync(public, paper, datetime.now(timezone.utc))
    write_atomic(PAPER_PATH, paper)
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
