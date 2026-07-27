#!/usr/bin/env python3
"""Verify one atomic PL+EN news publication on BriefRooms production."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CORE_PATHS = (
    "pl/index.html",
    "pl/aktualnosci.html",
    "en/index.html",
    "en/news.html",
)
LANGUAGE_PATHS = {
    "pl": {
        "index": "pl/index.html",
        "news": "pl/aktualnosci.html",
        "home": "pl/home_brief.json",
        "archive": "data/permanent_briefs_pl.json",
    },
    "en": {
        "index": "en/index.html",
        "news": "en/news.html",
        "home": "en/home_brief.json",
        "archive": "data/permanent_briefs_en.json",
    },
}


class VerificationError(RuntimeError):
    pass


def fetch(base_url: str, path: str, cache_key: str, attempt: int, timeout: float) -> bytes:
    target = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    separator = "&" if "?" in target else "?"
    url = f"{target}{separator}{urlencode({'publication': cache_key, 'attempt': attempt})}"
    request = Request(
        url,
        headers={
            "Accept": "*/*",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "BriefRooms-news-publication-verifier/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        status = int(getattr(response, "status", 200))
        if status != 200:
            raise VerificationError(f"{path} returned HTTP {status}")
        return response.read()


def load_local_json(path: str) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise VerificationError(f"{path} is not a JSON object")
    return value


def marker(html_source: str) -> str:
    match = re.search(
        r'<meta\s+name=["\']briefrooms-news-publication["\']\s+'
        r'content=["\']([^"\']+)["\']',
        html_source,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


def parse_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise VerificationError(f"invalid publication timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise VerificationError(f"publication timestamp has no timezone: {value!r}")
    return parsed


def verify_attempt(
    base_url: str,
    publication_id: str,
    expected_sha: str,
    attempt: int,
    timeout: float,
) -> dict[str, Any]:
    local_manifest = load_local_json("data/news_publication_status.json")
    remote: dict[str, bytes] = {}
    paths = list(CORE_PATHS) + [
        "data/news_publication_status.json",
        "data/news_source_report.json",
        "pl/home_brief.json",
        "en/home_brief.json",
    ]
    for path in paths:
        remote[path] = fetch(base_url, path, expected_sha, attempt, timeout)

    production_manifest = json.loads(
        remote["data/news_publication_status.json"].decode("utf-8")
    )
    if production_manifest != local_manifest:
        raise VerificationError("production publication manifest does not match current main")
    if production_manifest.get("publication_id") != publication_id:
        raise VerificationError(
            "production still serves publication "
            f"{production_manifest.get('publication_id')!r}, expected {publication_id!r}"
        )

    result: dict[str, Any] = {
        "publication_id": publication_id,
        "expected_sha": expected_sha,
        "core_urls": {},
        "languages": {},
    }
    for path in CORE_PATHS:
        expected = (ROOT / path).read_bytes()
        if remote[path] != expected:
            raise VerificationError(f"/{path} does not match the publication commit")
        html_source = remote[path].decode("utf-8")
        if marker(html_source) != publication_id:
            raise VerificationError(f"/{path} has an obsolete or missing publication marker")
        result["core_urls"][f"/{path}"] = 200

    for lang, paths_for_lang in LANGUAGE_PATHS.items():
        manifest_lang = production_manifest.get(lang) or {}
        if manifest_lang.get("status") not in {"fresh", "no-new-stories"}:
            raise VerificationError(f"{lang} has invalid status {manifest_lang.get('status')!r}")
        parse_time(manifest_lang.get("last_successful_fetch_at"))
        if manifest_lang.get("last_new_publication_at"):
            parse_time(manifest_lang["last_new_publication_at"])

        home = json.loads(remote[paths_for_lang["home"]].decode("utf-8"))
        if parse_time(home.get("updated_at")) != parse_time(
            manifest_lang.get("homepage_updated_at")
        ):
            raise VerificationError(f"{lang} homepage timestamp differs from the manifest")
        latest = list(home.get("latest") or [])
        if len(latest) < 3:
            raise VerificationError(f"{lang} homepage exposes fewer than three latest stories")
        index_source = html.unescape(
            remote[paths_for_lang["index"]].decode("utf-8")
        )
        latest_titles = [str(item.get("title") or "").strip() for item in latest[:3]]
        if not all(title and title in index_source for title in latest_titles):
            raise VerificationError(f"{lang} latest titles are missing from the production homepage")

        permanent_paths = [
            str(item.get("permalink") or "")
            for item in latest[:3]
        ]
        if len(permanent_paths) < 3 or not all(permanent_paths):
            raise VerificationError(f"{lang} permanent brief archive has fewer than three links")
        for permanent_path in permanent_paths:
            fetch(base_url, permanent_path, expected_sha, attempt, timeout)

        result["languages"][lang] = {
            "status": manifest_lang["status"],
            "latest_titles": latest_titles,
            "permanent_links": permanent_paths,
        }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication-id", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--base-url", default="https://briefrooms.com")
    parser.add_argument("--attempts", type=int, default=60)
    parser.add_argument("--interval", type=float, default=15)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    last_error = "production was not checked"
    for attempt in range(1, max(1, args.attempts) + 1):
        try:
            result = verify_attempt(
                args.base_url,
                args.publication_id,
                args.expected_sha,
                attempt,
                args.timeout,
            )
            result["status"] = "passed"
            result["verified_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except (
            VerificationError,
            HTTPError,
            URLError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < args.attempts:
                print(
                    f"Attempt {attempt}/{args.attempts}: {last_error}; retrying.",
                    file=sys.stderr,
                )
                time.sleep(max(0, args.interval))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "publication_id": args.publication_id,
                    "expected_sha": args.expected_sha,
                    "error": last_error,
                    "verified_at": datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(f"Production news verification failed: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
