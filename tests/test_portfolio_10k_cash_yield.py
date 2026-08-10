from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.portfolio_10k_cash_yield import (
    accrue_pln_ledgers,
    advance_usd_cash_ledger,
    interval_interest,
)


POLICY = {
    "day_count_basis": "ACT/365",
    "pln": {
        "benchmark": "NBP_REFERENCE_RATE",
        "annual_rate": 0.0375,
        "rate_percent": 3.75,
        "effective_from": "2026-07-08T00:00:00+02:00",
        "verified_at": "2026-08-10T20:24:00+02:00",
        "source_name": "NBP",
        "source_url": "https://nbp.pl/",
        "label_pl": "NBP 3,75%",
        "label_en": "NBP 3.75%",
    },
    "usd": {
        "benchmark": "FEDERAL_FUNDS_TARGET_MIDPOINT",
        "annual_rate": 0.03625,
        "rate_percent": 3.625,
        "target_range_low": 0.035,
        "target_range_high": 0.0375,
        "effective_from": "2026-07-30T00:00:00-04:00",
        "verified_at": "2026-08-10T20:24:00+02:00",
        "source_name": "Federal Reserve FOMC",
        "source_url": "https://www.federalreserve.gov/",
        "label_pl": "Fed midpoint 3,625%",
        "label_en": "Fed midpoint 3.625%",
    },
}


def test_interval_interest_act_365():
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    assert round(interval_interest(1000.0, 0.0365, start, end), 8) == 0.1


def test_pln_first_run_starts_clock_without_backfill():
    now = datetime(2026, 8, 10, 18, 30, tzinfo=timezone.utc)
    public = {"base_cash_pln": 455.25, "cash_pln": 455.25, "total_value_pln": 10045.77}
    paper = {"cash_pln": 455.25, "total_value_pln": 10045.77}
    result = accrue_pln_ledgers(public, paper, POLICY, now)
    assert result["initialized"] is True
    assert public["cash_pln"] == 455.25
    assert paper["cash_pln"] == 455.25
    assert public["cash_yield"]["annual_rate"] == 0.0375
    assert public["cash_yield"]["last_accrued_at"] == now.isoformat(timespec="seconds")


def test_pln_one_day_credit_is_added_to_public_and_paper_cash():
    start = datetime(2026, 8, 10, 18, 30, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    public = {
        "base_cash_pln": 455.25,
        "cash_pln": 455.25,
        "total_value_pln": 10045.77,
        "cash_yield": {"last_accrued_at": start.isoformat(), "annual_rate": 0.0375, "started_at": start.isoformat()},
        "cash_interest_accrued_pln": 0.0,
    }
    paper = {"cash_pln": 455.25, "total_value_pln": 10045.77, "cash_interest_accrued_pln": 0.0}
    result = accrue_pln_ledgers(public, paper, POLICY, end)
    expected = 455.25 * 0.0375 / 365.0
    assert abs(result["interest_pln"] - expected) < 1e-8
    assert abs(paper["cash_pln"] - (455.25 + expected)) < 1e-8
    assert abs(public["base_cash_pln"] - paper["cash_pln"]) < 1e-8


def test_pln_same_timestamp_is_idempotent():
    now = datetime(2026, 8, 11, 18, 30, tzinfo=timezone.utc)
    public = {
        "base_cash_pln": 455.25,
        "cash_pln": 455.25,
        "total_value_pln": 10045.77,
        "cash_yield": {"last_accrued_at": now.isoformat(), "annual_rate": 0.0375},
    }
    paper = {"cash_pln": 455.25, "total_value_pln": 10045.77}
    result = accrue_pln_ledgers(public, paper, POLICY, now)
    assert result["interest_pln"] == 0.0
    assert paper["cash_pln"] == 455.25


def test_usd_first_run_is_independent_and_uses_fed_midpoint():
    now = datetime(2026, 8, 10, 18, 30, tzinfo=timezone.utc)
    source = {
        "starting_capital_pln": 10000.0,
        "base_cash_pln": 455.25,
        "cash_interest_accrued_pln": 0.0,
    }
    ledger = advance_usd_cash_ledger({}, source, POLICY, now)
    assert ledger["cash_usd"] == 455.25
    assert ledger["cash_yield"]["annual_rate"] == 0.03625
    assert ledger["cash_yield"]["target_range_low"] == 0.035
    assert ledger["cash_yield"]["target_range_high"] == 0.0375


def test_usd_ignores_new_pln_interest_but_accrues_fed_interest():
    start = datetime(2026, 8, 10, 18, 30, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    previous = {
        "cash_principal_usd": 455.25,
        "cash_interest_balance_usd": 0.0,
        "cash_interest_accrued_usd": 0.0,
        "cash_yield": {
            "last_accrued_at": start.isoformat(),
            "annual_rate": 0.03625,
            "started_at": start.isoformat(),
            "source_base_cash_pln": 455.25,
            "source_interest_accrued_pln": 0.0,
        },
    }
    pln_interest = 455.25 * 0.0375 / 365.0
    source = {
        "starting_capital_pln": 10000.0,
        "base_cash_pln": 455.25 + pln_interest,
        "cash_interest_accrued_pln": pln_interest,
    }
    ledger = advance_usd_cash_ledger(previous, source, POLICY, end)
    expected_fed = 455.25 * 0.03625 / 365.0
    assert abs(ledger["cash_principal_usd"] - 455.25) < 1e-8
    assert abs(ledger["cash_interest_accrued_usd"] - expected_fed) < 1e-8
    assert abs(ledger["cash_usd"] - (455.25 + expected_fed)) < 1e-8
    assert abs(ledger["cash_yield"]["mirrored_trade_cash_delta_usd"]) < 1e-8


def test_usd_mirrors_trade_cash_delta_separately_from_interest():
    now = datetime(2026, 8, 10, 19, 30, tzinfo=timezone.utc)
    previous = {
        "cash_principal_usd": 455.25,
        "cash_interest_balance_usd": 0.0,
        "cash_interest_accrued_usd": 0.0,
        "cash_yield": {
            "last_accrued_at": now.isoformat(),
            "annual_rate": 0.03625,
            "source_base_cash_pln": 455.25,
            "source_interest_accrued_pln": 0.0,
        },
    }
    source = {
        "starting_capital_pln": 10000.0,
        "base_cash_pln": 655.25,
        "cash_interest_accrued_pln": 0.0,
    }
    ledger = advance_usd_cash_ledger(previous, source, POLICY, now)
    assert ledger["cash_principal_usd"] == 655.25
    assert ledger["cash_usd"] == 655.25
    assert ledger["cash_yield"]["mirrored_trade_cash_delta_usd"] == 200.0
