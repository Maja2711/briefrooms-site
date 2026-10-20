#!/usr/bin/env python3
"""Create one actionable GitHub issue per ongoing news publication incident."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

TITLE = "[NEWS SLA] Publication stale or production mismatch"


def issue_body(report: dict, repository: str, run_id: str) -> str:
    reasons = report.get("reasons") or ["health validation failed before report completion"]
    details = "\n".join("- " + str(reason) for reason in reasons)
    return (
        "Automated publication SLA incident.\n\n"
        f"Checked at: {report.get('checked_at', 'unknown')} UTC\n"
        f"Publication age: {report.get('age_minutes', 'unknown')} minutes (limit: 90)\n"
        f"Marker: {report.get('marker', 'unknown')}\n"
        f"Health: {report.get('status', 'failed')}\n"
        f"Reasons:\n{details}\n\n"
        f"Workflow: https://github.com/{repository}/actions/runs/{run_id}\n"
    )


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return result.stdout


def notify(report: dict, repository: str, run_id: str) -> str:
    issues = json.loads(gh("issue", "list", "--repo", repository, "--state", "open",
                           "--limit", "100", "--json", "number,title"))
    existing = next((issue for issue in issues if issue["title"] == TITLE), None)
    body = issue_body(report, repository, run_id)
    if existing:
        gh("issue", "comment", str(existing["number"]), "--repo", repository, "--body", body)
        return "updated"
    gh("issue", "create", "--repo", repository, "--title", TITLE, "--body", body)
    return "created"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {"status": "failed", "reasons": ["health_check_failed_without_valid_report"]}
    if report.get("status") != "failed":
        raise SystemExit("Refusing to open incident without failed health status")
    print(notify(report, os.environ["GH_REPO"], os.environ["GITHUB_RUN_ID"]))


if __name__ == "__main__":
    main()
