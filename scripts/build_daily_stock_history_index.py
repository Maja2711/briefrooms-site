#!/usr/bin/env python3
"""Build one public history index for GPW and US Daily Stock.

The underlying market histories remain isolated. PR26 keeps raw US day files for
audit while the presentation layer collapses overlapping daily selections into
one canonical open position.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import us_daily_stock_position_lifecycle as us_lifecycle
except ModuleNotFoundError:
    import us_daily_stock_position_lifecycle as us_lifecycle

ROOT = Path(__file__).resolve().parents[1]
INVESTMENTS = ROOT / "data/investments"
GPW_DIR = INVESTMENTS / "gpw_daily_pick_history"
US_DIR = INVESTMENTS / "us_daily_stock_history"
OUT = INVESTMENTS / "daily_stock_history_index.json"


def load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        name = handle.name
    Path(name).replace(path)


def normalized_trade(payload: dict[str, Any], market: str) -> dict[str, Any] | None:
    decision = str(payload.get("decision") or "")
    trade_decisions = {"gpw": {"TRANSAKCJA"}, "us": {"TRADE"}}
    if decision not in trade_decisions[market]:
        return None
    selection = payload.get("selection") or {}
    if not isinstance(selection, dict) or not (selection.get("ticker") or selection.get("symbol")):
        return None
    outcome = payload.get("outcome") or {}
    return {
        "market": market,
        "date": payload.get("date"),
        "generated_at": payload.get("generated_at"),
        "ticker": selection.get("ticker") or selection.get("symbol"),
        "symbol": selection.get("symbol") or selection.get("ticker"),
        "name": selection.get("name"),
        "sector": selection.get("sector"),
        "score": selection.get("score"),
        "entry_zone": selection.get("entry_zone"),
        "stop": selection.get("stop"),
        "target": selection.get("target"),
        "reward_risk": selection.get("reward_risk"),
        "valid_until": selection.get("valid_until"),
        "decision": decision,
        "outcome": outcome if isinstance(outcome, dict) else {},
    }


def scan(directory: Path, market: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    if not directory.exists():
        return rows, suppressed

    if market == "us":
        payloads, suppressed = us_lifecycle.canonical_history_payloads(directory)
        for payload in payloads:
            row = normalized_trade(payload, market)
            if row:
                rows.append(row)
    else:
        for path in sorted(directory.glob("????-??-??.json")):
            payload = load(path)
            if not payload:
                continue
            row = normalized_trade(payload, market)
            if row:
                rows.append(row)

    rows.sort(key=lambda row: (str(row.get("date") or ""), str(row.get("generated_at") or "")), reverse=True)
    return rows, suppressed


def market_summary(rows: list[dict[str, Any]], suppressed: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    resolved = [row for row in rows if (row.get("outcome") or {}).get("status") == "RESOLVED"]
    activated = [row for row in resolved if (row.get("outcome") or {}).get("activated") is True]
    wins = [row for row in activated if float((row.get("outcome") or {}).get("return_percent", 0.0)) > 0]
    r_values = [float((row.get("outcome") or {}).get("r_multiple", 0.0)) for row in activated]
    returns = [float((row.get("outcome") or {}).get("return_percent", 0.0)) for row in activated]
    suppressed = list(suppressed or [])
    return {
        "selected_trades": len(rows),
        "resolved_trades": len(resolved),
        "activated_resolved_trades": len(activated),
        "suppressed_overlapping_signals": len(suppressed),
        "suppressed_signals": suppressed,
        "win_rate": round(len(wins) / len(activated), 4) if activated else None,
        "average_r": round(sum(r_values) / len(r_values), 3) if r_values else None,
        "average_return_percent": round(sum(returns) / len(returns), 3) if returns else None,
        "trades": rows,
    }


def build(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    gpw, gpw_suppressed = scan(GPW_DIR, "gpw")
    us, us_suppressed = scan(US_DIR, "us")
    all_rows = sorted(
        [*gpw, *us],
        key=lambda row: (str(row.get("date") or ""), str(row.get("generated_at") or "")),
        reverse=True,
    )
    return {
        "schema_version": "daily-stock-history-index-v2",
        "updated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "markets": {
            "gpw": market_summary(gpw, gpw_suppressed),
            "us": market_summary(us, us_suppressed),
        },
        "all": all_rows,
    }


def main() -> int:
    payload = build()
    atomic(OUT, payload)
    print(json.dumps({
        "status": "OK",
        "gpw": payload["markets"]["gpw"]["selected_trades"],
        "us": payload["markets"]["us"]["selected_trades"],
        "us_suppressed": payload["markets"]["us"]["suppressed_overlapping_signals"],
        "out": str(OUT.relative_to(ROOT)),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
