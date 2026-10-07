#!/usr/bin/env python3
"""Seal only currently pending pre-ledger WES decisions into the immutable ledger.

No historical/executed decision is reconstructed. The economic pending payload is
hashed before and after sealing and must remain identical.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import wes_decision_ledger as ledger

ROOT = Path(__file__).resolve().parents[1]
WEEKLY = ROOT / "data" / "investments" / "weekly"
TZ = ZoneInfo("Europe/Warsaw")


def iso_week_id(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def seal_current_pending(*, now: datetime | None = None, ledger_path: Path | None = None) -> dict:
    now = now or datetime.now(TZ)
    week_id = iso_week_id(now.astimezone(TZ))
    week_path = WEEKLY / f"{week_id}.json"
    result = {
        "schema_version": "briefrooms-wes-pending-migration-v1",
        "week_id": week_id,
        "status": "NO_CURRENT_WEEK",
        "sealed": [],
        "unchanged_payloads": True,
    }
    if not week_path.exists():
        return result

    week = read_json(week_path)
    target_ledger = ledger_path or ledger.LEDGER_PATH
    ledger.verify_ledger(target_ledger)

    changed = False
    for item in week.get("instruments") or []:
        if not isinstance(item, dict):
            continue
        pending = item.get("pending_entry_decision")
        if not isinstance(pending, dict) or not pending.get("decided_at"):
            continue

        instrument_id = str(item.get("instrument_id") or "").strip()
        if not instrument_id:
            raise ledger.WesDecisionLedgerError("current pending WES decision has no instrument_id")

        if pending.get("decision_id") or pending.get("payload_hash"):
            ledger.assert_pending_integrity(pending)
            continue

        before_payload = ledger.payload_from_pending(pending)
        before_hash = ledger.sha256(before_payload)
        sealed = ledger.append_frozen_decision(
            before_payload,
            week_id=week_id,
            instrument_id=instrument_id,
            decision_kind="LEGACY_PENDING_SEAL",
            path=target_ledger,
        )
        after_payload = ledger.payload_from_pending(sealed)
        after_hash = ledger.sha256(after_payload)
        if before_payload != after_payload or before_hash != after_hash:
            raise ledger.WesDecisionLedgerError(
                f"legacy pending migration changed economic payload for {instrument_id}"
            )

        item["pending_entry_decision"] = sealed
        item["current_decision_id"] = sealed["decision_id"]
        item["current_decision_payload_hash"] = sealed["payload_hash"]
        item["decision_ledger_schema_version"] = sealed["ledger_schema_version"]
        result["sealed"].append({
            "instrument_id": instrument_id,
            "decision_id": sealed["decision_id"],
            "payload_hash": sealed["payload_hash"],
            "target_price": (sealed.get("entry_price_plan") or {}).get("target_price"),
            "economic_payload_hash_before": before_hash,
            "economic_payload_hash_after": after_hash,
        })
        changed = True

    if changed:
        atomic_write(week_path, week)
        ledger.verify_ledger(target_ledger)
        result["status"] = "SEALED"
    else:
        result["status"] = "NOTHING_TO_SEAL"
    return result


def check_current_pending(*, now: datetime | None = None) -> dict:
    now = now or datetime.now(TZ)
    week_id = iso_week_id(now.astimezone(TZ))
    week_path = WEEKLY / f"{week_id}.json"
    ledger.verify_ledger()
    if not week_path.exists():
        return {"status": "NO_CURRENT_WEEK", "legacy_pending": []}
    week = read_json(week_path)
    legacy = []
    for item in week.get("instruments") or []:
        if not isinstance(item, dict):
            continue
        pending = item.get("pending_entry_decision")
        if not isinstance(pending, dict) or not pending.get("decided_at"):
            continue
        if pending.get("decision_id") or pending.get("payload_hash"):
            ledger.assert_pending_integrity(pending)
        else:
            legacy.append(str(item.get("instrument_id") or ""))
    if legacy:
        raise ledger.WesDecisionLedgerError("unsealed_current_wes_pending:" + ",".join(legacy))
    return {"status": "PASS", "legacy_pending": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = check_current_pending() if args.check else seal_current_pending()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
