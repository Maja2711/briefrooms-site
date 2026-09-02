#!/usr/bin/env python3
"""Materialize one Experience Store from multiple independently verified ledgers."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from experience_store import SCHEMA_VERSION, build_experiences
    from learning_ledger import read_events, verify_chain
except ModuleNotFoundError:
    from scripts.experience_store import SCHEMA_VERSION, build_experiences
    from scripts.learning_ledger import read_events, verify_chain


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _validated_events(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], str] = {}
    for path in paths:
        chain = verify_chain(path)
        if not chain.get("ok"):
            raise RuntimeError(f"invalid source ledger {path}: {chain.get('error')}")
        rows = read_events(path)
        for row in rows:
            key = (str(row.get("event_type") or ""), str(row.get("subject_id") or ""))
            event_id = str(row.get("event_id") or "")
            previous = seen.get(key)
            if previous and previous != event_id:
                raise RuntimeError(f"cross-ledger event collision for {key[0]}:{key[1]}")
            seen[key] = event_id
            events.append(row)
        sources.append({
            "path": str(path),
            "event_count": len(rows),
            "head_hash": chain.get("head_hash"),
        })
    return events, sources


def materialize(ledgers: list[Path], store: Path, status_path: Path) -> dict[str, Any]:
    if not ledgers:
        raise ValueError("at least one source ledger is required")
    events, sources = _validated_events(ledgers)
    rows = build_experiences(events)
    _atomic_text(store, "".join(_canonical(row) + "\n" for row in rows))
    settled = sum(1 for row in rows if row.get("status") == "SETTLED")
    engines = sorted({str(row.get("engine") or "unknown") for row in rows})
    status = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "experience_count": len(rows),
        "settled_count": settled,
        "pending_count": len(rows) - settled,
        "engines": engines,
        "source_ledgers": sources,
        "source_event_count": len(events),
        "ledger_head_hash": sources[0].get("head_hash"),
        "zero_authority": True,
        "anti_lookahead": "every outcome must be strictly later than its frozen decision; each source ledger is independently hash-verified",
    }
    _atomic_json(status_path, status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical Experience Store from verified primary and shadow ledgers")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--additional-ledger", type=Path, action="append", default=[])
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    args = parser.parse_args()
    result = materialize([args.ledger, *args.additional_ledger], args.store, args.status)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
