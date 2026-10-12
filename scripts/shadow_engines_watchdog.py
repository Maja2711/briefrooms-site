#!/usr/bin/env python3
"""Independent GitHub Actions watchdog for the read-only Shadow Observatory.

Uses remote main-branch contents and Actions API; does not read the publisher's
workspace or run an engine. One deduplicated GitHub Issue per stale incident.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen

PUBLIC_PATH = "data/investments/shadow_engines_public.json"
WORKFLOW = "shadow-engines-observatory.yml"
ISSUE_TITLE = "[SHADOW OBSERVATORY] Stale status publication (>20 min)"
ISSUE_MARKER = "<!-- briefrooms-shadow-observatory-watchdog-v1 -->"
LIMIT_MINUTES = 20
API = "https://api.github.com"


def parse_time(value: object) -> datetime | None:
    try:
        if not isinstance(value, str) or not value:
            return None
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except ValueError:
        return None


def evaluate(snapshot: object, now: datetime, limit_minutes: int = LIMIT_MINUTES) -> dict:
    generated = parse_time(snapshot.get("generated_at")) if isinstance(snapshot, dict) else None
    if not isinstance(snapshot, dict):
        reason = "missing_or_unreadable_snapshot"
    elif snapshot.get("schema_version") != "briefrooms-shadow-engines-public-v1":
        reason = "invalid_schema"
    elif snapshot.get("read_only") is not True or snapshot.get("production_authority") is not False:
        reason = "authority_invariant_violation"
    elif not isinstance(snapshot.get("engines"), list) or not isinstance(snapshot.get("coverage"), dict):
        reason = "missing_engine_or_coverage_data"
    elif generated is None:
        reason = "missing_or_invalid_generated_at"
    else:
        age = (now.astimezone(timezone.utc) - generated).total_seconds() / 60
        reason = ("future_dated_report" if age < -2 else
                  "stale_report" if age > limit_minutes else "ok")
    return {
        "stale": reason != "ok",
        "reason": reason,
        "generated_at": generated.isoformat() if generated else None,
        "age_minutes": (
            round((now.astimezone(timezone.utc) - generated).total_seconds() / 60, 1)
            if generated else None
        ),
        "limit_minutes": limit_minutes,
    }


def api(repo: str, token: str, path: str, method: str = "GET", body: dict | None = None):
    if not repo or "/" not in repo or not token:
        raise ValueError("GitHub repository and token are required")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "BriefRooms-Shadow-Observatory-Watchdog",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"{API}/repos/{repo}/{path}", data=data, method=method, headers=headers)
    with urlopen(request, timeout=25) as response:
        return json.load(response)


def fetch_snapshot(repo: str, token: str, client=api) -> dict:
    obj = client(repo, token, f"contents/{PUBLIC_PATH}?ref=main")
    raw = base64.b64decode(obj["content"])
    return json.loads(raw)


def fetch_runs(repo: str, token: str, client=api) -> list[dict]:
    obj = client(repo, token, f"actions/workflows/{WORKFLOW}/runs?per_page=8")
    return [
        {
            "id": row.get("id"),
            "status": row.get("status"),
            "conclusion": row.get("conclusion"),
            "created_at": row.get("created_at"),
            "url": row.get("html_url"),
        }
        for row in (obj.get("workflow_runs") or [])[:8]
    ]


def open_issues(repo: str, token: str, client=api) -> list[dict]:
    # Fetch all pages; this repository can have many unrelated issues.
    matches = []
    for page in range(1, 21):
        items = client(repo, token, f"issues?state=open&per_page=100&page={page}")
        for item in items:
            if item.get("pull_request"):
                continue
            if item.get("title") == ISSUE_TITLE and ISSUE_MARKER in str(item.get("body") or ""):
                matches.append(item)
        if len(items) < 100:
            break
    return matches


def format_body(result: dict, runs: list[dict], diagnostic: str) -> str:
    lines = [
        ISSUE_MARKER,
        "## CRITICAL — stale Shadow Engines Observatory publication",
        f"Threshold: {LIMIT_MINUTES} minutes. Status: {result['reason']}.",
        f"Last generated_at: {result['generated_at'] or 'UNKNOWN'}.",
        f"Age in minutes: {result['age_minutes'] if result['age_minutes'] is not None else 'UNKNOWN'}.",
        "The LAB RUNNING/IDLE/NO DATA counters are historical until new publication is confirmed.",
        "This issue is observability-only. **Do not run trading engines or grant execution authority.**",
        "## Recent Observatory workflow runs",
    ]
    if runs:
        for row in runs:
            lines.append(
                f"- {row['created_at']}: {row['status']} / {row['conclusion']} — {row['url']}"
            )
    else:
        lines.append("- No runs were returned.")
    if diagnostic:
        lines.append(f"Diagnostic: {diagnostic}")
    lines.append("Automatic recovery: this issue closes after an independently verified fresh report.")
    return "\n".join(lines)


def reconcile(repo: str, token: str, result: dict, runs: list[dict], diagnostic: str, client=api) -> str:
    existing = open_issues(repo, token, client)
    if result["stale"]:
        if existing:
            return f"existing_issue_{existing[0]['number']}"
        created = client(repo, token, "issues", "POST", {
            "title": ISSUE_TITLE,
            "body": format_body(result, runs, diagnostic),
        })
        return f"opened_issue_{created['number']}"
    for issue in existing:
        number = issue["number"]
        client(repo, token, f"issues/{number}/comments", "POST", {
            "body": "RECOVERED: a fresh read-only Observatory report was verified by the independent watchdog."
        })
        client(repo, token, f"issues/{number}", "PATCH", {"state": "closed"})
    return f"healthy_closed_{len(existing)}"


def main() -> int:
    repo, token = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", "")
    if not repo or not token:
        print("Missing GITHUB_REPOSITORY or GH_TOKEN", file=sys.stderr)
        return 1
    diagnostic = ""
    try:
        snapshot = fetch_snapshot(repo, token)
    except Exception as error:
        snapshot = None
        diagnostic = f"snapshot fetch failed: {type(error).__name__}: {error}"
    result = evaluate(snapshot, datetime.now(timezone.utc))
    try:
        runs = fetch_runs(repo, token)
    except Exception as error:
        runs = []
        diagnostic += f"; Actions history unavailable: {type(error).__name__}: {error}"
    print(json.dumps({"check": result, "recent_runs": runs, "diagnostic": diagnostic}, indent=2))
    try:
        outcome = reconcile(repo, token, result, runs, diagnostic)
        print(f"Alert reconciliation: {outcome}")
    except Exception as error:
        print(f"Failed to deliver watchdog alert: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    # GitHub also raises a native failed-check alarm when the threshold is exceeded.
    return 1 if result["stale"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
