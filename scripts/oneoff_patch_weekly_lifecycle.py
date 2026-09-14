from __future__ import annotations

from pathlib import Path

LIFECYCLE = Path("scripts/investments_wes_lifecycle.py")
TEST = Path("tests/test_wes_lifecycle_expired_no_entry.py")

OLD_BLOCK = '''            side = str(item.get("direction") or "neutral")
            if side == "neutral":
                if now >= weekly_deadline and item.get("result") != "no_trade":
                    item.update(result="no_trade", result_value=0.0, result_percent=0.0, trade_status="no_trade")
                    changed = True
                if now >= weekly_deadline and v2.mark_exposure_closed(item):
                    changed = True
                continue
            entry = sf(item.get("entry_price"))
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
'''

NEW_BLOCK = '''            side = str(item.get("direction") or "neutral").lower()
            entry = sf(item.get("entry_price"))
            status = str(item.get("trade_status") or "").strip().lower().replace(" ", "_")

            # The frozen weekly forecast may remain neutral while WES later
            # authorizes a directional entry. If that authorization never
            # executes, the base NO TRADE result and the execution outcome are
            # different facts and must not be conflated.
            execution_side = side if side in {"long", "short"} else None
            if execution_side is None and status in {"planned", "pending"}:
                pending = item.get("pending_entry_decision")
                pending_decision = pending.get("decision") if isinstance(pending, dict) else None
                pending_side = str((pending_decision or {}).get("direction") or "").lower() if isinstance(pending_decision, dict) else ""
                auth = _authorization(item)
                candidate = auth.get("candidate") if isinstance(auth.get("candidate"), dict) else {}
                authorized_side = str(candidate.get("direction") or "").lower()
                if pending_side in {"long", "short"}:
                    execution_side = pending_side
                elif authorized_side in {"long", "short"}:
                    execution_side = authorized_side

            if entry is None and execution_side in {"long", "short"}:
                # A directional decision/authorization that never obtained a
                # valid execution becomes terminal at its governed deadline.
                # Never fabricate an entry, exit or P/L.
                item["trade_status"] = "expired_no_entry"
                item["entry_quality_status"] = "expired_without_execution"
                item["entry_expiry_reason"] = "governed_deadline_elapsed_without_entry"
                item["entry_expired_at"] = deadline.isoformat(timespec="seconds")
                item["execution_outcome"] = "no_entry"
                item["expired_entry_direction"] = execution_side
                if v2.mark_exposure_closed(item):
                    changed = True
                changed = True
                continue

            if side == "neutral":
                if now >= weekly_deadline and item.get("result") != "no_trade":
                    item.update(result="no_trade", result_value=0.0, result_percent=0.0)
                    changed = True
                # A neutral observation must also never retain an open/planned
                # lifecycle status after the weekly deadline.
                if now >= weekly_deadline and status in {"planned", "pending"}:
                    item["trade_status"] = "no_trade"
                    changed = True
                if now >= weekly_deadline and v2.mark_exposure_closed(item):
                    changed = True
                continue

            if entry is None:
                continue
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


def run_case(tmp_path, monkeypatch, row):
    week = {
        "week_id": "2026-W37",
        "market_window": {"exit_target_local": "2026-09-11T22:00:00+02:00"},
        "instruments": [row],
    }
    path = tmp_path / "2026-W37.json"
    path.write_text(json.dumps(week), encoding="utf-8")
    monkeypatch.setattr(lifecycle, "WEEKLY_DIR", tmp_path)
    changed = lifecycle.settle_due_positions(datetime.fromisoformat("2026-09-14T13:00:00+02:00"))
    return changed, json.loads(path.read_text(encoding="utf-8"))["instruments"][0]


def test_directional_decision_without_entry_expires_after_deadline(tmp_path, monkeypatch):
    changed, row = run_case(tmp_path, monkeypatch, {
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
    })

    assert changed is True
    assert row["trade_status"] == "expired_no_entry"
    assert row["execution_outcome"] == "no_entry"
    assert row["expired_entry_direction"] == "short"
    assert row["pending_entry_decision"] is None
    assert row["continuous_exposure_active"] is False
    assert row["continuous_exposure_status"] == "closed"
    assert row["entry_price"] is None
    assert row["exit_price"] is None
    assert row.get("result") != "no_trade"


def test_neutral_forecast_with_expired_wes_authorization_is_terminalized(tmp_path, monkeypatch):
    changed, row = run_case(tmp_path, monkeypatch, {
        "instrument_id": "sp500_futures",
        "symbol": "ES=F",
        "direction": "neutral",
        "entry_price": None,
        "exit_price": None,
        "trade_status": "planned",
        "result": "no_trade",
        "result_value": 0.0,
        "result_percent": 0.0,
        "pending_entry_decision": None,
        "wes_entry_authorization": {
            "authorized_at": "2026-09-08T06:39:37+02:00",
            "expires_at": "2026-09-08T06:59:37+02:00",
            "candidate": {"direction": "long", "entry_class": "midweek_trigger"},
        },
        "continuous_exposure_active": True,
        "continuous_exposure_status": "open",
        "next_entry_status": "pending",
    })

    assert changed is True
    assert row["trade_status"] == "expired_no_entry"
    assert row["execution_outcome"] == "no_entry"
    assert row["expired_entry_direction"] == "long"
    # Preserve the base model's NO TRADE while separately recording that the
    # later WES authorization was never executed.
    assert row["result"] == "no_trade"
    assert row["result_value"] == 0.0
    assert row["result_percent"] == 0.0
    assert row["entry_price"] is None
    assert row["exit_price"] is None
    assert row["continuous_exposure_active"] is False
    assert row["continuous_exposure_status"] == "closed"


def test_plain_neutral_no_trade_does_not_become_fake_expired_entry(tmp_path, monkeypatch):
    changed, row = run_case(tmp_path, monkeypatch, {
        "instrument_id": "sp500_futures",
        "symbol": "ES=F",
        "direction": "neutral",
        "entry_price": None,
        "exit_price": None,
        "trade_status": "planned",
        "result": "no_trade",
        "result_value": 0.0,
        "result_percent": 0.0,
        "pending_entry_decision": None,
        "continuous_exposure_active": False,
        "continuous_exposure_status": "closed",
    })

    assert changed is True
    assert row["trade_status"] == "no_trade"
    assert row.get("execution_outcome") is None
    assert row.get("expired_entry_direction") is None
    assert row["entry_price"] is None
    assert row["exit_price"] is None
'''


def main() -> None:
    text = LIFECYCLE.read_text(encoding="utf-8")
    if OLD_BLOCK not in text:
        if 'item["expired_entry_direction"] = execution_side' in text:
            print("Neutral/WES expiry lifecycle patch already present.")
        else:
            raise SystemExit("Expected lifecycle block not found; refusing unsafe patch")
    else:
        LIFECYCLE.write_text(text.replace(OLD_BLOCK, NEW_BLOCK, 1), encoding="utf-8", newline="\n")
        print("Patched neutral/WES no-entry lifecycle.")

    TEST.write_text(TEST_CONTENT, encoding="utf-8", newline="\n")
    print("Wrote production-shape lifecycle regression tests.")


if __name__ == "__main__":
    main()
