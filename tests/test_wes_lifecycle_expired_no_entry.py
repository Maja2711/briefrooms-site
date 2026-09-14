from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import investments_wes_lifecycle as lifecycle


def test_directional_decision_without_entry_expires_after_deadline(tmp_path, monkeypatch):
    week = {
        "week_id": "2026-W37",
        "market_window": {"exit_target_local": "2026-09-11T22:00:00+02:00"},
        "instruments": [{
            "instrument_id": "sp500_futures",
            "symbol": "ES=F",
            "direction": "short",
            "entry_price": None,
            "exit_price": None,
            "trade_status": "planned",
            "pending_entry_decision": {"decision": {"direction": "short"}},
            "continuous_exposure_active": True,
            "continuous_exposure_status": "open",
            "next_entry_status": "pending",
        }],
    }
    path = tmp_path / "2026-W37.json"
    path.write_text(json.dumps(week), encoding="utf-8")
    monkeypatch.setattr(lifecycle, "WEEKLY_DIR", tmp_path)

    changed = lifecycle.settle_due_positions(datetime.fromisoformat("2026-09-14T13:00:00+02:00"))
    saved = json.loads(path.read_text(encoding="utf-8"))
    row = saved["instruments"][0]

    assert changed is True
    assert row["trade_status"] == "expired_no_entry"
    assert row["execution_outcome"] == "no_entry"
    assert row["pending_entry_decision"] is None
    assert row["continuous_exposure_active"] is False
    assert row["continuous_exposure_status"] == "closed"
    assert row["entry_price"] is None
    assert row["exit_price"] is None
    assert row.get("result") != "no_trade"
