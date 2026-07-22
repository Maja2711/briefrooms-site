#!/usr/bin/env python3
"""Verify that public BriefRooms files match the current main checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_CONTRACT = "production-parity-v2"
PARITY_PATHS = (
    "build-version.json",
    "robots.txt",
    "sitemap.xml",
    "pl/index.html",
    "en/index.html",
    "pl/inwestycje.html",
    "en/investments.html",
    "scripts/home-briefs.js",
    "scripts/hot-x-render.js",
)


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
            "User-Agent": "BriefRooms-production-verifier/2.0",
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


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def local_bytes(path: str) -> bytes:
    target = ROOT / path
    if not target.is_file():
        raise RuntimeError(f"Required repository file is missing: {path}")
    return target.read_bytes()


def verify_contract(
    base_url: str,
    expected_sha: str,
    *,
    attempts: int,
    interval: float,
    timeout: float,
) -> None:
    last_error = "production contract file was not available"
    for attempt in range(1, attempts + 1):
        url = cache_busted_url(base_url, "/build-version.json", expected_sha, attempt)
        try:
            result = fetch(url, timeout=timeout)
            payload = json.loads(result.body.decode("utf-8"))
            contract = str(payload.get("deployment_contract", ""))
            repository = str(payload.get("repository", ""))
            branch = str(payload.get("branch", ""))
            if (
                contract == DEPLOYMENT_CONTRACT
                and repository == "Maja2711/briefrooms-site"
                and branch == "main"
            ):
                print(f"Production exposes deployment contract {DEPLOYMENT_CONTRACT}.")
                return
            last_error = (
                "unexpected deployment contract: "
                f"contract={contract or 'empty'}, repository={repository or 'empty'}, "
                f"branch={branch or 'empty'}"
            )
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        if attempt < attempts:
            print(f"Attempt {attempt}/{attempts}: {last_error}; retrying.", file=sys.stderr)
            time.sleep(interval)

    raise RuntimeError(f"Production contract check failed: {last_error}")


def verify_exact_files(base_url: str, expected_sha: str, *, timeout: float) -> None:
    mismatches: list[str] = []
    for path in PARITY_PATHS:
        expected = local_bytes(path)
        url = cache_busted_url(base_url, f"/{path}", expected_sha, 1)
        try:
            result = fetch(url, timeout=timeout)
        except (HTTPError, URLError, TimeoutError) as exc:
            mismatches.append(f"/{path}: {type(exc).__name__}: {exc}")
            continue

        if result.body != expected:
            mismatches.append(
                f"/{path}: repository sha256={digest(expected)}, "
                f"production sha256={digest(result.body)}, bytes={len(expected)}/{len(result.body)}"
            )
            continue
        print(f"/{path} matches main exactly ({digest(expected)[:12]}).")

    if mismatches:
        formatted = "\n - ".join(mismatches)
        raise RuntimeError(f"Production file parity failed:\n - {formatted}")


def verify_homepage_contract() -> None:
    required_fragments = (
        '<!-- HOME_BRIEFS_START -->',
        'class="brief-card"',
    )
    forbidden_fragments = (
        "Karty uzupełnią się po wczytaniu home_brief.json",
        "The cards update when home_brief.json loads",
    )
    for language in ("pl", "en"):
        html = local_bytes(f"{language}/index.html").decode("utf-8")
        missing = [fragment for fragment in required_fragments if fragment not in html]
        forbidden = [fragment for fragment in forbidden_fragments if fragment in html]
        if missing or forbidden:
            details = []
            if missing:
                details.append(f"missing {missing}")
            if forbidden:
                details.append(f"contains stale placeholders {forbidden}")
            raise RuntimeError(
                f"Repository /{language}/ violates static-content contract: {'; '.join(details)}"
            )


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
        verify_homepage_contract()
        verify_contract(
            args.base_url,
            expected_sha,
            attempts=args.attempts,
            interval=args.interval,
            timeout=args.timeout,
        )
        verify_exact_files(args.base_url, expected_sha, timeout=args.timeout)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Production files match main checkout {expected_sha}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
