#!/usr/bin/env python3
"""Bridge production Event Intelligence into the existing weekly lifecycle.

The bridge owns no trading rules. It produces the shared event snapshot and writes
only close-only material-event requests. daily_position_review.py remains the
execution authority for EUR/USD, S&P 500 futures and BTC weekly positions.
"""
from __future__ import annotations

import json
from datetime import datetime

import investment_event_intelligence as event
import investment_event_quality as quality


def main() -> int:
    now = datetime.now(event.UTC)
    snapshot, _, week_path, week = quality.build_snapshot(now)
    scores = {str(row.get("target_id") or ""): row for row in snapshot.get("targets") or []}
    requests = []
    if snapshot.get("events"):
        requests = event.apply_weekly_requests(now, week, scores)
    print(json.dumps({
        "status": snapshot.get("status"),
        "events": len(snapshot.get("events") or []),
        "quality_rejections": len(snapshot.get("quality_rejections") or []),
        "new_close_requests": len(requests),
        "week": str(week_path) if week_path else None,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
