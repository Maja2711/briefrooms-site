from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "portfolio_10k_open_live_now_test", ROOT / "scripts" / "portfolio_10k_open_live_now.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def staged_data():
    return {
        "portfolio_id": "briefrooms-xtb-10k", "status": "pending_open",
        "starting_capital_pln": 10000.0, "base_cash_pln": 10000.0,
        "cash_pln": 10000.0, "total_value_pln": 10000.0,
        "cost_assumptions": {"fx_conversion_fee_rate": 0.005},
        "positions": [
            {"id":"googl","broker_symbol":"GOOGL.US","market_symbol":"GOOGL","currency":"USD","target_weight":0.5,"status":"pending"},
            {"id":"fwia","broker_symbol":"FWIA.DE","market_symbol":"FWIA.DE","currency":"EUR","target_weight":0.5,"status":"pending"},
        ],
        "benchmark": {"currency": "EUR"}, "snapshots": [], "audit_corrections": [{"reason": "invalid legacy close"}],
        "staged_entry_batches": [{
            "executed_at_utc":"2026-07-17T17:22:19+00:00",
            "opened":[{"symbol":"GOOGL.US","timestamp_utc":"2026-07-17T17:15+00:00","price":100.0,"fx_to_pln":4.0,"entry_value_pln":5000.0}],
            "still_pending":[{"symbol":"FWIA.DE","reason":"market closed"}],
        }],
    }


def test_reconcile_creates_partially_active_portfolio_and_preserves_audit():
    data = staged_data()
    assert MODULE.reconcile_staged_entries(data) is True
    assert data["status"] == "partially_active"
    assert data["positions"][0]["status"] == "active"
    assert data["positions"][1]["status"] == "pending"
    assert data["base_cash_pln"] == 5000.0
    assert data["cash_pln"] == 5000.0
    assert data["audit_corrections"] == [{"reason": "invalid legacy close"}]
    assert data["positions"][0]["entry_timestamp_utc"] == "2026-07-17T17:15+00:00"


def test_reconcile_is_idempotent():
    data = staged_data()
    MODULE.reconcile_staged_entries(data)
    first = copy.deepcopy(data)
    assert MODULE.reconcile_staged_entries(data) is False
    assert data == first


def test_live_run_opens_only_pending_and_never_creates_duplicate_batch(tmp_path, monkeypatch):
    path = tmp_path / "portfolio.json"
    path.write_text(json.dumps(staged_data()), encoding="utf-8")
    now = datetime(2026, 7, 20, 15, 5, tzinfo=timezone.utc)
    requested = []

    def quote(symbol, _now):
        requested.append(symbol)
        assert symbol == "FWIA.DE"
        return 10.0, datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(MODULE.base, "validate_config", lambda data: None)
    monkeypatch.setattr(MODULE, "utc_now", lambda: now)
    monkeypatch.setattr(MODULE, "fresh_completed_quote", quote)
    monkeypatch.setattr(MODULE, "fx_quote", lambda currency, current: (4.0, datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)))
    MODULE.run(path)
    after_first = json.loads(path.read_text(encoding="utf-8"))
    assert requested == ["FWIA.DE"]
    assert after_first["status"] == "active"
    assert len(after_first["staged_entry_batches"]) == 2
    assert after_first["staged_entry_batches"][1]["opened"][0]["symbol"] == "FWIA.DE"
    requested.clear()
    MODULE.run(path)
    after_second = json.loads(path.read_text(encoding="utf-8"))
    assert requested == []
    assert len(after_second["staged_entry_batches"]) == 2
    assert after_second == after_first
