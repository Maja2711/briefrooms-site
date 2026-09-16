#!/usr/bin/env python3
"""Bridge production Event Intelligence into canonical GPW/US Stock Trading.

The bridge does not create entries. It may veto a current candidate before canonical
admission and may close an already-open position through stock_trading_portfolio's
existing close_position lifecycle. All other portfolio governance stays unchanged.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime

import investment_event_intelligence as event
import investment_event_quality as quality


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--candidates-only", action="store_true")
    mode.add_argument("--positions-only", action="store_true")
    args = parser.parse_args()

    now = datetime.now(event.UTC)
    events, errors, rejected = quality.collect(now)
    targets, state, _, _ = event.build_targets(now)
    scores = {str(target["target_id"]): event.score_target(target, events) for target in targets}
    before = json.dumps(state, ensure_ascii=False, sort_keys=True)
    actions: list[dict] = []

    if events and not args.positions_only:
        actions.extend(event.apply_candidate_blocks(state, targets, scores, now))
    if events and not args.candidates_only:
        state, position_actions = event.apply_stock_closes(state, scores, now)
        actions.extend(position_actions)

    after = json.dumps(state, ensure_ascii=False, sort_keys=True)
    if after != before:
        state["updated_at"] = now.isoformat(timespec="seconds")
        event._write(event.STOCK_STATE_PATH, state)
    if actions:
        event._append_audit({"recorded_at": event.iso_z(now), **row} for row in actions)

    print(json.dumps({
        "status": "healthy" if events else "degraded_no_fresh_classified_events",
        "events": len(events),
        "quality_rejections": len(rejected),
        "source_errors": errors,
        "actions": actions,
        "state_changed": after != before,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
