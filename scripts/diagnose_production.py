#!/usr/bin/env python3
"""Capture HTTP diagnostics for BriefRooms production and GitHub Pages endpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TARGETS = {
    "custom_version": "https://briefrooms.com/build-version.json",
    "custom_pl": "https://briefrooms.com/pl/",
    "custom_en": "https://briefrooms.com/en/",
    "pages_version": "https://maja2711.github.io/briefrooms-site/build-version.json",
    "pages_pl": "https://maja2711.github.io/briefrooms-site/pl/",
    "pages_en": "https://maja2711.github.io/briefrooms-site/en/",
}

HEADER_NAMES = (
    "server",
    "via",
    "age",
    "cache-control",
    "content-type",
    "etag",
    "last-modified",
    "location",
    "x-cache",
    "x-cache-hits",
    "x-fastly-request-id",
    "x-github-request-id",
    "x-served-by",
    "x-timer",
)


def inspect_url(url: str, *, timeout: float) -> dict:
    request = Request(
        url,
        headers={
            "Accept": "*/*",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "BriefRooms-production-diagnostic/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            headers = response.headers
            return response_record(
                requested_url=url,
                final_url=response.geturl(),
                status=getattr(response, "status", 200),
                headers=headers,
                body=body,
            )
    except HTTPError as exc:
        body = exc.read()
        return response_record(
            requested_url=url,
            final_url=exc.geturl(),
            status=exc.code,
            headers=exc.headers,
            body=body,
            error=f"HTTPError: {exc}",
        )
    except (URLError, TimeoutError, OSError) as exc:
        return {
            "requested_url": url,
            "error": f"{type(exc).__name__}: {exc}",
        }


def response_record(
    *,
    requested_url: str,
    final_url: str,
    status: int,
    headers,
    body: bytes,
    error: str | None = None,
) -> dict:
    text = body.decode("utf-8", errors="replace")
    selected_headers = {
        name: headers.get(name)
        for name in HEADER_NAMES
        if headers.get(name) is not None
    }
    record = {
        "requested_url": requested_url,
        "final_url": final_url,
        "status": status,
        "headers": selected_headers,
        "body_bytes": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_prefix": text[:500],
        "has_static_brief_marker": "<!-- HOME_BRIEFS_START -->" in text,
        "has_static_brief_card": 'class="brief-card"' in text,
        "has_stale_pl_placeholder": "Karty uzupełnią się po wczytaniu home_brief.json" in text,
        "has_stale_en_placeholder": "The cards update when home_brief.json loads" in text,
    }
    if error:
        record["error"] = error
    try:
        record["json"] = json.loads(text)
    except json.JSONDecodeError:
        pass
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/production_diagnostic.json")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "targets": {
            name: inspect_url(url, timeout=args.timeout)
            for name, url in TARGETS.items()
        },
    }
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
