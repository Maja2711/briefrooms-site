#!/usr/bin/env python3
"""Bridge production Event Intelligence into the existing weekly lifecycle.

The bridge owns no trading rules. It produces the shared event snapshot and writes
only close-only material-event requests. daily_position_review.py remains the
execution authority for EUR/USD, S&P 500 futures and BTC weekly positions.
"""
from __future__ import annotations

import json
from datetime import datetime

import investment_event_engine_profiles as profiles
import investment_event_intelligence as event
import investment_event_quality as quality


def _apply_position_direction(scores: dict[str, dict], week: dict) -> None:
    """Translate signed market impact into LONG/SHORT position risk."""
    for instrument_id in ("eurusd", "sp500_futures", "btcusd"):
        score = scores.get(instrument_id)
        item = event._week_position(week, instrument_id)
        if not score or not item:
            continue
        side = str(item.get("direction") or "").upper()
        if side not in {"LONG", "SHORT"}:
            continue
        decision = profiles.directional_decision(score, side)
        score["market_impact_overlay"] = score.get("decision_overlay")
        score["position_direction"] = side
        score["decision_overlay"] = decision


def main() -> int:
    now = datetime.now(event.UTC)
    snapshot, _, week_path, week = quality.build_snapshot(now)
    events = list(snapshot.get("events") or [])
    targets, _, _, _ = event.build_targets(now)
    scores = {
        str(target["target_id"]): profiles.score_target(target, events, engine_profile=profiles.WEEKLY)
        for target in targets
    }
    _apply_position_direction(scores, week)
    snapshot["targets"] = list(scores.values())
    snapshot["thresholds"] = profiles.thresholds(profiles.WEEKLY)
    snapshot["engine_weighting"] = profiles.public_profiles()
    event._write(event.OUTPUT_PATH, snapshot)

    requests = []
    if events:
        requests = event.apply_weekly_requests(now, week, scores)
    print(json.dumps({
        "status": snapshot.get("status"),
        "events": len(events),
        "quality_rejections": len(snapshot.get("quality_rejections") or []),
        "engine_profile": profiles.WEEKLY,
        "new_close_requests": len(requests),
        "week": str(week_path) if week_path else None,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
