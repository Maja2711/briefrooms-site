from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SPEC = importlib.util.spec_from_file_location(
    "portfolio_10k_runtime", ROOT / "scripts" / "portfolio_10k_runtime.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def valid_active_data():
    return {
        "status": "active",
        "execution_model_version": "2.0",
        "starting_capital_pln": 10000.0,
        "positions": [
            {"id": "fwia", "target_weight": 1.0, "status": "active", "entry_price": 8.1}
        ],
        "benchmark": {},
    }


def test_migrate_mode_does_not_reset_already_valid_active_portfolio(monkeypatch):
    data = valid_active_data()
    writes = []
    monkeypatch.setattr(MODULE.base, "load_json", lambda path: data)
    monkeypatch.setattr(MODULE.base, "validate_config", lambda payload: None)
    monkeypatch.setattr(MODULE.base, "write_json_atomic", lambda path, payload: writes.append(payload))
    MODULE.run("migrate")
    assert data["status"] == "active"
    assert writes == []


def test_migrate_mode_writes_pending_state_only_when_legacy_was_corrected(monkeypatch):
    data = {
        "status": "active",
        "starting_capital_pln": 10000.0,
        "positions": [
            {
                "id": "fwia", "broker_symbol": "FWIA.DE", "target_weight": 1.0,
                "status": "active", "entry_price": 8.0, "entry_date": "2026-07-17",
            }
        ],
        "benchmark": {"entry_price": 8.0},
        "snapshots": [{"date": "2026-07-17"}],
        "weekly_reviews": [{"week_id": "2026-W29"}],
    }
    writes = []
    monkeypatch.setattr(MODULE.base, "load_json", lambda path: data)
    monkeypatch.setattr(MODULE.base, "validate_config", lambda payload: None)
    monkeypatch.setattr(MODULE.base, "write_json_atomic", lambda path, payload: writes.append(dict(payload)))
    MODULE.run("migrate")
    assert data["status"] == "pending_open"
    assert data["cash_pln"] == 10000.0
    assert len(writes) == 1
