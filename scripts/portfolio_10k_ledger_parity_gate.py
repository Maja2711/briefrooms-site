#!/usr/bin/env python3
"""Fail-closed accounting parity gate for Portfolio 10K publication.

Checks durable paper ledger vs public PLN book, plus independent USD cash-yield
book.  This is intentionally stricter than the public valuation gate: a fresh
mark may not publish when quantities, deployable cash or interest ledgers drift.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PATH = ROOT / "data/investments/portfolio_10k.json"
PAPER_PATH = ROOT / "data/portfolio10k/paper_portfolio.json"
USD_PATH = ROOT / "data/investments/portfolio_10k_usd.json"
QTY_TOL = 1e-8
CASH_TOL = 0.02
INTEREST_TOL = 1e-6


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AssertionError(f"Non-finite value: {value!r}")
    if not math.isfinite(number):
        raise AssertionError(f"Non-finite value: {value!r}")
    return number


def positions(payload: dict[str, Any], *, paper: bool) -> dict[str, dict[str, Any]]:
    allowed = {"active", "paper_active"} if paper else {"active"}
    return {
        str(row.get("id") or row.get("instrument_id")): row
        for row in payload.get("positions", []) or []
        if str(row.get("status") or "active") in allowed
    }


def main() -> None:
    public = load(PUBLIC_PATH)
    paper = load(PAPER_PATH)
    usd = load(USD_PATH)

    pub = positions(public, paper=False)
    pap = positions(paper, paper=True)
    assert set(pub) == set(pap), (sorted(pub), sorted(pap))
    quantity_gaps: dict[str, float] = {}
    for instrument_id in sorted(pub):
        gap = abs(finite(pub[instrument_id].get("quantity")) - finite(pap[instrument_id].get("quantity")))
        quantity_gaps[instrument_id] = gap
        assert gap <= QTY_TOL, f"Quantity drift {instrument_id}: {gap}"

    public_deployable = finite(public.get("base_cash_pln", public.get("cash_pln")))
    paper_cash = finite(paper.get("cash_pln"))
    assert abs(public_deployable - paper_cash) <= CASH_TOL, (
        f"Deployable cash drift public={public_deployable} paper={paper_cash}"
    )

    public_interest = finite(public.get("cash_interest_accrued_pln", 0.0))
    paper_interest = finite(paper.get("cash_interest_accrued_pln", 0.0))
    assert abs(public_interest - paper_interest) <= INTEREST_TOL, (
        f"PLN interest drift public={public_interest} paper={paper_interest}"
    )
    public_yield = public.get("cash_yield") or {}
    paper_yield = paper.get("cash_yield") or {}
    assert public_yield.get("last_accrued_at") == paper_yield.get("last_accrued_at"), "PLN yield timestamps differ"
    assert abs(finite(public_yield.get("total_interest_accrued", 0.0)) - public_interest) <= INTEREST_TOL
    assert abs(finite(paper_yield.get("total_interest_accrued", 0.0)) - paper_interest) <= INTEREST_TOL

    usd_principal = finite(usd.get("cash_principal_usd", 0.0))
    usd_interest = finite(usd.get("cash_interest_balance_usd", 0.0))
    usd_cash = finite(usd.get("cash_usd", usd.get("cash_pln")))
    assert abs((usd_principal + usd_interest) - usd_cash) <= CASH_TOL, "USD cash principal + interest != cash"
    usd_cumulative = finite(usd.get("cash_interest_accrued_usd", 0.0))
    usd_yield = usd.get("cash_yield") or {}
    assert abs(finite(usd_yield.get("total_interest_accrued", 0.0)) - usd_cumulative) <= INTEREST_TOL

    reconciliation = public.get("execution_reconciliation") or {}
    assert reconciliation.get("execution_authority") == "paper_portfolio.transactions"

    mtm = paper.get("execution_mtm_sync") or {}
    if mtm:
        assert mtm.get("quantity_parity") is True and mtm.get("cash_parity") is True

    print(json.dumps({
        "status": "PASS",
        "active_positions": sorted(pub),
        "max_quantity_gap": max(quantity_gaps.values(), default=0.0),
        "public_deployable_cash_pln": round(public_deployable, 8),
        "paper_cash_pln": round(paper_cash, 8),
        "pln_interest_accrued": round(public_interest, 8),
        "usd_cash": round(usd_cash, 8),
        "usd_interest_accrued": round(usd_cumulative, 8),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
