from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_portfolio_data as data


def config() -> SimpleNamespace:
    return SimpleNamespace(
        monitoring_max_price_age_hours=6.0,
        analysis_max_price_age_hours=72.0,
        maximum_missing_instruments=0,
        maximum_single_price_jump=0.35,
        safe_mode_on_stale_data=True,
    )


def held_fwia(*, market_status: str = "closed", price_timestamp: str = "2026-08-18T15:25:00+00:00") -> dict:
    return {
        "id": "fwia",
        "broker_symbol": "FWIA.DE",
        "market_symbol": "FWIA.DE",
        "currency": "EUR",
        "current_price": 8.218,
        "current_fx_to_pln": 4.32488,
        "current_price_updated_at": price_timestamp,
        "current_fx_updated_at": "2026-08-18T17:10:00+00:00",
        "market_status": market_status,
        "market_timezone": "Europe/Berlin",
    }


def benchmark_fwia(*, price_timestamp: str | None = "2026-08-18T15:25:00+00:00") -> dict:
    return {
        "broker_symbol": "FWIA.DE",
        "market_symbol": "FWIA.DE",
        "currency": "EUR",
        "current_price": 8.218,
        "current_fx_to_pln": 4.32488,
        "current_price_updated_at": price_timestamp,
        "current_fx_updated_at": "2026-08-18T17:10:00+00:00",
    }


def test_held_benchmark_inherits_closed_session_metadata_and_does_not_false_fail():
    # This reproduces the committed 2026-08-18 false fail-safe: the held FWIA
    # was marked closed, while the duplicate benchmark view had no market_status.
    # At 21:50 UTC the same 15:25 close is 6h25m old: invalid under the monitor
    # 6h limit, but valid under the intended closed-market 72h limit.
    now = datetime(2026, 8, 18, 21, 50, 11, tzinfo=timezone.utc)
    report = data.data_freshness_report(
        {"positions": [held_fwia()], "benchmark": benchmark_fwia()},
        config(),
        now,
        "monitor",
    )

    assert report["safe_mode"] is False
    assert report["reasons"] == []
    assert report["invalid_instruments"] == 0

    benchmark_row = next(
        row for row in report["instruments"] if row["instrument_id"] == "__benchmark__"
    )
    assert benchmark_row["freshness_alias_of"] == "fwia"
    assert benchmark_row["market_status"] == "closed"
    assert benchmark_row["maximum_price_age_hours"] == 72.0
    assert benchmark_row["price_age_hours"] > 6.0
    assert benchmark_row["status"] == "OK"


def test_distinct_stale_benchmark_still_fails_closed():
    now = datetime(2026, 8, 18, 21, 50, 11, tzinfo=timezone.utc)
    distinct_benchmark = {
        **benchmark_fwia(),
        "broker_symbol": "OTHER.DE",
        "market_symbol": "OTHER.DE",
    }
    report = data.data_freshness_report(
        {"positions": [held_fwia()], "benchmark": distinct_benchmark},
        config(),
        now,
        "monitor",
    )

    assert report["safe_mode"] is True
    assert "STALE_MARKET_DATA" in report["reasons"]
    assert "TOO_MANY_INVALID_INSTRUMENTS" in report["reasons"]
    assert report["invalid_instruments"] == 1

    benchmark_row = next(
        row for row in report["instruments"] if row["instrument_id"] == "__benchmark__"
    )
    assert benchmark_row["freshness_alias_of"] is None
    assert benchmark_row["maximum_price_age_hours"] == 6.0
    assert "PRICE_STALE" in benchmark_row["reasons"]


def test_true_open_market_stale_quote_still_fails_closed():
    now = datetime(2026, 8, 18, 21, 50, 11, tzinfo=timezone.utc)
    report = data.data_freshness_report(
        {
            "positions": [
                held_fwia(
                    market_status="open",
                    price_timestamp="2026-08-18T14:00:00+00:00",
                )
            ],
            "benchmark": benchmark_fwia(
                price_timestamp="2026-08-18T14:00:00+00:00"
            ),
        },
        config(),
        now,
        "monitor",
    )

    assert report["safe_mode"] is True
    assert "STALE_MARKET_DATA" in report["reasons"]
    assert "TOO_MANY_INVALID_INSTRUMENTS" in report["reasons"]
    assert report["invalid_instruments"] == 2
    assert all("PRICE_STALE" in row["reasons"] for row in report["instruments"])


def test_matching_benchmark_missing_timestamp_is_not_masked_by_aliasing():
    now = datetime(2026, 8, 18, 21, 50, 11, tzinfo=timezone.utc)
    report = data.data_freshness_report(
        {
            "positions": [held_fwia()],
            "benchmark": benchmark_fwia(price_timestamp=None),
        },
        config(),
        now,
        "monitor",
    )

    assert report["safe_mode"] is True
    assert "TOO_MANY_INVALID_INSTRUMENTS" in report["reasons"]
    assert report["invalid_instruments"] == 1
    benchmark_row = next(
        row for row in report["instruments"] if row["instrument_id"] == "__benchmark__"
    )
    assert benchmark_row["freshness_alias_of"] == "fwia"
    assert "PRICE_TIMESTAMP_MISSING" in benchmark_row["reasons"]
