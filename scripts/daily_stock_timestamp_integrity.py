#!/usr/bin/env python3
"""Preserve explicit entry/exit timestamps for GPW and US paper trades.

Never fabricates timestamps. US entry time may be recovered only from the
selection market snapshot when its observed price is inside the published entry
zone. Exit time is copied only from an explicit execution/closure timestamp.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INV = ROOT / "data/investments"
US_DIR = INV / "us_daily_stock_history"
GPW_DIR = INV / "gpw_daily_pick_history"
US_INDEX = US_DIR / "index.json"
GPW_INDEX = INV / "gpw_daily_pick_history_index.json"


def load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def atomic(path: Path, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        tmp = handle.name
    Path(tmp).replace(path)


def explicit_us_activation(payload: dict[str, Any]) -> str | None:
    selection = payload.get("selection") or {}
    snapshot = selection.get("market_snapshot") or {}
    observed_at = snapshot.get("observed_at")
    zone = selection.get("entry_zone") or []
    last = snapshot.get("last")
    try:
        low, high = float(zone[0]), float(zone[1])
        price = float(last)
    except (TypeError, ValueError, IndexError):
        return None
    if not observed_at or not (low <= price <= high):
        return None
    return str(observed_at)


def enrich_payload(payload: dict[str, Any], market: str) -> bool:
    if str(payload.get("decision") or "") not in ({"TRADE"} if market == "us" else {"TRANSAKCJA"}):
        return False
    outcome = dict(payload.get("outcome") or {})
    changed = False

    if market == "us" and not outcome.get("activated_at"):
        observed_at = explicit_us_activation(payload)
        if observed_at and (outcome.get("activated") is True or not outcome):
            selection = payload.get("selection") or {}
            snapshot = selection.get("market_snapshot") or {}
            outcome.setdefault("status", "PENDING")
            outcome["activated"] = True
            outcome["activated_at"] = observed_at
            if outcome.get("entry_price") is None and snapshot.get("last") is not None:
                outcome["entry_price"] = snapshot.get("last")
            changed = True

    if outcome.get("activated") is True and not outcome.get("exit_at"):
        explicit_exit = outcome.get("exit_bar_at") if market == "gpw" else outcome.get("closed_at")
        if explicit_exit:
            outcome["exit_at"] = explicit_exit
            changed = True

    if changed:
        payload["outcome"] = outcome
    return changed


def enrich_history(directory: Path, market: str) -> tuple[int, dict[tuple[str, str], dict[str, Any]]]:
    changed = 0
    outcomes: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(directory.glob("????-??-??.json")):
        payload = load(path)
        if not payload:
            continue
        if enrich_payload(payload, market):
            atomic(path, payload)
            changed += 1
        selection = payload.get("selection") or {}
        symbol = str(selection.get("ticker") or selection.get("symbol") or "").upper()
        if symbol:
            outcomes[(str(payload.get("date") or ""), symbol)] = dict(payload.get("outcome") or {})
    return changed, outcomes


def sync_index(path: Path, outcomes: dict[tuple[str, str], dict[str, Any]]) -> bool:
    payload = load(path)
    if not payload or not isinstance(payload.get("trades"), list):
        return False
    changed = False
    for row in payload["trades"]:
        key = (str(row.get("date") or ""), str(row.get("ticker") or row.get("symbol") or "").upper())
        source = outcomes.get(key)
        if source is not None and row.get("outcome") != source:
            row["outcome"] = source
            changed = True
    if changed:
        atomic(path, payload)
    return changed


def run(market: str) -> dict[str, Any]:
    result: dict[str, Any] = {"market": market, "history_files_changed": 0, "index_changed": False}
    if market in {"us", "all"}:
        count, outcomes = enrich_history(US_DIR, "us")
        result["history_files_changed"] += count
        result["index_changed"] = sync_index(US_INDEX, outcomes) or result["index_changed"]
    if market in {"gpw", "all"}:
        count, outcomes = enrich_history(GPW_DIR, "gpw")
        result["history_files_changed"] += count
        result["index_changed"] = sync_index(GPW_INDEX, outcomes) or result["index_changed"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=("us", "gpw", "all"), default="all")
    args = parser.parse_args()
    print(json.dumps(run(args.market), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
