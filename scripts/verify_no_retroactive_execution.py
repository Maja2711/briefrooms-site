#!/usr/bin/env python3
"""Pre-commit airlock for NO RETROACTIVE EXECUTION.

Compare the working tree with the commit currently checked out. Existing LIVE
positions are grandfathered. Any *new* LIVE opening/execution event created by
this run must be contemporaneous with the run; historical reconstruction is
rejected. Shadow/replay artifacts may contain historical simulated events, but
never count as canonical LIVE state.

# NO RETROACTIVE EXECUTION
This verifier deliberately scans all changed JSON under data/, not only the
Weekly/Stock Trading subtree. BRACE, Daily Trading, Portfolio 10K and future
canonical trading engines therefore inherit the same publication airlock.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from scripts.no_retroactive_execution import (
        MAX_LIVE_MARKET_DATA_LAG,
        RetroactiveExecutionError,
        assert_live_fill,
        parse_ts,
    )
except ModuleNotFoundError:  # direct scripts/ execution
    from no_retroactive_execution import (
        MAX_LIVE_MARKET_DATA_LAG,
        RetroactiveExecutionError,
        assert_live_fill,
        parse_ts,
    )

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
NON_LIVE_TOKENS = (
    "shadow",
    "replay",
    "counterfactual",
    "backtest",
    "research",
    "simulation",
    "simulated",
)
CANONICAL_NAMES = {
    "stock_trading_portfolio.json",
    "stock_trading_v2_production_state.json",
    "us_daily_stock_position.json",
    "multi_instrument_exposure_state_v5.json",
    "multi_instrument_exposure_report_v5.json",
    "wes_report.json",
    "paper_portfolio.json",
    "execution_results.json",
    "order_queue.json",
    "eurusd_daily_spot.json",
    "eurusd_daily_history.json",
}


def _run(*args: str, check: bool = True) -> str:
    return subprocess.run(
        args,
        cwd=ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def _load_worktree(path: str) -> Any:
    try:
        return json.loads((ROOT / path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _load_ref(path: str, ref: str) -> Any:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None


def _changed_json(ref: str) -> list[str]:
    # Intentionally scan the complete data tree. _path_is_canonical() decides
    # which files are executable state; this prevents a new engine directory
    # from silently falling outside the safety perimeter.
    names = set(_run("git", "diff", "--name-only", ref, "--", "data").splitlines())
    names.update(
        _run("git", "diff", "--name-only", "--cached", ref, "--", "data").splitlines()
    )
    return sorted(x for x in names if x.endswith(".json") and (ROOT / x).exists())


def _path_is_non_live(path: str) -> bool:
    lowered = path.lower()
    return any(token in lowered for token in NON_LIVE_TOKENS)


def _path_is_canonical(path: str, payload: Any) -> bool:
    normalized = path.replace("\\", "/").lower()
    p = Path(path)
    if _path_is_non_live(path):
        return False
    if p.name in CANONICAL_NAMES:
        return True
    if "/weekly/" in normalized and p.name.endswith(".json"):
        return True
    if any(token in normalized for token in ("/portfolio10k/", "portfolio_10k", "portfolio-10k")):
        return True
    if isinstance(payload, dict):
        mode = str(
            payload.get("mode")
            or payload.get("execution_mode")
            or payload.get("environment")
            or ""
        ).upper()
        if mode in {"LIVE", "PRODUCTION", "PAPER_LIVE"}:
            return True
    return False


def _walk(value: Any, pointer: str = "$") -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        yield pointer, value
        for key, child in value.items():
            yield from _walk(child, f"{pointer}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{pointer}[{index}]")


def _is_execution_event(row: dict[str, Any]) -> bool:
    action = str(row.get("action") or "").lower()
    status = str(row.get("status") or row.get("trade_status") or "").lower()
    direction = str(row.get("direction") or "").lower()
    side = str(row.get("side") or "").upper()
    has_entry = any(
        row.get(k) is not None
        for k in (
            "entry_at",
            "entry_captured_at",
            "opened_at",
            "entry_time",
            "entry_timestamp",
            "entry_price",
            "entry",
        )
    )
    if action == "open":
        return True
    if status in {"open", "opened"} and has_entry:
        return True
    if direction in {"long", "short"} and row.get("entry_price") is not None and row.get("exit_price") is None:
        return True
    if side in {"BUY", "SELL"} and row.get("executed_at") and row.get("price") is not None:
        return True
    if status in {"paper_executed", "executed", "filled"} and row.get("executed_at"):
        return True
    return False


def _entry_ts(row: dict[str, Any]) -> Any:
    for key in (
        "entry_at",
        "entry_captured_at",
        "opened_at",
        "entry_time",
        "entry_timestamp",
        "executed_at",
    ):
        if row.get(key):
            return row.get(key)
    return None


def _decision_ts(row: dict[str, Any]) -> Any:
    for key in (
        "entry_decision_at",
        "decision_at",
        "decided_at",
        "decision_created_at",
        "signal_at",
        "queued_at",
    ):
        if row.get(key):
            return row.get(key)
    decision = row.get("entry_decision")
    if isinstance(decision, dict):
        for key in ("decision_at", "decided_at", "decision_created_at", "signal_at"):
            if decision.get(key):
                return decision.get(key)
    return _entry_ts(row)


def _event_identity(row: dict[str, Any]) -> str:
    fields = {
        key: row.get(key)
        for key in (
            "transaction_id",
            "order_id",
            "position_id",
            "leg_id",
            "instrument_id",
            "symbol",
            "ticker",
            "market",
            "side",
            "entry_at",
            "entry_captured_at",
            "opened_at",
            "entry_time",
            "entry_timestamp",
            "executed_at",
        )
        if row.get(key) is not None
    }
    if not fields:
        fields = {
            "entry": row.get("entry"),
            "entry_price": row.get("entry_price"),
            "price": row.get("price"),
            "direction": row.get("direction"),
            "side": row.get("side"),
        }
    return hashlib.sha256(
        json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def _events(payload: Any) -> dict[str, tuple[str, dict[str, Any]]]:
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    for pointer, row in _walk(payload):
        if _is_execution_event(row):
            out[_event_identity(row)] = (pointer, row)
    return out


def verify_file(path: str, baseline_ref: str, run_started_at: datetime) -> list[str]:
    current = _load_worktree(path)
    if not _path_is_canonical(path, current):
        return []
    before = _load_ref(path, baseline_ref)
    previous = _events(before)
    violations: list[str] = []
    for identity, (pointer, row) in _events(current).items():
        if identity in previous:
            continue
        entry_at = _entry_ts(row)
        decision_at = _decision_ts(row)
        if not entry_at:
            violations.append(
                f"{path} {pointer}: missing_execution_provenance: "
                "new LIVE execution event has no timestamp"
            )
            continue
        try:
            assert_live_fill(
                entry_at=entry_at,
                decision_at=decision_at,
                run_started_at=run_started_at,
                fill_persisted_before_run=False,
                decision_persisted_before_run=False,
                mode="LIVE",
                max_market_data_lag=MAX_LIVE_MARKET_DATA_LAG,
            )
        except RetroactiveExecutionError as exc:
            violations.append(f"{path} {pointer}: {exc}")
    return violations


def verify_shadow_isolation(paths: Iterable[str]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        if not _path_is_non_live(path):
            continue
        payload = _load_worktree(path)
        if not isinstance(payload, dict):
            continue
        mode = str(payload.get("mode") or payload.get("execution_mode") or "").upper()
        if mode in {"LIVE", "PRODUCTION", "PAPER_LIVE"}:
            violations.append(f"{path}: shadow/replay artifact declares executable mode={mode}")
        if payload.get("execution_enabled") is True or payload.get("broker_execution_enabled") is True:
            violations.append(f"{path}: shadow/replay artifact enables execution")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-ref", default="HEAD")
    parser.add_argument("--run-start", default=os.getenv("NO_RETROACTIVE_RUN_STARTED_AT"))
    parser.add_argument("--paths", nargs="*")
    args = parser.parse_args()
    run_started_at = parse_ts(args.run_start) if args.run_start else datetime.now(UTC)
    if run_started_at is None:
        print("NO RETROACTIVE EXECUTION: invalid --run-start", file=sys.stderr)
        return 2
    paths = args.paths or _changed_json(args.baseline_ref)
    violations: list[str] = []
    for path in paths:
        if path.endswith(".json") and (ROOT / path).exists():
            violations.extend(verify_file(path, args.baseline_ref, run_started_at))
    violations.extend(verify_shadow_isolation(paths))
    if violations:
        print("NO RETROACTIVE EXECUTION: BLOCKED", file=sys.stderr)
        for item in violations:
            print(f" - {item}", file=sys.stderr)
        return 1
    print(f"NO RETROACTIVE EXECUTION: PASS ({len(paths)} changed data JSON files checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
