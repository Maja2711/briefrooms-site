#!/usr/bin/env python3
"""Read-only proof before promoting private D1 to single canonical Daily ledger.

Never print/return private records, quotes, trade IDs, hashes or policy details.
A mismatch is a HARD STOP, not permission to reset or splice history.
"""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping


class MigrationBlocked(RuntimeError):
    pass


FILES=("eurusd_daily_spot.json","eurusd_daily_history.json",
       "eurusd_production_policy_state.json","eurusd_production_decisions.json")


def _read(directory: Path, name: str) -> dict:
    try:
        path=directory/name
        data=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data,dict):
            raise ValueError("schema")
        return data
    except Exception:
        raise MigrationBlocked("STATE_MISSING_OR_UNREADABLE") from None


def verify_same_ledger(public: Path, private: Path) -> str:
    """No retroactive cross-state reconciliation; require exact prior authority."""
    p={n:_read(public,n) for n in FILES}
    s={n:_read(private,n) for n in FILES}
    # JSON field order and whitespace do not confer different trading authority,
    # but changing any field/decision does, including manifests or price lineage.
    if p!=s:
        raise MigrationBlocked("LEDGER_STATE_CONFLICT")
    return "MIGRATION_READY_FOR_HUMAN_REVIEW"


def main() -> int:
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        print("CANONICAL_CUTOVER=BLOCKED_NOT_MAIN")
        return 2
    try:
        from saxo_private_daily_d1_runner import D1, load
        public=Path("data/investments")
        with tempfile.TemporaryDirectory(prefix="brs-private-readonly-audit-") as name:
            private=Path(name).resolve()
            generation,_digest=load(D1(),private)
            if generation <= 0:
                raise MigrationBlocked("PRIVATE_STATE_NOT_INITIALIZED")
            verify_same_ledger(public,private)
        print("CANONICAL_CUTOVER=MATCHED_REVIEW_REQUIRED")
        return 0
    except MigrationBlocked as exc:
        print("CANONICAL_CUTOVER=BLOCKED_"+str(exc))
    except Exception:
        print("CANONICAL_CUTOVER=BLOCKED_PRIVATE_READ_UNAVAILABLE")
    return 1


if __name__=="__main__":
    raise SystemExit(main())
