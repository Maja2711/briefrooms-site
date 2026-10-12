#!/usr/bin/env python3
"""Publish ONLY the read-only Shadow Engines status artifact using GitHub Contents API.

A single-file optimistic write avoids a git-rebase conflict against unrelated
frequent production commits. No execution, policy, position or risk state writes.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PUBLIC_PATH = "data/investments/shadow_engines_public.json"
API = "https://api.github.com"
RETRY_LIMIT = 6


def timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("missing timestamp")
    value = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timezone missing")
    return parsed.astimezone(timezone.utc)


def verify_snapshot(snapshot: dict, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be a JSON object")
    if snapshot.get("schema_version") != "briefrooms-shadow-engines-public-v1":
        raise ValueError("wrong Shadow Engines schema")
    if snapshot.get("read_only") is not True or snapshot.get("production_authority") is not False:
        raise ValueError("Shadow Observatory must remain read-only")
    if not isinstance(snapshot.get("engines"), list) or not isinstance(snapshot.get("coverage"), dict):
        raise ValueError("missing observable engines or coverage")
    age = (now - timestamp(snapshot.get("generated_at"))).total_seconds()
    if not -120 <= age <= 12 * 60:
        raise ValueError("refusing to publish a stale or future-dated snapshot")


def api_request(repo: str, token: str, method: str, payload: dict | None = None) -> dict:
    url = f"{API}/repos/{repo}/contents/{PUBLIC_PATH}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "BriefRooms-Shadow-Status-Publisher",
    }
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, headers=headers, method=method, data=body)
    with urlopen(req, timeout=25) as response:
        return json.load(response)


def publish(snapshot_file: Path, repo: str, token: str, request=api_request, sleep=time.sleep) -> str:
    if not token or not repo or "/" not in repo:
        raise ValueError("GitHub token and repository are required")
    raw = snapshot_file.read_bytes()
    snapshot = json.loads(raw)
    verify_snapshot(snapshot)
    generated = timestamp(snapshot["generated_at"])
    for attempt in range(RETRY_LIMIT):
        remote = request(repo, token, "GET")
        sha = remote.get("sha")
        if not sha:
            raise ValueError("could not read canonical file SHA")
        # A queued older publication cannot overwrite a more recent report.
        try:
            remote_bytes = base64.b64decode(remote.get("content") or "")
            prior = json.loads(remote_bytes)
            if timestamp(prior.get("generated_at")) >= generated:
                return "skipped_newer_remote"
        except (ValueError, TypeError, KeyError):
            pass
        try:
            request(repo, token, "PUT", {
                "message": "Refresh Shadow Engines observatory (read-only status)",
                "content": base64.b64encode(raw).decode("ascii"),
                "sha": sha,
                "branch": "main",
            })
            return "published"
        except HTTPError as error:
            if error.code != 409 or attempt == RETRY_LIMIT - 1:
                raise
            sleep(min(attempt + 1, 5))
    raise RuntimeError("unreachable: publication retry exhausted")


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN", "")
    try:
        result = publish(Path(PUBLIC_PATH), repo, token)
    except Exception as error:
        print(f"Shadow Observatory publication failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(f"Shadow Observatory publication: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
