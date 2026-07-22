#!/usr/bin/env python3
"""Verify that the public BriefRooms site matches the expected main commit."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    body: bytes


def fetch(url: str, *, timeout: float) -> FetchResult:
    request = Request(
        url,
        headers={
            "Accept": "*/*",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "BriefRooms-production-verifier/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return FetchResult(
            url=response.geturl(),
            status=getattr(response, "status", 200),
            body=response.read(),
        )


def cache_busted_url(base_url: str, path: str, expected_sha: str, attempt: int) -> str:
    target = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    separator = "&" if "?" in target else "?"
    return f"{target}{separator}{urlencode({'sha': expected_sha, 'attempt': attempt})}"


def verify_version(base_url: str, expected_sha: str, *, attempts: int, interval: float, timeout: float) -> None:
    last_error = "production version file was not available"
    for attempt in range(1, attempts + 1):
        url = cache_busted_url(base_url, "/build-version.json", expected_sha, attempt)
        try:
            result = fetch(url, timeout=timeout)
            payload = json.loads(result.body.decode("utf-8"))
            actual_sha = str(payload.get("sha", ""))
            if actual_sha == expected_sha:
                print(f"Production reports expected SHA {expected_sha}.")
                return
            last_error = f"expected SHA {expected_sha}, received {actual_sha or 'empty SHA'}"
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < attempts:
            print(f"Attempt {attempt}/{attempts}: {last_error}; retrying.", file=sys.stderr)
            time.sleep(interval)

    raise RuntimeError(f"Production parity check failed: {last_error}")


def verify_homepages(base_url: str, expected_sha: str, *, timeout: float) -> None:
    required_fragments = (
        '<!-- HOME_BRIEFS_START -->',
        'class="brief-card"',
    )
    forbidden_fragments = (
        "Karty uzupełnią się po wczytaniu home_brief.json",
        "The cards update when home_brief.json loads",
    )

    for language in ("pl", "en"):
        url = cache_busted_url(base_url, f"/{language}/", expected_sha, 1)
        result = fetch(url, timeout=timeout)
        html = result.body.decode("utf-8")
        missing = [fragment for fragment in required_fragments if fragment not in html]
        forbidden = [fragment for fragment in forbidden_fragments if fragment in html]
        if missing or forbidden:
            details = []
            if missing:
                details.append(f"missing {missing}")
            if forbidden:
                details.append(f"contains stale placeholders {forbidden}")
            raise RuntimeError(f"/{language}/ failed static-content verification: {'; '.join(details)}")
        print(f"/{language}/ contains static brief cards and no stale placeholder.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--base-url", default="https://briefrooms.com")
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error("--attempts must be at least 1")
    if args.interval < 0:
        parser.error("--interval cannot be negative")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def main() -> int:
    args = parse_args()
    expected_sha = args.expected_sha.strip().lower()
    if len(expected_sha) != 40 or any(character not in "0123456789abcdef" for character in expected_sha):
        print("--expected-sha must be a full 40-character hexadecimal commit SHA", file=sys.stderr)
        return 2

    try:
        verify_version(
            args.base_url,
            expected_sha,
            attempts=args.attempts,
            interval=args.interval,
            timeout=args.timeout,
        )
        verify_homepages(args.base_url, expected_sha, timeout=args.timeout)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Production matches {expected_sha}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
