#!/usr/bin/env python3
"""Prepare immutable AI Tournament portfolios for first-open execution.

This command is deliberately fail-closed. It never derives a portfolio from a
commitment hash. All configured participants must have a full revealed
submission whose canonical SHA-256 matches the pre-market commitment.

When ready, the script writes one pending target per participant with a created
session before the tournament start. The normal engine can then reconstruct the
first purchase from the official daily opening prices in the first completed
session snapshot. Daily rounds value the positions but cannot rebalance them.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ai_tournament_engine as engine  # noqa: E402

CONFIG_PATH = ROOT / "data" / "ai_tournament" / "config.json"
PUBLIC_PATH = ROOT / "data" / "ai_tournament" / "public.json"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    engine.atomic_write_json(path, payload)


def previous_weekday(value: str) -> str:
    current = date.fromisoformat(value) - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current.isoformat()


def public_participants(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "agent_id": agent["id"],
            "provider": agent["provider"],
            "model": engine.resolve_model(agent),
        }
        for agent in config.get("agents", [])
    ]


def publish_readiness(config: dict[str, Any], readiness: dict[str, Any], status: str) -> None:
    public = read_json(PUBLIC_PATH, {})
    if not isinstance(public, dict):
        public = {}
    tournament = dict(public.get("tournament") or {})
    tournament.update({
        "id": config["tournament_id"],
        "title_pl": config.get("title_pl"),
        "title_en": config.get("title_en"),
        "start_date": config["start_date"],
        "end_date": config["end_date"],
        "starting_capital_pln": config["starting_capital_pln"],
        "starting_capital_usd": config.get("starting_capital_usd"),
        "status": status,
        "ranking_rule": "cumulative_return_desc_then_drawdown_then_sharpe",
        "decision_time": "locked_before_first_open",
        "execution_time": "first_eligible_us_session_open",
    })
    public.update({
        "schema_version": engine.SCHEMA_VERSION,
        "engine_version": engine.ENGINE_VERSION,
        "generated_at": engine.utc_now(),
        "tournament": tournament,
        "participants": public_participants(config),
        "readiness": readiness,
        "disclaimer_pl": "Publiczny eksperyment portfeli modelowych. Nie jest rekomendacją ani poradą inwestycyjną.",
        "disclaimer_en": "A public model-portfolio experiment. It is not investment advice or a recommendation.",
    })
    if status.startswith("BLOCKED"):
        public["latest_session"] = None
        public["leaderboard"] = []
    write_json(PUBLIC_PATH, public)


def decision_already_recorded(ledger: Path, decision_id: str) -> bool:
    return any(
        row.get("event_type") == "DECISION" and row.get("decision_id") == decision_id
        for row in engine.read_jsonl(ledger)
    )


def bootstrap(config: dict[str, Any]) -> dict[str, Any]:
    readiness = engine.manual_submission_readiness(config)
    if not readiness.get("ready"):
        publish_readiness(config, readiness, "BLOCKED_MISSING_REVEALED_SUBMISSIONS")
        missing = [
            f"{row.get('agent_id')}: {row.get('error')}"
            for row in readiness.get("participants", [])
            if not row.get("ready")
        ]
        raise engine.ValidationError("; ".join(missing) or "manual submissions are incomplete")

    paths = engine.config_paths(config)
    created_session = previous_weekday(config["start_date"])
    lockset_hash = engine.sha256_text(engine.canonical_json({
        row["agent_id"]: row["submission_hash"]
        for row in readiness["participants"]
    }))
    synthetic_snapshot = {
        "session_date": created_session,
        "snapshot_hash": f"LOCKED_MANUAL_SUBMISSIONS:{lockset_hash}",
        "close_prices": {},
        "fx_close": 1.0,
    }

    prepared = []
    for agent in config.get("agents", []):
        state = engine.load_state(paths, agent, config)
        locked = engine.load_locked_submission(agent, config)
        existing_hash = (state.get("latest_decision") or {}).get("locked_submission_hash")
        if state.get("positions") and existing_hash != locked["submission_hash"]:
            raise engine.ValidationError(
                f"existing positions for {agent['id']} do not match the locked submission"
            )
        if existing_hash and existing_hash != locked["submission_hash"]:
            raise engine.ValidationError(f"stored decision hash mismatch for {agent['id']}")

        if not state.get("latest_decision"):
            decision = engine.collect_decision(agent, synthetic_snapshot, state, config)
            state["latest_decision"] = decision
            state["pending_target"] = {
                "decision_id": decision["decision_id"],
                "created_session": created_session,
                "target_weights": decision["target_weights"],
                "locked_submission_hash": decision["locked_submission_hash"],
            }
            state["status"] = "READY_FOR_FIRST_OPEN"
            ledger = engine.ledger_path(paths, agent["id"])
            if not decision_already_recorded(ledger, decision["decision_id"]):
                engine.append_ledger(ledger, {"event_type": "DECISION", **decision})
            engine.save_state(paths, state)
        elif not state.get("positions") and not state.get("pending_target"):
            decision = state["latest_decision"]
            state["pending_target"] = {
                "decision_id": decision["decision_id"],
                "created_session": created_session,
                "target_weights": decision["target_weights"],
                "locked_submission_hash": decision["locked_submission_hash"],
            }
            state["status"] = "READY_FOR_FIRST_OPEN"
            engine.save_state(paths, state)

        prepared.append({
            "agent_id": agent["id"],
            "state": state.get("status"),
            "submission_hash": locked["submission_hash"],
            "pending": bool(state.get("pending_target")),
            "positions": len(state.get("positions") or {}),
        })

    readiness["prepared_at"] = engine.utc_now()
    readiness["created_session"] = created_session
    readiness["lockset_hash"] = lockset_hash
    readiness["prepared"] = prepared
    publish_readiness(config, readiness, "READY_FOR_FIRST_EXECUTION")
    return readiness


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--readiness-only", action="store_true")
    args = parser.parse_args()
    config = read_json(args.config)
    if not isinstance(config, dict):
        raise SystemExit("AI Tournament config is missing")
    engine.validate_config(config)
    readiness = engine.manual_submission_readiness(config)
    if args.readiness_only:
        status = "READY_FOR_FIRST_EXECUTION" if readiness.get("ready") else "BLOCKED_MISSING_REVEALED_SUBMISSIONS"
        publish_readiness(config, readiness, status)
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
        return 0 if readiness.get("ready") else 2
    try:
        result = bootstrap(config)
    except Exception as exc:
        print(f"AI Tournament bootstrap blocked: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
