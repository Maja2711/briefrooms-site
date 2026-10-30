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


def _position_category(payload: Mapping) -> str:
    if payload.get("status") == "OPEN":
        position=(payload.get("metadata") or {}).get("position")
        if not isinstance(position,dict) or position.get("status") != "OPEN":
            raise MigrationBlocked("POSITION_STATE_INVALID")
        return "OPEN"
    if (payload.get("metadata") or {}).get("position") not in (None,{}):
        raise MigrationBlocked("POSITION_STATE_INVALID")
    return "FLAT"


def _trade_index(history: Mapping) -> dict:
    rows=history.get("trades")
    if not isinstance(rows,list):
        raise MigrationBlocked("HISTORY_SCHEMA_INVALID")
    result={}
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get("trade_id"),str):
            raise MigrationBlocked("HISTORY_SCHEMA_INVALID")
        key=row["trade_id"]
        if not key or key in result:
            raise MigrationBlocked("HISTORY_DUPLICATE_ID")
        result[key]=row
    return result


def read_only_divergence_categories(public: Path, private: Path) -> tuple[str,str,str]:
    """Return ONLY fixed enums. No data, IDs, hashes, quantities or prices."""
    pub=_read(public,FILES[0]); priv=_read(private,FILES[0])
    a,b=_position_category(pub),_position_category(priv)
    if a=="OPEN" and b=="OPEN":
        x=(pub.get("metadata") or {}).get("position")
        y=(priv.get("metadata") or {}).get("position")
        position="BOTH_OPEN_SAME" if x==y else "BOTH_OPEN_DIFFERENT"
    elif a=="OPEN": position="PUBLIC_OPEN_ONLY"
    elif b=="OPEN": position="PRIVATE_OPEN_ONLY"
    else: position="BOTH_FLAT"
    a,b=_trade_index(_read(public,FILES[1])),_trade_index(_read(private,FILES[1]))
    overlap=a.keys() & b.keys()
    if any(a[k]!=b[k] for k in overlap):
        history="SHARED_TRADE_CONFLICT"
    elif (a.keys()-b.keys()) and (b.keys()-a.keys()):
        history="BRANCHED_HISTORY"
    elif a.keys()-b.keys():
        history="PUBLIC_ONLY_TRADES"
    elif b.keys()-a.keys():
        history="PRIVATE_ONLY_TRADES"
    else:
        history="MATCHED_TRADE_IDS_AND_ROWS"
    policy="MATCHED" if all(
        _read(public,name)==_read(private,name) for name in FILES[2:]) else "DIVERGENT"
    return position,history,policy


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
            position,history,policy=read_only_divergence_categories(public,private)
            # Logs contain categorical non-price statuses only. No detailed
            # Saxo price/trade data, IDs, timestamps, counts or hashes escape.
            print("CANONICAL_POSITION_ALIGNMENT="+position)
            print("CANONICAL_HISTORY_ALIGNMENT="+history)
            print("CANONICAL_POLICY_ALIGNMENT="+policy)
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
