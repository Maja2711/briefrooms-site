from __future__ import annotations

from pathlib import Path

LIFECYCLE = Path("scripts/investments_wes_lifecycle.py")
TEST = Path("tests/test_wes_lifecycle_expired_no_entry.py")
OWNERSHIP_TEST = Path("tests/test_automation_workflow_ownership.py")

OLD = '''            entry = sf(item.get("entry_price"))
            if entry is None:
                continue
            if sf(item.get("exit_price")) is not None:
'''

NEW = '''            entry = sf(item.get("entry_price"))
            if entry is None:
                # A directional decision that never obtained a valid execution
                # must become terminal once its governed entry/holding window is
                # over. Do not fabricate an entry, exit or P/L: record the
                # execution outcome separately from a model-level NO TRADE.
                if side in {"long", "short"}:
                    item["trade_status"] = "expired_no_entry"
                    item["entry_quality_status"] = "expired_without_execution"
                    item["entry_expiry_reason"] = "governed_deadline_elapsed_without_entry"
                    item["entry_expired_at"] = deadline.isoformat(timespec="seconds")
                    item["execution_outcome"] = "no_entry"
                    if v2.mark_exposure_closed(item):
                        changed = True
                    changed = True
                continue
            if sf(item.get("exit_price")) is not None:
'''

TEST_CONTENT = '''from __future__ import annotations

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
'''


def main() -> None:
    text = LIFECYCLE.read_text(encoding="utf-8")
    if OLD not in text:
        if 'item["trade_status"] = "expired_no_entry"' in text:
            print("Lifecycle patch already present.")
        else:
            raise SystemExit("Expected lifecycle block not found; refusing unsafe patch")
    else:
        LIFECYCLE.write_text(text.replace(OLD, NEW, 1), encoding="utf-8", newline="\n")
        print("Patched lifecycle.")

    TEST.write_text(TEST_CONTENT, encoding="utf-8", newline="\n")
    print("Wrote regression test.")

    ownership = OWNERSHIP_TEST.read_text(encoding="utf-8")
    old_step = 'Settle due weekly positions before downstream work'
    new_step = 'Settle due weekly or rolling WES positions before downstream work'
    if old_step in ownership:
        OWNERSHIP_TEST.write_text(ownership.replace(old_step, new_step, 1), encoding="utf-8", newline="\n")
        print("Aligned ownership test with canonical exposure step.")
    elif new_step in ownership:
        print("Ownership test already aligned.")
    else:
        raise SystemExit("Expected ownership assertion not found; refusing unsafe patch")


if __name__ == "__main__":
    main()
