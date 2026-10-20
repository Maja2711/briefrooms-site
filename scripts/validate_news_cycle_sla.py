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
    latest_failure = next((r for r in sorted(runs, key=lambda x: x.get("updated_at", ""), reverse=True)
                           if r.get("status") == "completed" and r.get("conclusion") in ("failure", "timed_out")), None)
    unresolved_failure = bool(latest_failure and (not latest or latest_failure["updated_at"] > latest["updated_at"]))
    return {
        "status": "healthy" if age is not None and age <= max_age_minutes and not unresolved_failure else "failed",
        "age_minutes": age,
        "last_successful_cycle_at": latest["updated_at"] if latest else None,
        "last_successful_run_url": latest.get("html_url") if latest else None,
        "reasons": (["successful_cycle_missing_or_stale"] if age is None or age > max_age_minutes else []) +
                   (["publisher_failed_without_later_success"] if unresolved_failure else []),
        "failed_run_url": latest_failure.get("html_url") if unresolved_failure else None,
    }


def verify_production(base_url: str = "https://briefrooms.com") -> list[str]:
    """Compare PL/EN production feeds and page markers with the production status."""
    try:
        def get(path: str) -> bytes:
            req = Request(f"{base_url}/{path}?cycle_sla=1",
                          headers={"User-Agent": "BriefRooms-news-watchdog", "Cache-Control": "no-cache"})
            with urlopen(req, timeout=15) as response:
                return response.read()
        status = json.loads(get("data/news/status.json"))
        marker = status.get("marker")
        if not marker:
            return ["production_marker_missing"]
        reasons = []
        for lang, page in (("pl", "pl/aktualnosci.html"), ("en", "en/news.html")):
            feed = json.loads(get(f"data/news/{lang}.json"))
            if feed.get("marker") != marker:
                reasons.append(f"production_feed_marker_mismatch:{lang}")
            html = get(page).decode("utf-8", "replace")
            if f'name="briefrooms-live-news-marker" content="{marker}"' not in html:
                reasons.append(f"production_page_marker_mismatch:{lang}")
        return reasons
    except (OSError, ValueError, KeyError) as exc:
        return [f"production_verification_failed:{type(exc).__name__}"]


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
    production_reasons = verify_production()
    if production_reasons:
        report["status"] = "failed"
        report["reasons"].extend(production_reasons)
    with open(args.output, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
    print(json.dumps(report))
    return int(report["status"] == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
