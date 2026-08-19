#!/usr/bin/env python3
"""One-time, ledger-derived correction for cash yield before the accrual engine launch.

The cash-yield engine intentionally started without retroactive guessing. We now
have an append-only execution ledger, so the missing interval can be reconstructed
exactly from executed cash flows. This script is idempotent and records a durable
correction receipt. It never estimates a trade that is absent from the ledger.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from portfolio_10k_cash_yield import interval_interest, load_policy, parse_timestamp
except ModuleNotFoundError:  # unittest imports modules as scripts.* from repo root
    from scripts.portfolio_10k_cash_yield import interval_interest, load_policy, parse_timestamp

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PATH = ROOT / "data/investments/portfolio_10k.json"
PAPER_PATH = ROOT / "data/portfolio10k/paper_portfolio.json"
USD_PATH = ROOT / "data/investments/portfolio_10k_usd.json"
CORRECTION_ID = "pre-cash-yield-activation-ledger-v1"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        name = tmp.name
    os.replace(name, path)


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def snapshot_time(row: dict[str, Any]) -> datetime | None:
    for key in ("timestamp_utc", "recorded_at"):
        parsed = parse_timestamp(row.get(key))
        if parsed is not None:
            return parsed
    return None


def cash_flow(transaction: dict[str, Any]) -> float:
    price = finite(transaction.get("price"))
    fx = finite(transaction.get("fx_to_pln"), 1.0)
    quantity = finite(transaction.get("quantity"))
    costs = finite(transaction.get("transaction_cost_pln"))
    gross = price * fx * quantity
    side = str(transaction.get("side") or "").upper()
    if side == "SELL":
        return gross - costs
    if side == "BUY":
        return -(gross + costs)
    raise AssertionError(f"Unsupported ledger side: {side!r}")


def correction_from_ledger(
    public: dict[str, Any], paper: dict[str, Any], *, activation: datetime, annual_rate: float
) -> tuple[float, list[dict[str, Any]], float]:
    transactions = []
    for row in paper.get("transactions", []) or []:
        executed = parse_timestamp(row.get("executed_at"))
        if executed is not None and executed < activation:
            transactions.append((executed, row))
    transactions.sort(key=lambda item: item[0])
    if not transactions:
        return 0.0, [], 0.0

    first_time = transactions[0][0]
    eligible_snapshots = []
    for row in public.get("snapshots", []) or []:
        observed = snapshot_time(row)
        if observed is not None and observed <= first_time and row.get("cash_pln") is not None:
            eligible_snapshots.append((observed, row))
    initial_cash = finite(max(eligible_snapshots, key=lambda item: item[0])[1].get("cash_pln")) if eligible_snapshots else 0.0

    balance = initial_cash
    cursor = first_time
    interest_total = 0.0
    events: list[dict[str, Any]] = []
    for executed, row in transactions:
        earned = interval_interest(balance, annual_rate, cursor, executed)
        balance += earned
        interest_total += earned
        flow = cash_flow(row)
        balance += flow
        if balance < -0.02:
            raise AssertionError(f"Historical cash becomes negative at {executed.isoformat()}: {balance}")
        events.append(
            {
                "executed_at": executed.isoformat(timespec="seconds"),
                "instrument_id": row.get("instrument_id"),
                "side": row.get("side"),
                "cash_flow": round(flow, 8),
                "interest_before_event": round(earned, 8),
                "cash_after_event": round(balance, 8),
            }
        )
        cursor = executed

    earned = interval_interest(balance, annual_rate, cursor, activation)
    balance += earned
    interest_total += earned
    return interest_total, events, initial_cash


def has_correction(payload: dict[str, Any]) -> bool:
    return any(row.get("correction_id") == CORRECTION_ID for row in payload.get("cash_yield_corrections", []) or [])


def add_pln_correction(payload: dict[str, Any], amount: float, receipt: dict[str, Any], *, paper: bool) -> None:
    if has_correction(payload):
        return
    payload["cash_pln"] = round(finite(payload.get("cash_pln")) + amount, 8)
    if "cash_balance_pln" in payload:
        payload["cash_balance_pln"] = round(finite(payload.get("cash_balance_pln")) + amount, 8)
    if not paper:
        payload["base_cash_pln"] = round(
            finite(payload.get("base_cash_pln"), finite(payload.get("cash_pln")) - amount) + amount,
            8,
        )
    if payload.get("total_value_pln") is not None:
        payload["total_value_pln"] = round(finite(payload.get("total_value_pln")) + amount, 8)
    if payload.get("total_return_pln") is not None:
        payload["total_return_pln"] = round(finite(payload.get("total_return_pln")) + amount, 8)
    payload["cash_interest_accrued_pln"] = round(finite(payload.get("cash_interest_accrued_pln")) + amount, 8)
    yield_meta = dict(payload.get("cash_yield") or {})
    yield_meta["total_interest_accrued"] = round(finite(yield_meta.get("total_interest_accrued")) + amount, 8)
    yield_meta["historical_correction_applied"] = CORRECTION_ID
    payload["cash_yield"] = yield_meta
    payload.setdefault("cash_yield_corrections", []).append(receipt)


def add_usd_correction(payload: dict[str, Any], amount: float, receipt: dict[str, Any]) -> None:
    if has_correction(payload):
        return
    payload["cash_interest_balance_usd"] = round(finite(payload.get("cash_interest_balance_usd")) + amount, 8)
    payload["cash_interest_accrued_usd"] = round(finite(payload.get("cash_interest_accrued_usd")) + amount, 8)
    payload["cash_usd"] = round(finite(payload.get("cash_usd"), finite(payload.get("cash_pln"))) + amount, 8)
    payload["cash_pln"] = payload["cash_usd"]
    if payload.get("total_value_usd") is not None:
        payload["total_value_usd"] = round(finite(payload.get("total_value_usd")) + amount, 8)
        payload["total_value_pln"] = payload["total_value_usd"]
    if payload.get("total_return_usd") is not None:
        payload["total_return_usd"] = round(finite(payload.get("total_return_usd")) + amount, 8)
        payload["total_return_pln"] = payload["total_return_usd"]
    yield_meta = dict(payload.get("cash_yield") or {})
    yield_meta["total_interest_accrued"] = round(finite(yield_meta.get("total_interest_accrued")) + amount, 8)
    yield_meta["historical_correction_applied"] = CORRECTION_ID
    payload["cash_yield"] = yield_meta
    payload.setdefault("cash_yield_corrections", []).append(receipt)


def main() -> None:
    public = load(PUBLIC_PATH)
    paper = load(PAPER_PATH)
    usd = load(USD_PATH)
    if has_correction(public) and has_correction(paper) and has_correction(usd):
        print(json.dumps({"status": "ALREADY_APPLIED", "correction_id": CORRECTION_ID}))
        return

    activation = parse_timestamp((public.get("cash_yield") or {}).get("started_at"))
    if activation is None:
        raise AssertionError("Cannot backfill before cash-yield activation timestamp is known")
    policy = load_policy()
    pln_rate = finite((policy.get("pln") or {}).get("annual_rate"))
    usd_rate = finite((policy.get("usd") or {}).get("annual_rate"))
    pln_amount, events, initial_cash = correction_from_ledger(
        public, paper, activation=activation, annual_rate=pln_rate
    )
    usd_amount, _, _ = correction_from_ledger(
        public, paper, activation=activation, annual_rate=usd_rate
    )

    common = {
        "correction_id": CORRECTION_ID,
        "basis": "durable paper_portfolio.transactions before cash-yield engine activation",
        "period_end": activation.isoformat(timespec="seconds"),
        "initial_cash": round(initial_cash, 8),
        "events": events,
        "retroactive_estimation": False,
        "idempotent": True,
    }
    pln_receipt = {
        **common,
        "currency": "PLN",
        "annual_rate": pln_rate,
        "amount": round(pln_amount, 8),
    }
    usd_receipt = {
        **common,
        "currency": "USD",
        "annual_rate": usd_rate,
        "amount": round(usd_amount, 8),
    }

    add_pln_correction(public, pln_amount, pln_receipt, paper=False)
    add_pln_correction(paper, pln_amount, pln_receipt, paper=True)
    add_usd_correction(usd, usd_amount, usd_receipt)
    write_atomic(PUBLIC_PATH, public)
    write_atomic(PAPER_PATH, paper)
    write_atomic(USD_PATH, usd)
    print(
        json.dumps(
            {
                "status": "APPLIED",
                "pln": round(pln_amount, 8),
                "usd": round(usd_amount, 8),
                "events": len(events),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
