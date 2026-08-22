#!/usr/bin/env python3
"""BriefRooms Learning Ledger / Outcome Loop v1.

Research-shadow only. Records immutable prospective facts needed for later
learning. It deliberately has zero authority to tune beliefs, policies, weights,
causal edges, rankings, sizing or execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

SCHEMA_VERSION = "briefrooms-learning-ledger-v1"
EVENT_TYPES = {"forecast", "decision", "outcome", "verification", "learning_observation"}
DEFAULT_LEDGER = Path("data/research/learning_ledger.jsonl")


def iso_z_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def safety_controls() -> Dict[str, bool]:
    return {
        "automatic_tuning": False,
        "belief_writeback": False,
        "evidence_weight_writeback": False,
        "causal_edge_writeback": False,
        "engine_policy_writeback": False,
        "ranking_writeback": False,
        "sizing_writeback": False,
        "trade_execution": False,
        "automatic_promotion": False,
    }


def _assert_safety() -> None:
    bad = [k for k, v in safety_controls().items() if v is not False]
    if bad:
        raise RuntimeError("learning-ledger zero-authority invariant violated: " + ",".join(bad))


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid ledger JSON at line {line_no}") from exc
        rows.append(row)
    return rows


def verify_chain(path: Path) -> Dict[str, Any]:
    rows = read_events(path)
    previous: Optional[str] = None
    seen: set[str] = set()
    for index, row in enumerate(rows):
        event_id = str(row.get("event_id") or "")
        if not event_id or event_id in seen:
            return {"ok": False, "count": len(rows), "error": f"invalid_or_duplicate_event_id:{index}"}
        seen.add(event_id)
        if row.get("previous_hash") != previous:
            return {"ok": False, "count": len(rows), "error": f"broken_previous_hash:{index}"}
        stored_hash = row.get("event_hash")
        body = dict(row)
        body.pop("event_hash", None)
        if stored_hash != _sha(body):
            return {"ok": False, "count": len(rows), "error": f"hash_mismatch:{index}"}
        previous = str(stored_hash)
    return {"ok": True, "count": len(rows), "head_hash": previous}


def append_event(
    path: Path,
    *,
    event_type: str,
    occurred_at: str,
    subject_id: str,
    payload: Mapping[str, Any],
    source_ref: str = "",
) -> Dict[str, Any]:
    _assert_safety()
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported event_type: {event_type}")
    if not subject_id.strip():
        raise ValueError("subject_id is required")
    # Timestamp must be parseable before persistence.
    datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))

    status = verify_chain(path)
    if not status["ok"]:
        raise RuntimeError("refusing append to invalid learning ledger: " + str(status["error"]))
    previous_hash = status.get("head_hash")
    identity = {
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "subject_id": subject_id,
        "source_ref": source_ref,
        "payload": dict(payload),
    }
    event_id = "learn-" + _sha(identity)[:24]
    for row in read_events(path):
        if row["event_id"] == event_id:
            if all(row.get(k) == v for k, v in identity.items()):
                return row
            raise RuntimeError("event_id collision")

    row = {
        **identity,
        "event_id": event_id,
        "recorded_at": iso_z_now(),
        "previous_hash": previous_hash,
    }
    row["event_hash"] = _sha(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_APPEND keeps each encoded event append-only at the file level.
    encoded = (_canonical(row) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_chain(args.ledger), indent=2, sort_keys=True))
        return 0 if verify_chain(args.ledger)["ok"] else 2
    if not args.input:
        parser.error("--input is required unless --verify is used")
    data = json.loads(args.input.read_text(encoding="utf-8"))
    row = append_event(
        args.ledger,
        event_type=str(data["event_type"]),
        occurred_at=str(data["occurred_at"]),
        subject_id=str(data["subject_id"]),
        source_ref=str(data.get("source_ref") or ""),
        payload=dict(data.get("payload") or {}),
    )
    print(json.dumps(row, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
