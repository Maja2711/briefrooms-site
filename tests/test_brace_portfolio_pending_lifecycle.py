from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brace_portfolio_execution import (
    MAX_EXECUTION_CANDLE_AGE,
    _expected_market_open,
    _validate_quote,
)
from brace_portfolio_state_sync import (
    PUBLIC_PENDING_MAX_AGE,
    reconcile_public_decisions,
)


def test_xetra_open_state_is_not_inferred_from_delayed_quote_age():
    open_now = datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc)
    closed_now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    assert _expected_market_open("SXR8.DE", open_now) is True
    assert _expected_market_open("SXR8.DE", closed_now) is False


def test_delayed_public_candle_inside_execution_tolerance_is_valid():
    now = datetime(2026, 9, 21, 13, 15, tzinfo=timezone.utc)
    quote = {
        "price": 100.0,
        "fx_to_pln": 4.3,
        "completed_at": (now - timedelta(minutes=20)).isoformat(),
        "market_open": True,
    }
    assert _validate_quote(quote, now) is None

    quote["completed_at"] = (
        now - MAX_EXECUTION_CANDLE_AGE - timedelta(seconds=1)
    ).isoformat()
    assert _validate_quote(quote, now) == "STALE_QUOTE"


def test_public_pending_decision_disappears_after_hard_ttl():
    now = datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc)
    decision = {
        "decision_id": "d-stale",
        "generated_at": (now - PUBLIC_PENDING_MAX_AGE - timedelta(seconds=1)).isoformat(),
        "action": "ADD",
        "instrument": "sxr8",
        "confidence": 1.0,
    }
    orders = {
        "orders": [
            {
                "decision_id": "d-stale",
                "status": "WAITING_FOR_MARKET",
                "failure_reason": "MARKET_CLOSED",
            }
        ]
    }
    assert reconcile_public_decisions([decision], {"positions": []}, orders, now=now) == []


def test_fresh_public_pending_decision_remains_visible():
    now = datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc)
    decision = {
        "decision_id": "d-fresh",
        "generated_at": (now - timedelta(hours=2)).isoformat(),
        "action": "ADD",
        "instrument": "sxr8",
        "confidence": 1.0,
    }
    orders = {
        "orders": [
            {
                "decision_id": "d-fresh",
                "status": "WAITING_FOR_MARKET",
                "failure_reason": "MARKET_CLOSED",
            }
        ]
    }
    rows = reconcile_public_decisions([decision], {"positions": []}, orders, now=now)
    assert len(rows) == 1
    assert rows[0]["execution_status"] == "PENDING"
