from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SPEC = importlib.util.spec_from_file_location(
    "portfolio_10k_weekly_v3", ROOT / "scripts" / "portfolio_10k_weekly_v3.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def sample_data(status="active"):
    return {
        "status": status,
        "starting_capital_pln": 10000.0,
        "cash_pln": 0.0,
        "total_value_pln": 9950.0,
        "total_return_pln": -50.0,
        "positions": [
            {
                "id": "fwia", "broker_symbol": "FWIA.DE", "market_symbol": "FWIA.DE",
                "currency": "EUR", "target_weight": 0.25, "status": "active",
                "entry_date": "2026-07-17", "entry_price": 8.0, "quantity": 70.0,
                "entry_value_pln": 2500.0,
            },
            {
                "id": "googl", "broker_symbol": "GOOGL.US", "market_symbol": "GOOGL",
                "currency": "USD", "target_weight": 0.75, "status": "active",
                "entry_date": "2026-07-16", "entry_price": 350.0, "quantity": 5.0,
                "entry_value_pln": 7500.0,
            },
        ],
        "benchmark": {
            "currency": "EUR", "entry_price": 8.0, "units": 280.0,
            "current_value_pln": 9950.0,
        },
        "snapshots": [{"date": "2026-07-17", "total_value_pln": 9950.0}],
        "weekly_reviews": [{"week_id": "2026-W29"}],
        "closed_positions": [],
    }


def test_common_entry_window_requires_weekday_and_live_overlap():
    assert MODULE.is_common_entry_window(datetime(2026, 7, 17, 14, 50, tzinfo=timezone.utc))
    assert not MODULE.is_common_entry_window(datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc))
    assert not MODULE.is_common_entry_window(datetime(2026, 7, 18, 14, 50, tzinfo=timezone.utc))


def test_legacy_daily_close_entries_are_invalidated_not_preserved_as_trades():
    data = sample_data()
    changed = MODULE.migrate_invalid_initialization(
        data, datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc)
    )
    assert changed is True
    assert data["status"] == "pending_open"
    assert data["cash_pln"] == 10000.0
    assert data["total_return_pln"] == 0.0
    assert data["snapshots"] == []
    assert data["weekly_reviews"] == []
    assert len(data["audit_corrections"]) == 1
    assert data["audit_corrections"][0]["invalidated_positions"][1]["broker_symbol"] == "GOOGL.US"
    for position in data["positions"]:
        assert position["status"] == "pending"
        assert "entry_price" not in position
        assert "quantity" not in position


def test_intraday_initialization_uses_same_timestamp_rule_and_fx_fee():
    data = sample_data(status="pending_open")
    MODULE.migrate_invalid_initialization(
        data, datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc)
    )
    target = datetime(2026, 7, 17, 14, 40, tzinfo=timezone.utc)
    quotes = {
        "fwia": MODULE.IntradayQuote("FWIA.DE", 8.10, target),
        "googl": MODULE.IntradayQuote("GOOGL", 352.0, target),
    }
    fx = {
        "EUR": MODULE.IntradayQuote("EURPLN=X", 4.35, target),
        "USD": MODULE.IntradayQuote("USDPLN=X", 3.80, target),
    }
    MODULE.initialize_from_intraday(data, quotes, fx, target)
    assert data["status"] == "active"
    assert data["model_entry_timestamp_utc"] == "2026-07-17T14:40+00:00"
    assert data["base_cash_pln"] >= -0.02
    assert sum(p["entry_value_pln"] for p in data["positions"]) <= 10000.02
    for position in data["positions"]:
        assert position["entry_timestamp_utc"] == "2026-07-17T14:40+00:00"
        assert position["entry_price_type"] == "first_completed_5m_close_at_or_after_14_40_utc"
        assert position["entry_fee_pln"] > 0


def test_migration_is_idempotent_after_execution_version_is_set():
    data = sample_data()
    now = datetime(2026, 7, 17, 10, 30, tzinfo=timezone.utc)
    assert MODULE.migrate_invalid_initialization(data, now)
    snapshot = dict(data)
    assert MODULE.migrate_invalid_initialization(data, now) is False
    assert data == snapshot
