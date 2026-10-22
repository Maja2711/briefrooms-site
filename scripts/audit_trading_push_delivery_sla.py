#!/usr/bin/env python3
"""Read-only Web Push delivery latency audit for canonical BriefRooms trading events.

Never replays OPEN/CLOSE, reads subscriptions, or changes trading state.
The push service's acceptance is not proof of the phone displaying the alert.
"""
from __future__ import annotations

import json
import sys
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

EVENTS = Path("data/notifications/trading-events.json")
PUSH_API = "https://briefrooms-trading-push.szczerbinski0577.workers.dev"
WINDOW = timedelta(hours=4)
SLA = timedelta(minutes=2)


def parse_time(value: object) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def audit_one(event: dict, record: dict, now: datetime) -> list[str]:
    eid = str(event.get("event_id") or "")
    occurred = parse_time(event.get("closed_at") if event.get("event_type") == "CLOSE"
                          else event.get("opened_at"))
    if occurred is None:
        return [f"EVENT_CLOCK_MISSING event_id={eid}"]
    if not record.get("ok"):
        return [f"PUSH_EVENT_MISSING event_id={eid}"]
    first = parse_time(record.get("first_provider_accepted_at"))
    ack = parse_time(record.get("first_device_ack_at"))
    recipients = int(record.get("recipients_initial_count") or 0)
    notes = []
    if recipients == 0:
        notes.append(f"PUSH_NO_ELIGIBLE_RECIPIENTS event_id={eid}")
    elif first is None:
        notes.append(f"PUSH_NOT_ACCEPTED event_id={eid} age_s={int((now-occurred).total_seconds())}")
    elif first - occurred > SLA:
        notes.append(f"PUSH_PROVIDER_LATE event_id={eid} seconds={int((first-occurred).total_seconds())}")
    if first is not None and ack is not None and ack - first > SLA:
        notes.append(f"PUSH_DEVICE_ACK_LATE event_id={eid} seconds={int((ack-first).total_seconds())}")
    elif first is not None and ack is None and now - first > SLA:
        notes.append(f"PUSH_DEVICE_ACK_UNKNOWN event_id={eid} seconds={int((now-first).total_seconds())}")
    return notes


def main() -> int:
    if not EVENTS.is_file():
        print("PUSH_SLA_AUDIT events file not available")
        return 0
    source = json.loads(EVENTS.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    rows = []
    for event in source.get("events", []):
        if event.get("event_type") not in ("OPEN", "CLOSE"):
            continue
        occurred = parse_time(event.get("closed_at") if event.get("event_type") == "CLOSE"
                              else event.get("opened_at"))
        if occurred and timedelta(0) <= now - occurred <= WINDOW:
            rows.append(event)
    warnings = 0
    for event in rows[-12:]:
        eid = str(event.get("event_id", ""))
        if not eid or not all(x in "0123456789abcdef" for x in eid):
            continue
        url = PUSH_API + "/event-delivery-summary?" + urlencode({"event_id": eid})
        # Use the same curl transport as the deployed Worker health checks.
        # Do not interpret an HTTP failure as a successful SLA check.
        response = subprocess.run(
            ["curl", "--silent", "--show-error", "--connect-timeout", "5",
             "--max-time", "15", "--write-out", "\\n%{http_code}", url],
            capture_output=True, text=True, check=False, timeout=20,
        )
        payload, sep, status = response.stdout.rpartition("\n")
        if response.returncode != 0 or not sep:
            warnings += 1
            print(f"::warning::PUSH_SLA_DIAGNOSTIC_UNAVAILABLE event_id={eid} transport=curl")
            continue
        if status == "404":
            record = {"ok": False}
        elif status == "200":
            try:
                record = json.loads(payload)
            except (ValueError, TypeError):
                warnings += 1
                print(f"::warning::PUSH_SLA_DIAGNOSTIC_INVALID_JSON event_id={eid}")
                continue
        else:
            warnings += 1
            print(f"::warning::PUSH_SLA_DIAGNOSTIC_HTTP_{status} event_id={eid}")
            continue
        for note in audit_one(event, record, now):
            warnings += 1
            print("::warning::" + note)
    print(f"PUSH_SLA_AUDIT checked={len(rows[-12:])} warnings={warnings} no_replay=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
