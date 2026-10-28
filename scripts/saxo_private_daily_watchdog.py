#!/usr/bin/env python3
"""Read-only private EURUSD D1 liveness guard, one bounded recovery dispatch.

Never logs D1 payloads, Saxo prices, trade IDs, secrets, P/L or quote content.
This guard has no directional, fill, close, or broker authority.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from daily_eurusd_cycle_watchdog import fx_market_active
from saxo_private_daily_d1_runner import D1, PrivateStateFailure

WORKFLOW = "saxo-private-eurusd-daily-d1-cycle.yml"
MAX_AGE_MINUTES = 20
SQL = "SELECT generation, updated_at FROM private_saxo_eurusd_daily WHERE id=1"


def evaluate(rows: list[dict[str, Any]], now: datetime, *, market_active: bool) -> str:
    if not market_active:
        return "INACTIVE_FX_MARKET"
    if len(rows) != 1:
        return "MISSING_D1_STATE"
    row = rows[0]
    if type(row.get("generation")) is not int or row["generation"] < 1:
        return "NO_COMPLETED_PRIVATE_CYCLE"
    try:
        stamp = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return "INVALID_D1_TIMESTAMP"
        age = (now - stamp.astimezone(timezone.utc)).total_seconds() / 60
    except (ValueError, TypeError, OverflowError):
        return "INVALID_D1_TIMESTAMP"
    if age < -2:
        return "D1_CLOCK_SKEW"
    return "FRESH" if age <= MAX_AGE_MINUTES else "STALE"


def _api(url: str, token: str, *, payload: dict[str, str] | None = None) -> dict[str, Any]:
    if not token or not url.startswith("https://api.github.com/"):
        raise RuntimeError("github_api_unauthorized")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method="POST" if payload is not None else "GET",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "BriefRoomsPrivateSaxoWatchdog/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if payload is not None:
            if response.status not in (201, 204):
                raise RuntimeError("recovery_not_accepted")
            return {}
        return json.loads(response.read(250_000))


def recovery(repo: str, token: str) -> str:
    if not repo or "/" not in repo:
        raise RuntimeError("repository_unavailable")
    root = "https://api.github.com/repos/" + repo + "/actions/workflows/" + WORKFLOW
    # Do not queue another run when the target is already queued or executing.
    for status in ("queued", "in_progress", "waiting", "pending"):
        result = _api(root + "/runs?branch=main&status=" + status + "&per_page=25", token)
        if result.get("workflow_runs"):
            return "ALREADY_RUNNING"
    _api(root + "/dispatches", token, payload={"ref": "main", "inputs": {"operation": "run"}})
    return "DISPATCHED_ONE_RECOVERY"


def main() -> int:
    if os.environ.get("SAXO_PRIVATE_AUTO_ENABLED") != "true":
        print("PRIVATE_EURUSD_WATCHDOG=DISABLED")
        return 0
    now = datetime.now(timezone.utc)
    active = fx_market_active(now)
    if not active:
        print("PRIVATE_EURUSD_WATCHDOG=INACTIVE_FX_MARKET")
        return 0
    try:
        rows = D1().query(SQL)["results"]
        result = evaluate(rows, now, market_active=True)
    except Exception:
        print("PRIVATE_EURUSD_WATCHDOG=STORAGE_UNAVAILABLE_FAIL_CLOSED")
        print("::error::Private Saxo EURUSD state cannot be verified (no recovery without verified state).")
        return 1
    print("PRIVATE_EURUSD_WATCHDOG=" + result)
    if result == "FRESH":
        return 0
    if result == "STALE":
        try:
            action = recovery(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", ""))
            print("PRIVATE_EURUSD_RECOVERY=" + action)
        except Exception:
            print("PRIVATE_EURUSD_RECOVERY=DISPATCH_FAILED")
        print("::error::Private Saxo EURUSD D1 state stale: one controlled recovery attempted.")
        return 1
    # A missing or corrupt row must never be silently initialized.
    print("::error::Private Saxo EURUSD D1 state invalid or no successful cycle; human review required.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
