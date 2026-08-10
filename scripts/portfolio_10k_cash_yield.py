#!/usr/bin/env python3
"""Accrue model-cash yield without retroactive backfilling.

PLN cash earns the NBP reference rate.  The public PLN ledger and BRACE paper
ledger are credited together so interest is both visible and available to the
paper execution engine.  The independent USD portfolio uses the helper
``advance_usd_cash_ledger`` from this module and accrues at the midpoint of the
Federal Reserve target range.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data" / "portfolio10k" / "cash_yield_policy.json"
PUBLIC_PATH = ROOT / "data" / "investments" / "portfolio_10k.json"
PAPER_PATH = ROOT / "data" / "portfolio10k" / "paper_portfolio.json"
YEAR_SECONDS = 365.0 * 24.0 * 60.0 * 60.0


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp = tmp.name
    os.replace(temp, path)


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = load_json(path)
    if policy.get("schema_version") != "portfolio-10k-cash-yield-policy-v1":
        raise ValueError("Unsupported Portfolio 10K cash-yield policy")
    if policy.get("day_count_basis") != "ACT/365":
        raise ValueError("Portfolio 10K cash yield must use ACT/365")
    for currency in ("pln", "usd"):
        row = policy.get(currency) or {}
        rate = finite(row.get("annual_rate"), -1.0)
        if not 0.0 <= rate <= 0.25:
            raise ValueError(f"Invalid {currency.upper()} annual cash rate")
    return policy


def interval_interest(balance: float, annual_rate: float, start: datetime, end: datetime) -> float:
    """Simple ACT/365 accrual for one interval; credited interest compounds later."""
    if balance <= 0 or annual_rate <= 0 or end <= start:
        return 0.0
    seconds = (end - start).total_seconds()
    return balance * annual_rate * seconds / YEAR_SECONDS


def accrue_with_policy_change(
    balance: float,
    start: datetime,
    end: datetime,
    previous_rate: float,
    policy_row: Mapping[str, Any],
) -> float:
    current_rate = finite(policy_row.get("annual_rate"))
    effective = parse_timestamp(policy_row.get("effective_from"))
    if (
        effective is not None
        and start < effective < end
        and abs(previous_rate - current_rate) > 1e-12
    ):
        first = interval_interest(balance, previous_rate, start, effective)
        return first + interval_interest(balance + first, current_rate, effective, end)
    rate = current_rate if effective is None or end >= effective else previous_rate
    return interval_interest(balance, rate, start, end)


def yield_metadata(
    currency: str,
    policy_row: Mapping[str, Any],
    now: datetime,
    *,
    accrued_this_run: float,
    total_accrued: float,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    previous = previous or {}
    result = {
        "currency": currency,
        "benchmark": policy_row.get("benchmark"),
        "annual_rate": finite(policy_row.get("annual_rate")),
        "rate_percent": finite(policy_row.get("rate_percent")),
        "day_count_basis": "ACT/365",
        "crediting_frequency": "hourly_on_portfolio_refresh",
        "effective_from": policy_row.get("effective_from"),
        "verified_at": policy_row.get("verified_at"),
        "source_name": policy_row.get("source_name"),
        "source_url": policy_row.get("source_url"),
        "label_pl": policy_row.get("label_pl"),
        "label_en": policy_row.get("label_en"),
        "last_accrued_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "accrued_this_run": round(accrued_this_run, 8),
        "total_interest_accrued": round(total_accrued, 8),
        "retroactive_backfill": False,
    }
    if currency == "USD":
        result["target_range_low"] = finite(policy_row.get("target_range_low"))
        result["target_range_high"] = finite(policy_row.get("target_range_high"))
    if previous.get("started_at"):
        result["started_at"] = previous.get("started_at")
    else:
        result["started_at"] = result["last_accrued_at"]
    return result


def accrue_pln_ledgers(
    public: dict[str, Any],
    paper: dict[str, Any],
    policy: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Credit NBP-rate cash interest to public and paper PLN ledgers exactly once."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    row = policy.get("pln") or {}
    previous = dict(public.get("cash_yield") or {})
    last = parse_timestamp(previous.get("last_accrued_at"))
    cumulative = finite(public.get("cash_interest_accrued_pln"), finite(previous.get("total_interest_accrued")))

    # First deployment only starts the clock.  It deliberately does not guess
    # historic cash balances or backfill interest at today's policy rate.
    if last is None:
        metadata = yield_metadata(
            "PLN", row, now, accrued_this_run=0.0, total_accrued=cumulative, previous=previous
        )
        public["cash_yield"] = metadata
        paper["cash_yield"] = deepcopy(metadata)
        public["cash_interest_accrued_pln"] = round(cumulative, 8)
        paper["cash_interest_accrued_pln"] = round(
            finite(paper.get("cash_interest_accrued_pln"), cumulative), 8
        )
        return {"initialized": True, "interest_pln": 0.0, "cash_pln": finite(paper.get("cash_pln"))}

    if now <= last:
        return {"initialized": False, "interest_pln": 0.0, "cash_pln": finite(paper.get("cash_pln"))}

    paper_cash_before = finite(paper.get("cash_pln"), finite(public.get("base_cash_pln"), finite(public.get("cash_pln"))))
    if paper_cash_before < 0:
        raise ValueError("Cannot accrue interest on negative Portfolio 10K paper cash")
    previous_rate = finite(previous.get("annual_rate"), finite(row.get("annual_rate")))
    interest = accrue_with_policy_change(paper_cash_before, last, now, previous_rate, row)
    paper_cash_after = paper_cash_before + interest
    cumulative += interest

    paper["cash_pln"] = round(paper_cash_after, 8)
    if "cash_balance_pln" in paper:
        paper["cash_balance_pln"] = round(paper_cash_after, 8)
    if paper.get("total_value_pln") is not None:
        paper["total_value_pln"] = round(finite(paper.get("total_value_pln")) + interest, 8)
    paper["cash_interest_accrued_pln"] = round(
        finite(paper.get("cash_interest_accrued_pln")) + interest, 8
    )

    # base_cash_pln is the deployable cash principal used by the hourly model.
    # Interest is credited into it, so the existing valuation and paper-order
    # paths automatically see the higher available balance.
    public["base_cash_pln"] = round(paper_cash_after, 8)
    public["cash_pln"] = round(finite(public.get("cash_pln"), paper_cash_before) + interest, 8)
    public["cash_balance_pln"] = public["cash_pln"]
    if public.get("total_value_pln") is not None:
        public["total_value_pln"] = round(finite(public.get("total_value_pln")) + interest, 8)
    public["cash_interest_accrued_pln"] = round(cumulative, 8)

    metadata = yield_metadata(
        "PLN", row, now, accrued_this_run=interest, total_accrued=cumulative, previous=previous
    )
    public["cash_yield"] = metadata
    paper["cash_yield"] = deepcopy(metadata)
    return {
        "initialized": False,
        "interest_pln": round(interest, 8),
        "cash_pln": round(paper_cash_after, 8),
    }


def advance_usd_cash_ledger(
    previous_payload: Mapping[str, Any] | None,
    source: Mapping[str, Any],
    policy: Mapping[str, Any],
    now: datetime,
    *,
    starting_capital_usd: float = 10_000.0,
) -> dict[str, Any]:
    """Maintain an independent USD cash ledger while mirroring only trade cash flows.

    PLN interest is removed from the source-cash delta before that delta is
    mirrored into USD.  The USD ledger then earns only the Fed target midpoint.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    previous_payload = previous_payload or {}
    previous_yield = dict(previous_payload.get("cash_yield") or {})
    row = policy.get("usd") or {}
    source_start = finite(source.get("starting_capital_pln"), 10_000.0)
    source_base = finite(source.get("base_cash_pln"), finite(source.get("cash_pln")))
    source_interest = finite(source.get("cash_interest_accrued_pln"))

    if previous_payload.get("cash_principal_usd") is None:
        principal = max(0.0, source_base - source_interest) / max(source_start, 1e-12) * starting_capital_usd
        interest_balance = 0.0
        interest_cumulative = 0.0
        metadata = yield_metadata(
            "USD", row, now, accrued_this_run=0.0, total_accrued=0.0, previous=previous_yield
        )
        metadata.update({
            "source_base_cash_pln": round(source_base, 8),
            "source_interest_accrued_pln": round(source_interest, 8),
            "mirrored_trade_cash_delta_usd": 0.0,
        })
        return {
            "cash_principal_usd": round(principal, 8),
            "cash_interest_balance_usd": 0.0,
            "cash_interest_accrued_usd": 0.0,
            "cash_usd": round(principal, 8),
            "cash_yield": metadata,
        }

    principal = finite(previous_payload.get("cash_principal_usd"))
    interest_balance = finite(previous_payload.get("cash_interest_balance_usd"))
    interest_cumulative = finite(previous_payload.get("cash_interest_accrued_usd"))
    last = parse_timestamp(previous_yield.get("last_accrued_at")) or now
    previous_cash = max(0.0, principal + interest_balance)
    previous_rate = finite(previous_yield.get("annual_rate"), finite(row.get("annual_rate")))
    earned = accrue_with_policy_change(previous_cash, last, now, previous_rate, row) if now > last else 0.0
    interest_balance += earned
    interest_cumulative += earned

    previous_source_base = finite(previous_yield.get("source_base_cash_pln"), source_base)
    previous_source_interest = finite(previous_yield.get("source_interest_accrued_pln"), source_interest)
    source_trade_delta_pln = (source_base - previous_source_base) - (source_interest - previous_source_interest)
    mirrored_delta = source_trade_delta_pln / max(source_start, 1e-12) * starting_capital_usd

    if mirrored_delta >= 0:
        principal += mirrored_delta
    else:
        reduction = -mirrored_delta
        from_principal = min(principal, reduction)
        principal -= from_principal
        reduction -= from_principal
        if reduction > 0:
            interest_balance = max(0.0, interest_balance - reduction)

    cash_usd = max(0.0, principal + interest_balance)
    metadata = yield_metadata(
        "USD", row, now, accrued_this_run=earned, total_accrued=interest_cumulative, previous=previous_yield
    )
    metadata.update({
        "source_base_cash_pln": round(source_base, 8),
        "source_interest_accrued_pln": round(source_interest, 8),
        "mirrored_trade_cash_delta_usd": round(mirrored_delta, 8),
    })
    return {
        "cash_principal_usd": round(max(0.0, principal), 8),
        "cash_interest_balance_usd": round(max(0.0, interest_balance), 8),
        "cash_interest_accrued_usd": round(max(0.0, interest_cumulative), 8),
        "cash_usd": round(cash_usd, 8),
        "cash_yield": metadata,
    }


def main() -> None:
    policy = load_policy()
    public = load_json(PUBLIC_PATH)
    paper = load_json(PAPER_PATH)
    result = accrue_pln_ledgers(public, paper, policy, datetime.now(timezone.utc))
    write_json_atomic(PUBLIC_PATH, public)
    write_json_atomic(PAPER_PATH, paper)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
