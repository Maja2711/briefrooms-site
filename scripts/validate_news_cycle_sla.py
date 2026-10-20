#!/usr/bin/env python3
"""Check successful publisher runs independently from editorial article timestamps.

Uses GitHub Actions API, not an editable heartbeat committed by the watchdog itself.
A run is successful only when the publisher's production verification job passed.
"""
from __future__ import annotations
import argparse
import json
import os
from datetime import datetime, timezone
from urllib.request import Request, urlopen

WORKFLOW = "publish-news.yml"


def assess_runs(runs: list[dict], now: datetime, max_age_minutes: int = 90) -> dict:
    successful = [r for r in runs if r.get("conclusion") == "success"
                  and r.get("status") == "completed"
                  and r.get("event") in ("schedule", "workflow_dispatch", "push")
                  and r.get("run_started_at") and r.get("updated_at")]
    successful.sort(key=lambda r: r["updated_at"], reverse=True)
    latest = successful[0] if successful else None
    age = None
    if latest:
        completed = datetime.fromisoformat(latest["updated_at"].replace("Z", "+00:00"))
        age = max(0, (now - completed).total_seconds() / 60)
    return {
        "status": "healthy" if age is not None and age <= max_age_minutes else "failed",
        "age_minutes": age,
        "last_successful_cycle_at": latest["updated_at"] if latest else None,
        "last_successful_run_url": latest.get("html_url") if latest else None,
        "reasons": [] if age is not None and age <= max_age_minutes else ["successful_cycle_missing_or_stale"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "Maja2711/briefrooms-site"))
    parser.add_argument("--max-age-minutes", type=int, default=90)
    parser.add_argument("--output", default="/tmp/news-publication-sla.json")
    args = parser.parse_args()
    request = Request(
        f"https://api.github.com/repos/{args.repo}/actions/workflows/{WORKFLOW}/runs?per_page=50",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "BriefRooms-news-watchdog",
                 **({"Authorization": f"Bearer {os.environ['GH_TOKEN']}"} if os.environ.get("GH_TOKEN") else {})},
    )
    with urlopen(request, timeout=20) as response:
        runs = json.load(response)["workflow_runs"]
    report = assess_runs(runs, datetime.now(timezone.utc), args.max_age_minutes)
    with open(args.output, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
    print(json.dumps(report))
    return int(report["status"] == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
