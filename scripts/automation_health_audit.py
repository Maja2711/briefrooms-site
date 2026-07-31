#!/usr/bin/env python3
"""Build the public automation health registry from Actions and published data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS = ROOT / "data" / "system" / "automation_status.json"
DEFAULT_INCIDENTS = ROOT / "data" / "system" / "automation_incidents.json"
ALLOWED_STATUSES = {"healthy", "degraded", "failed", "stale", "running"}
ACTIVE_RUN_STATES = {"queued", "in_progress", "pending", "waiting", "requested"}
FAILED_CONCLUSIONS = {"failure", "timed_out", "action_required", "startup_failure"}
DEGRADED_CONCLUSIONS = {"cancelled", "skipped", "neutral"}
SECRET_PATTERN = re.compile(
    r"(?i)(authorization|token|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)


@dataclass(frozen=True)
class Domain:
    key: str
    label: str
    workflow_file: str
    data_path: str
    timestamp_fields: tuple[str, ...]
    stale_after_minutes: int


DOMAINS = (
    Domain(
        "news_pl_en",
        "PL and EN news publication",
        "publish-news.yml",
        "data/news_publication_status.json",
        ("generated_at",),
        480,
    ),
    Domain(
        "investment_alert",
        "Investment daily market alert",
        "daily-market-alert.yml",
        "data/investments/daily_market_alert.json",
        ("updated_at",),
        2160,
    ),
    Domain(
        "portfolio_prices",
        "Portfolio 10K prices",
        "portfolio-10k-hourly-prices.yml",
        "data/investments/portfolio_10k.json",
        ("last_updated_at", "updated_at"),
        180,
    ),
    Domain(
        "brace_portfolio",
        "BRACE Portfolio Engine",
        "portfolio-10k-brace.yml",
        "data/portfolio10k/operational_state.json",
        ("generated_at",),
        2160,
    ),
    Domain(
        "brace_spx",
        "BRACE-SPX research",
        "brace-spx-recovery-engine.yml",
        "data/public/brace_spx_generation3_public.json",
        ("updated_at", "generated_at"),
        1440,
    ),
    Domain(
        "hot_x",
        "Hot X topics",
        "hot-x-topics.yml",
        "data/hot_tweets.json",
        ("updated_at",),
        1440,
    ),
)


class GitHubApiError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sanitize_summary(value: Any, limit: int = 240) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    text = SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return text[:limit] or None


def data_timestamp(domain: Domain, root: Path = ROOT) -> tuple[datetime | None, str | None]:
    path = root / domain.data_path
    payload = read_json(path, None)
    if not isinstance(payload, dict):
        return None, "missing_or_invalid_public_data"
    for field in domain.timestamp_fields:
        value = parse_time(payload.get(field))
        if value is not None:
            return value, None
    return None, "missing_data_timestamp"


def effective_stale_minutes(domain: Domain, now: datetime) -> int:
    if domain.key == "portfolio_prices":
        if now.weekday() >= 5:
            return 4320
        if now.hour < 6 or now.hour > 22:
            return 720
    if domain.key == "investment_alert" and now.weekday() >= 5:
        return 4320
    return domain.stale_after_minutes


def run_timestamp(run: dict[str, Any]) -> datetime | None:
    for field in ("updated_at", "run_started_at", "created_at"):
        parsed = parse_time(run.get(field))
        if parsed is not None:
            return parsed
    return None


def latest_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = [run for run in runs if run_timestamp(run) is not None]
    return max(valid, key=lambda run: run_timestamp(run) or datetime.min.replace(tzinfo=timezone.utc), default=None)


def latest_success(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    return latest_run([run for run in runs if run.get("conclusion") == "success"])


def build_domain_status(
    domain: Domain,
    runs: list[dict[str, Any]],
    now: datetime,
    data_updated_at: datetime | None,
    data_error: str | None,
    previous: dict[str, Any] | None = None,
    api_error: str | None = None,
) -> dict[str, Any]:
    previous = previous or {}
    current = latest_run(runs)
    success = latest_success(runs)
    threshold = effective_stale_minutes(domain, now)
    age = None
    if data_updated_at is not None:
        age = max(0, round((now - data_updated_at).total_seconds() / 60))

    last_attempt_at = isoformat(run_timestamp(current)) if current else previous.get("last_attempt_at")
    last_success_at = (
        isoformat(run_timestamp(success)) if success else previous.get("last_success_at")
    )
    run_id = str(current.get("id")) if current and current.get("id") is not None else previous.get("run_id")
    commit_sha = current.get("head_sha") if current else previous.get("commit_sha")
    run_url = current.get("html_url") if current else previous.get("run_url")
    conclusion = current.get("conclusion") if current else previous.get("run_conclusion")
    run_state = current.get("status") if current else previous.get("run_state")

    status = "healthy"
    failed_stage = None
    error_class = None
    error_summary = None

    if current and run_state in ACTIVE_RUN_STATES:
        status = "running"
    elif current and conclusion in FAILED_CONCLUSIONS:
        status = "failed"
        failed_stage = "github_actions_workflow"
        error_class = f"workflow_{conclusion}"
        error_summary = f"Latest run concluded {conclusion}; inspect run {run_id}."
    elif data_error:
        status = "failed"
        failed_stage = "public_data_validation"
        error_class = data_error
        error_summary = f"Public data check failed for {domain.data_path}."
    elif age is not None and age > threshold:
        status = "stale"
        failed_stage = "published_data_freshness"
        error_class = "stale_public_data"
        error_summary = (
            f"Published data is {age} minutes old; threshold is {threshold} minutes."
        )
    elif current and conclusion in DEGRADED_CONCLUSIONS:
        status = "degraded"
        failed_stage = "github_actions_workflow"
        error_class = f"workflow_{conclusion}"
        error_summary = f"Latest run concluded {conclusion}; inspect run {run_id}."
    elif api_error:
        status = "degraded"
        failed_stage = "github_actions_api"
        error_class = "actions_api_unavailable"
        error_summary = api_error

    result = {
        "workflow_file": domain.workflow_file,
        "data_path": domain.data_path,
        "last_attempt_at": last_attempt_at,
        "last_success_at": last_success_at,
        "status": status,
        "run_id": run_id,
        "run_state": run_state,
        "run_conclusion": conclusion,
        "run_url": run_url,
        "commit_sha": commit_sha,
        "data_updated_at": isoformat(data_updated_at),
        "data_age_minutes": age,
        "stale_after_minutes": threshold,
        "failed_stage": failed_stage,
        "error_class": error_class,
        "error_summary": sanitize_summary(error_summary),
    }
    assert result["status"] in ALLOWED_STATUSES
    return result


def github_json(
    url: str,
    token: str | None,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "briefrooms-automation-health-audit",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, method=method, headers=headers)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504}:
                break
        except URLError as exc:
            last_error = exc
        if attempt < 2:
            time.sleep(2**attempt)
    if isinstance(last_error, HTTPError):
        raise GitHubApiError(f"GitHub API returned HTTP {last_error.code}") from last_error
    raise GitHubApiError("GitHub API request failed") from last_error


def fetch_workflow_runs(repository: str, workflow_file: str, token: str | None) -> list[dict[str, Any]]:
    workflow_id = quote(workflow_file, safe="")
    query = urlencode({"branch": "main", "per_page": 50})
    payload = github_json(
        f"https://api.github.com/repos/{repository}/actions/workflows/{workflow_id}/runs?{query}",
        token,
    )
    runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
    return [run for run in runs if isinstance(run, dict)]


def build_registry(
    *,
    root: Path,
    now: datetime,
    previous: dict[str, Any],
    repository: str,
    token: str | None,
    offline: bool,
) -> dict[str, Any]:
    previous_workflows = previous.get("workflows", {}) if isinstance(previous, dict) else {}
    workflows: dict[str, Any] = {}
    for domain in DOMAINS:
        runs: list[dict[str, Any]] = []
        api_error = None
        if not offline:
            try:
                runs = fetch_workflow_runs(repository, domain.workflow_file, token)
            except GitHubApiError as exc:
                api_error = sanitize_summary(exc) or "GitHub Actions API unavailable"
        timestamp, data_error = data_timestamp(domain, root)
        workflows[domain.key] = build_domain_status(
            domain,
            runs,
            now,
            timestamp,
            data_error,
            previous_workflows.get(domain.key, {}),
            api_error,
        )
    return {
        "schema_version": "1.0",
        "generated_at": isoformat(now),
        "source": "github_actions_and_published_data",
        "repository": repository,
        "workflows": workflows,
    }


def incident_fingerprint(domain_status: dict[str, Any]) -> str:
    significant = {
        "status": domain_status.get("status"),
        "failed_stage": domain_status.get("failed_stage"),
        "error_class": domain_status.get("error_class"),
    }
    encoded = json.dumps(significant, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def issue_body(domain: Domain, status: dict[str, Any], fingerprint: str) -> str:
    lines = [
        f"Automation domain: `{domain.key}`",
        f"Status: `{status.get('status')}`",
        f"Workflow: `{domain.workflow_file}`",
        f"Last attempt: `{status.get('last_attempt_at') or 'unknown'}`",
        f"Last success: `{status.get('last_success_at') or 'unknown'}`",
        f"Published data: `{status.get('data_updated_at') or 'unknown'}`",
        f"Failure class: `{status.get('error_class') or 'unknown'}`",
        "",
        sanitize_summary(status.get("error_summary")) or "No additional public diagnostic is available.",
    ]
    if status.get("run_url"):
        lines.extend(("", f"Actions run: {status['run_url']}"))
    lines.extend(("", f"<!-- briefrooms-automation-fingerprint:{fingerprint} -->"))
    return "\n".join(lines)


def list_open_automation_issues(repository: str, token: str) -> dict[str, dict[str, Any]]:
    query = urlencode({"state": "open", "per_page": 100})
    payload = github_json(
        f"https://api.github.com/repos/{repository}/issues?{query}", token
    )
    return {
        item.get("title", ""): item
        for item in payload
        if isinstance(item, dict)
        and "pull_request" not in item
        and str(item.get("title", "")).startswith("[AUTOMATION]")
    }


def sync_incidents(
    registry: dict[str, Any],
    incidents: dict[str, Any],
    repository: str,
    token: str,
    now: datetime,
) -> dict[str, Any]:
    existing_issues = list_open_automation_issues(repository, token)
    previous = incidents.get("incidents", {}) if isinstance(incidents, dict) else {}
    updated: dict[str, Any] = dict(previous)
    for domain in DOMAINS:
        status = registry["workflows"][domain.key]
        title = f"[AUTOMATION] {domain.label}"
        current_issue = existing_issues.get(title)
        prior = previous.get(domain.key, {})
        unhealthy = status["status"] in {"failed", "stale"}
        if unhealthy:
            fingerprint = incident_fingerprint(status)
            changed = prior.get("fingerprint") != fingerprint or prior.get("state") != "open"
            number = current_issue.get("number") if current_issue else prior.get("issue_number")
            body = issue_body(domain, status, fingerprint)
            if changed:
                if current_issue:
                    github_json(
                        f"https://api.github.com/repos/{repository}/issues/{current_issue['number']}/comments",
                        token,
                        method="POST",
                        payload={"body": body},
                    )
                    number = current_issue["number"]
                else:
                    created = github_json(
                        f"https://api.github.com/repos/{repository}/issues",
                        token,
                        method="POST",
                        payload={"title": title, "body": body},
                    )
                    number = created.get("number")
            updated[domain.key] = {
                "fingerprint": fingerprint,
                "state": "open",
                "first_seen_at": prior.get("first_seen_at") or isoformat(now),
                "last_changed_at": isoformat(now) if changed else prior.get("last_changed_at"),
                "last_seen_at": isoformat(now),
                "issue_number": number,
            }
        elif current_issue:
            github_json(
                f"https://api.github.com/repos/{repository}/issues/{current_issue['number']}/comments",
                token,
                method="POST",
                payload={"body": f"Resolved by automation health audit at {isoformat(now)}."},
            )
            github_json(
                f"https://api.github.com/repos/{repository}/issues/{current_issue['number']}",
                token,
                method="PATCH",
                payload={"state": "closed", "state_reason": "completed"},
            )
            updated[domain.key] = {
                **prior,
                "state": "resolved",
                "resolved_at": isoformat(now),
                "last_seen_at": isoformat(now),
                "issue_number": current_issue["number"],
            }
        elif prior:
            updated[domain.key] = {
                **prior,
                "state": "resolved",
                "resolved_at": prior.get("resolved_at") or isoformat(now),
                "last_seen_at": isoformat(now),
            }
    return {
        "schema_version": "1.0",
        "generated_at": isoformat(now),
        "incidents": updated,
    }


def validate_registry(registry: dict[str, Any]) -> None:
    workflows = registry.get("workflows")
    if not isinstance(workflows, dict) or set(workflows) != {domain.key for domain in DOMAINS}:
        raise ValueError("automation registry does not contain the required domains")
    for key, status in workflows.items():
        if status.get("status") not in ALLOWED_STATUSES:
            raise ValueError(f"invalid automation status for {key}")
        if "last_attempt_at" not in status or "last_success_at" not in status:
            raise ValueError(f"missing attempt/success timestamps for {key}")
        serialized = json.dumps(status).lower()
        if "bearer " in serialized or "github_pat_" in serialized or "ghp_" in serialized:
            raise ValueError(f"possible secret in public automation status for {key}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", "Maja2711/briefrooms-site"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--status-path", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--incidents-path", type=Path, default=DEFAULT_INCIDENTS)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--now")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--sync-issues", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.validate_only:
        validate_registry(read_json(args.status_path, {}))
        print(f"Validated {args.status_path}")
        return 0
    now = parse_time(args.now) if args.now else utc_now()
    if now is None:
        raise SystemExit("--now must be an ISO-8601 timestamp")
    token = os.getenv(args.token_env) or None
    previous = read_json(args.status_path, {})
    registry = build_registry(
        root=args.root,
        now=now,
        previous=previous,
        repository=args.repository,
        token=token,
        offline=args.offline,
    )
    validate_registry(registry)
    incidents = read_json(args.incidents_path, {"schema_version": "1.0", "incidents": {}})
    if args.sync_issues:
        if not token:
            raise SystemExit(f"--sync-issues requires {args.token_env}")
        try:
            incidents = sync_incidents(registry, incidents, args.repository, token, now)
        except GitHubApiError as exc:
            print(f"::warning::Issue synchronization failed: {sanitize_summary(exc)}", file=sys.stderr)
    write_json_atomic(args.status_path, registry)
    write_json_atomic(args.incidents_path, incidents)
    summary = ", ".join(
        f"{key}={value['status']}" for key, value in registry["workflows"].items()
    )
    print(f"Automation health: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
