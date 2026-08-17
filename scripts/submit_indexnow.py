#!/usr/bin/env python3
"""Submit recently changed public BriefRooms URLs to IndexNow.

The script is deliberately conservative: it maps only public PL/EN HTML files to
URLs and never submits arbitrary repository paths. A sitemap change additionally
submits the two language homepages. Submission status is written to
``data/indexnow_status.json`` for auditability.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = ROOT / "data" / "indexnow_status.json"
HOST = "briefrooms.com"
BASE_URL = f"https://{HOST}"
KEY = "c5b0d236f963163a96596ec8d463a402"
KEY_LOCATION = f"{BASE_URL}/{KEY}.txt"
ENDPOINT = "https://api.indexnow.org/indexnow"


def public_url_for_path(raw_path: str) -> str | None:
    path = raw_path.strip().replace("\\", "/").lstrip("./")
    if path in {"pl/index.html", "en/index.html"}:
        lang = path.split("/", 1)[0]
        return f"{BASE_URL}/{lang}/"
    if (path.startswith("pl/") or path.startswith("en/")) and path.endswith(".html"):
        return f"{BASE_URL}/{path}"
    return None


def collect_urls(paths: list[str]) -> list[str]:
    urls: list[str] = []
    for path in paths:
        url = public_url_for_path(path)
        if url:
            urls.append(url)
        elif path.strip().replace("\\", "/").lstrip("./") == "sitemap.xml":
            urls.extend([f"{BASE_URL}/", f"{BASE_URL}/pl/", f"{BASE_URL}/en/"])
    return list(dict.fromkeys(urls))


def submit(urls: list[str]) -> tuple[int, str]:
    payload = json.dumps(
        {
            "host": HOST,
            "key": KEY,
            "keyLocation": KEY_LOCATION,
            "urlList": urls,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "BriefRooms-IndexNow/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
            return int(response.status), body[-2000:]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return int(exc.code), body[-2000:]


def write_status(*, urls: list[str], code: int | None, response: str, result: str) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(
        json.dumps(
            {
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "endpoint": ENDPOINT,
                "host": HOST,
                "key_location": KEY_LOCATION,
                "url_count": len(urls),
                "urls": urls,
                "http_code": code,
                "result": result,
                "response": response,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", help="Repository paths changed since the last scan")
    parser.add_argument("--paths-file", type=Path)
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Also submit the root and PL/EN homepages when enabling IndexNow",
    )
    args = parser.parse_args()

    paths = list(args.paths)
    if args.paths_file and args.paths_file.exists():
        paths.extend(args.paths_file.read_text(encoding="utf-8").splitlines())
    urls = collect_urls(paths)
    if args.bootstrap:
        urls = list(dict.fromkeys([f"{BASE_URL}/", f"{BASE_URL}/pl/", f"{BASE_URL}/en/", *urls]))

    if not urls:
        write_status(urls=[], code=None, response="No eligible changed public URLs", result="no_changes")
        print("IndexNow: no eligible changed public URLs")
        return 0

    code, response = submit(urls)
    accepted = code in {200, 202}
    write_status(
        urls=urls,
        code=code,
        response=response,
        result="accepted" if accepted else "failed",
    )
    print(f"IndexNow: HTTP {code}; submitted {len(urls)} URL(s)")
    for url in urls:
        print(f" - {url}")
    return 0 if accepted else 1


if __name__ == "__main__":
    sys.exit(main())
