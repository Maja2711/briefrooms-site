#!/usr/bin/env python3
"""Dispatch newly created trading notification events to the configured Web Push backend."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data" / "notifications" / "trading-notification-config.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True)
    args = parser.parse_args()

    config = load(CONFIG_PATH)
    bg = config.get("background_push") or {}
    if bg.get("enabled") is not True:
        print("BACKGROUND_PUSH_DISABLED")
        return 0

    base = str(bg.get("api_base") or "").rstrip("/")
    if not base.startswith("https://"):
        print("BACKGROUND_PUSH_API_NOT_CONFIGURED", file=sys.stderr)
        return 1

    events = load(Path(args.events))
    if not isinstance(events, list):
        print("INVALID_NEW_EVENTS_PAYLOAD", file=sys.stderr)
        return 1

    failures = 0
    for event in events:
        event_id = str((event or {}).get("event_id") or "")
        if not event_id:
            continue
        body = json.dumps({"event_id": event_id}).encode("utf-8")
        req = urllib.request.Request(
            base + "/trading-push-dispatch",
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "BriefRooms-GitHub-Actions/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                payload = response.read().decode("utf-8", "replace")
                print(f"PUSH_DISPATCH {event_id} {response.status} {payload}")
        except Exception as exc:
            failures += 1
            print(f"PUSH_DISPATCH_FAILED {event_id} {exc}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
