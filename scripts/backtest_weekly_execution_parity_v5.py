#!/usr/bin/env python3
"""Replay the saved paper-trading ledger against the exact v5 execution rules.

This is an execution-parity backtest, not a synthetic long-horizon performance
claim. It uses immutable weekly files and closed legs, so it tests the rules that
actually governed published positions: validation, decision timing, NO_TRADE,
re-entry locks and after-cost accounting.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
WEEKLY = ROOT / "data/investments/weekly"
METHOD = ROOT / "data/investments/methodology.json"
OUT = ROOT / "data/investments/weekly_execution_parity_v5.json"
TZ = ZoneInfo("Europe/Warsaw")


def read(path: Path, default: Any) -> Any:
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return default


def dt(value: Any) -> Optional[datetime]:
    try:
        x = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (x.replace(tzinfo=TZ) if x.tzinfo is None else x.astimezone(TZ))
    except Exception: return None


def finite(value: Any) -> Optional[float]:
    try:
        x = float(value); return x if x == x and abs(x) != float("inf") else None
    except Exception: return None


def enabled_map() -> Dict[str, bool]:
    method = read(METHOD, {})
    return {str(x.get("id")): bool(x.get("enabled_for_new_positions", False)) for x in method.get("instruments", [])}


def event(item: Dict[str, Any], leg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source = leg or item
    decision = source.get("entry_decision") if isinstance(source.get("entry_decision"), dict) else {}
    entry_at = dt(source.get("entry_captured_at")); decision_at = dt(decision.get("decided_at") or item.get("entry_decision_at"))
    exit_at = dt(source.get("exit_captured_at")); reason = str(source.get("exit_reason") or "")
    net = finite(source.get("net_result_percent"))
    if net is None: net = finite(source.get("result_percent"))
    return {
        "instrument_id": str(source.get("instrument_id") or item.get("instrument_id") or ""),
        "entry_at": entry_at, "decision_at": decision_at, "exit_at": exit_at,
        "exit_reason": reason, "strategy_id": source.get("strategy_id") or decision.get("strategy_id"),
        "validation_gate": decision.get("validation_gate") or item.get("validation_gate"),
        "net_result_percent": net,
    }


def events(week: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in week.get("instruments", []):
        legs = [x for x in item.get("position_legs", []) if isinstance(x, dict)]
        rows.extend(event(item, leg) for leg in legs)
        if item.get("entry_captured_at") and not item.get("exit_captured_at"):
            rows.append(event(item))
        elif item.get("entry_captured_at") and item.get("exit_captured_at") and not legs:
            rows.append(event(item))
    return sorted(rows, key=lambda x: x.get("entry_at") or datetime.min.replace(tzinfo=TZ))


def check_week(path: Path, gate: Dict[str, bool]) -> Dict[str, Any]:
    week = read(path, {}); violations: List[Dict[str, Any]] = []; warnings: List[Dict[str, Any]] = []
    strict = str(week.get("method_version") or "").startswith("5.")
    rows = events(week); locks: Dict[str, Optional[datetime]] = {}
    end = dt((week.get("market_window") or {}).get("exit_target_local"))
    for row in rows:
        iid = row["instrument_id"]; entry_at = row["entry_at"]; decision_at = row["decision_at"]
        if strict and decision_at is not None and not gate.get(iid, False) and row.get("validation_gate") != "grandfathered_existing_position_no_new_entries":
            violations.append({"instrument_id": iid, "rule": "shared_validation_gate", "entry_at": str(entry_at)})
        if decision_at is None:
            warnings.append({"instrument_id": iid, "rule": "legacy_decision_timestamp_missing"})
        elif entry_at is None or entry_at < decision_at:
            violations.append({"instrument_id": iid, "rule": "no_retroactive_entry", "decision_at": str(decision_at), "entry_at": str(entry_at)})
        lock_until = locks.get(iid)
        if decision_at is not None and lock_until and entry_at and entry_at < lock_until:
            violations.append({"instrument_id": iid, "rule": "same_week_reentry_lock", "entry_at": str(entry_at), "locked_until": str(lock_until)})
        if decision_at is not None and row["exit_reason"].startswith(("event_review_", "daily_model_")):
            locks[iid] = end
    for item in week.get("instruments", []):
        if item.get("no_trade_decision") and item.get("trade_status") == "open":
            violations.append({"instrument_id": item.get("instrument_id"), "rule": "no_trade_must_not_open"})
    returns = [x["net_result_percent"] for x in rows if x["net_result_percent"] is not None]
    return {
        "week_id": week.get("week_id") or path.stem, "model_version": week.get("method_version"),
        "entries": len(rows), "closed_net_results": len(returns), "net_result_percent": round(sum(returns), 6),
        "violations": violations, "warnings": warnings,
    }


def metrics(values: List[float]) -> Dict[str, Any]:
    if not values: return {"closed_legs": 0, "net_result_percent": 0.0, "win_rate": None, "max_drawdown_percent": None}
    equity = peak = 1.0; drawdown = 0.0
    for value in values:
        equity *= 1 + value / 100; peak = max(peak, equity); drawdown = min(drawdown, equity / peak - 1)
    return {
        "closed_legs": len(values), "net_result_percent": round((equity - 1) * 100, 6),
        "mean_net_result_percent": round(sum(values) / len(values), 6),
        "win_rate": round(sum(x > 0 for x in values) / len(values), 6),
        "max_drawdown_percent": round(drawdown * 100, 6),
    }


def main() -> None:
    gate = enabled_map(); weeks = [check_week(path, gate) for path in sorted(WEEKLY.glob("*.json"))]
    values: List[float] = []
    for path in sorted(WEEKLY.glob("*.json")):
        for row in events(read(path, {})):
            if row["net_result_percent"] is not None: values.append(row["net_result_percent"])
    violations = [v for week in weeks for v in week["violations"]]
    report = {
        "model_version": "5.0.0-execution-parity", "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "status": "passed" if not violations else "failed", "scope": "immutable_saved_paper_execution_ledger",
        "rules": ["shared_validation_gate", "entry_not_before_decision", "same_week_lock_after_thesis_or_event_exit", "no_trade_never_opens", "after_cost_results"],
        "limitations": "This replay verifies actual published execution. It is not a synthetic claim about unobserved historical trades.",
        "metrics": metrics(values), "violations": violations, "weeks": weeks,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "violations": len(violations), **report["metrics"]}, ensure_ascii=False))
    if violations: raise SystemExit(1)


if __name__ == "__main__": main()
