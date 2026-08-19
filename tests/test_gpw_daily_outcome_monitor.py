from __future__ import annotations

from datetime import datetime, timezone

from scripts import gpw_daily_outcome_monitor as monitor


def payload():
    return {
        "date": "2026-08-19",
        "generated_at": "2026-08-19T11:53:57+02:00",
        "decision": "TRANSAKCJA",
        "selection": {
            "symbol": "PKO.WA",
            "ticker": "PKO",
            "entry_zone": [109.13, 110.12],
            "stop": 106.62,
            "target": 114.57,
            "valid_until": "2026-08-21",
            "market_snapshot": {
                "observed_at": "2026-08-19T11:39:32+02:00",
                "last": 109.46,
            },
        },
        "outcome": {"status": "PENDING", "activated": None},
    }


def test_same_day_target_resolves_before_expiry(monkeypatch):
    monkeypatch.setattr(monitor, "fetch_intraday", lambda symbol: [
        {
            "timestamp": datetime(2026, 8, 19, 13, 0, tzinfo=timezone.utc),
            "open": 113.0,
            "high": 115.2,
            "low": 112.8,
            "close": 115.0,
        }
    ])
    row = payload()
    changed = monitor.resolve_pending(row, datetime(2026, 8, 19, 13, 10, tzinfo=timezone.utc))
    assert changed is True
    assert row["outcome"]["status"] == "RESOLVED"
    assert row["outcome"]["exit_reason"] == "target"
    assert row["outcome"]["exit_price"] == 114.57
    assert row["outcome"]["entry_price"] == 109.46
    assert row["outcome"]["r_multiple"] == 1.799


def test_same_bar_target_and_stop_is_conservatively_stop(monkeypatch):
    monkeypatch.setattr(monitor, "fetch_intraday", lambda symbol: [
        {
            "timestamp": datetime(2026, 8, 19, 13, 0, tzinfo=timezone.utc),
            "open": 110.0,
            "high": 115.0,
            "low": 106.0,
            "close": 112.0,
        }
    ])
    row = payload()
    monitor.resolve_pending(row, datetime(2026, 8, 19, 13, 10, tzinfo=timezone.utc))
    assert row["outcome"]["exit_reason"] == "stop"
    assert row["outcome"]["exit_price"] == 106.62
