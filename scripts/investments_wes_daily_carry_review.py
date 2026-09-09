#!/usr/bin/env python3
"""Run the existing daily thesis review against active prior-week WES carry positions.

A qualified Monday/Tuesday replacement can cross the weekly file boundary because
its governed deadline is seven calendar days from the actual replacement entry.
This bridge reuses daily_position_review without duplicating its signal, material-
event, no-backdating, or execution logic.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import daily_position_review as daily
import investments_wes_guarded_lifecycle as lifecycle

ROOT = Path(__file__).resolve().parents[1]
WEEKLY_DIR = ROOT / "data" / "investments" / "weekly"
SCAN_WEEKS = 8


def active_carry_item(item: Dict[str, Any], now: datetime) -> bool:
    if not isinstance(item, dict) or not lifecycle.v5.open_position(item):
        return False
    deadline = lifecycle.valid_rolling_deadline(item)
    return deadline is not None and now < deadline


def carry_paths(now: datetime, weekly_dir: Optional[Path] = None) -> List[Path]:
    weekly_dir = weekly_dir or WEEKLY_DIR
    current = lifecycle.v2.current_week_path(now)
    found: List[Path] = []
    for path in sorted(weekly_dir.glob("*.json"), reverse=True)[:SCAN_WEEKS]:
        try:
            if path.resolve() == current.resolve():
                continue
        except Exception:
            if path == current:
                continue
        week = lifecycle.v4.read(path, {})
        if any(active_carry_item(item, now) for item in week.get("instruments") or []):
            found.append(path)
    return found


def review_path(path: Path, now: datetime) -> Dict[str, Any]:
    """Reuse the canonical daily reviewer for one prior-week ledger."""
    original_current_week_path = daily.model.current_week_path
    original_now_local = daily.legacy.now_local
    original_report_path = daily.REPORT_PATH
    temp_report = Path(tempfile.gettempdir()) / f"wes-carry-review-{path.stem}.json"
    try:
        daily.model.current_week_path = lambda _now=None: path
        daily.legacy.now_local = lambda: now
        daily.REPORT_PATH = temp_report
        return daily.review()
    finally:
        daily.model.current_week_path = original_current_week_path
        daily.legacy.now_local = original_now_local
        daily.REPORT_PATH = original_report_path
        try:
            temp_report.unlink(missing_ok=True)
        except Exception:
            pass


def review_prior_week_carries(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or lifecycle.v2.now_local()
    paths = carry_paths(now)
    reports = []
    for path in paths:
        report = review_path(path, now)
        reports.append({"path": str(path.relative_to(ROOT)), "report": report})
    return {
        "reviewed_at": now.isoformat(timespec="seconds"),
        "active_prior_week_carry_ledgers": len(paths),
        "reports": reports,
        "status": "completed" if paths else "no_active_prior_week_carry",
    }


def main() -> int:
    print(json.dumps(review_prior_week_carries(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
