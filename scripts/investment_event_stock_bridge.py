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

import investment_event_engine_profiles as profiles
import investment_event_intelligence as event
import investment_event_quality as quality


def _enrich_target_names(targets: list[dict], state: dict) -> None:
    """Add company names for boundary-safe direct-company event classification."""
    by_key: dict[tuple[str, str], str] = {}
    for market, row in (state.get("markets") or {}).items():
        for position in (row or {}).get("open_positions") or []:
            symbol = str(position.get("symbol") or "").upper()
            name = str(position.get("name") or "").strip()
            if symbol and name:
                by_key[(str(market).upper(), symbol)] = name
    for target in targets:
        market = str(target.get("market") or "").upper()
        symbol = str(target.get("symbol") or "").upper()
        if not target.get("name") and (market, symbol) in by_key:
            target["name"] = by_key[(market, symbol)]


def _persist_profile_audit(state: dict, scores: dict[str, dict]) -> None:
    for market, row in (state.get("markets") or {}).items():
        for position in (row or {}).get("open_positions") or []:
            target_id = f"stock:{str(market).upper()}:{position.get('symbol')}"
            score = scores.get(target_id) or {}
            overlay = position.get("event_intelligence")
            if not isinstance(overlay, dict) or not score:
                continue
            overlay.update({
                "engine_profile": score.get("engine_profile"),
                "engine_role": score.get("engine_role"),
                "profile_base_weight": score.get("profile_base_weight"),
                "raw_normalized_impact": score.get("raw_normalized_impact"),
                "dominant_event_scope": score.get("dominant_event_scope"),
                "dominant_engine_weight": score.get("dominant_engine_weight"),
                "thresholds_applied": score.get("thresholds_applied"),
            })
    for market, row in (state.get("markets") or {}).items():
        overlay = (row or {}).get("last_candidate_event_overlay")
        if not isinstance(overlay, dict):
            continue
        key = str((row or {}).get("last_candidate_key") or "")
        symbol = key.rsplit(":", 1)[-1].upper() if key else ""
        score = scores.get(f"candidate:{str(market).upper()}:{symbol}") or {}
        if score:
            overlay.update({
                "engine_profile": score.get("engine_profile"),
                "engine_role": score.get("engine_role"),
                "profile_base_weight": score.get("profile_base_weight"),
                "raw_normalized_impact": score.get("raw_normalized_impact"),
                "dominant_event_scope": score.get("dominant_event_scope"),
                "dominant_engine_weight": score.get("dominant_engine_weight"),
                "thresholds_applied": score.get("thresholds_applied"),
            })


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--candidates-only", action="store_true")
    mode.add_argument("--positions-only", action="store_true")
    args = parser.parse_args()

    now = datetime.now(event.UTC)
    events, errors, rejected = quality.collect(now)
    targets, state, _, _ = event.build_targets(now)
    _enrich_target_names(targets, state)
    scores = {
        str(target["target_id"]): profiles.score_target(target, events, engine_profile=profiles.STOCK_TRADING)
        for target in targets
    }
    before = json.dumps(state, ensure_ascii=False, sort_keys=True)
    actions: list[dict] = []

    if events and not args.positions_only:
        actions.extend(event.apply_candidate_blocks(state, targets, scores, now))
    if events and not args.candidates_only:
        state, position_actions = event.apply_stock_closes(state, scores, now)
        actions.extend(position_actions)
    _persist_profile_audit(state, scores)

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
        "engine_profile": profiles.STOCK_TRADING,
        "source_errors": errors,
        "actions": actions,
        "state_changed": after != before,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
