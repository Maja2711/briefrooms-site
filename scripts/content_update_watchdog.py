#!/usr/bin/env python3
"""Audit atomic news freshness and production parity without changing content."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
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
        "home": Path("pl/home_brief.json"),
        "news": Path("pl/aktualnosci.html"),
    },
    "en": {
        "home": Path("en/home_brief.json"),
        "news": Path("en/news.html"),
    },
}


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_hours(value: Any, now: datetime) -> float | None:
    parsed = parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 3600)


def home_timestamp(root: Path, lang: str) -> datetime | None:
    return parse_time(load_json(root / LANGUAGE_PATHS[lang]["home"]).get("updated_at"))


def news_timestamp(root: Path, lang: str) -> datetime | None:
    try:
        source = (root / LANGUAGE_PATHS[lang]["news"]).read_text(encoding="utf-8")
    except OSError:
        return None
    marker = re.search(
        r'<meta\s+name=["\']briefrooms-news-updated-at["\']\s+content=["\']([^"\']+)',
        source,
        flags=re.IGNORECASE,
    )
    if marker:
        return parse_time(marker.group(1))
    if lang == "pl":
        match = re.search(r'Ostatnia aktualizacja:\s*<time datetime="([^"]+)"', source)
        return parse_time(match.group(1)) if match else None
    match = re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", source)
    return parse_time(match.group(1)) if match else None


def equal_time(first: Any, second: datetime | None) -> bool:
    parsed = parse_time(first)
    return parsed is not None and second is not None and parsed == second


def fetch(base_url: str, path: str, cache_key: str, attempt: int, timeout: float) -> bytes:
    target = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    separator = "&" if "?" in target else "?"
    url = f"{target}{separator}{urlencode({'watchdog': cache_key, 'attempt': attempt})}"
    request = Request(
        url,
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "BriefRooms-content-watchdog/2.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        if int(getattr(response, "status", 200)) != 200:
            raise RuntimeError(f"{path} did not return HTTP 200")
        return response.read()


def production_matches_main(
    root: Path,
    base_url: str,
    publication_id: str,
    attempts: int,
    interval: float,
    timeout: float,
) -> tuple[bool, str]:
    required = list(CORE_PATHS) + [
        "data/news_publication_status.json",
        "data/news_source_report.json",
        "pl/home_brief.json",
        "en/home_brief.json",
    ]
    last_error = "production check did not run"
    for attempt in range(1, max(1, attempts) + 1):
        try:
            for path in required:
                remote = fetch(base_url, path, publication_id, attempt, timeout)
                local_path = root / path
                if not local_path.is_file() or remote != local_path.read_bytes():
                    raise RuntimeError(f"/{path} differs from current main")
            return True, ""
        except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts:
                time.sleep(max(0, interval))
    return False, last_error


def assess_health(
    root: Path,
    *,
    now: datetime,
    base_url: str = "",
    attempts: int = 1,
    interval: float = 0,
    timeout: float = 20,
) -> dict[str, Any]:
    contract = load_json(root / "data/content_update_contract.json")
    manifest = load_json(root / "data/news_publication_status.json")
    watch = contract.get("watchdog") or {}
    fetch_limit = float(watch.get("successful_fetch_stale_after_hours", 8))
    successful_publication_limit = float(
        watch.get("successful_publication_stale_after_hours", 8)
    )
    publication_limit = float(watch.get("new_publication_stale_after_hours", 24))
    publication_id = str(manifest.get("publication_id") or "")
    report: dict[str, Any] = {
        "checked_at": now.isoformat(timespec="seconds"),
        "contract_version": contract.get("contract_version"),
        "publication_id": publication_id,
        "languages": {},
    }

    atomic_manifest_valid = bool(publication_id) and all(
        isinstance(manifest.get(lang), dict) for lang in ("pl", "en")
    )
    for lang in ("pl", "en"):
        value = manifest.get(lang) if isinstance(manifest.get(lang), dict) else {}
        fetch_age = age_hours(value.get("last_successful_fetch_at"), now)
        successful_publication_age = age_hours(
            value.get("last_successful_publication_at"), now
        )
        publication_age = age_hours(value.get("last_new_publication_at"), now)
        home_matches = equal_time(value.get("homepage_updated_at"), home_timestamp(root, lang))
        news_matches = equal_time(value.get("news_page_updated_at"), news_timestamp(root, lang))
        status_valid = value.get("status") in {"fresh", "no-new-stories"}
        served_last_good = bool(value.get("served_from_last_good"))
        stale_reasons: list[str] = []
        if fetch_age is None or fetch_age > fetch_limit:
            stale_reasons.append("successful_fetch_stale")
        if (
            successful_publication_age is None
            or successful_publication_age > successful_publication_limit
        ):
            stale_reasons.append("successful_publication_stale")
        if publication_age is None or publication_age > publication_limit:
            stale_reasons.append("new_publication_stale")
        if not status_valid:
            stale_reasons.append("invalid_status")
        if served_last_good:
            stale_reasons.append("served_from_last_good")
        if not home_matches:
            stale_reasons.append("homepage_timestamp_mismatch")
        if not news_matches:
            stale_reasons.append("news_timestamp_mismatch")
        report["languages"][lang] = {
            "status": value.get("status") or "missing",
            "successful_fetch_age_hours": None if fetch_age is None else round(fetch_age, 3),
            "successful_publication_age_hours": (
                None
                if successful_publication_age is None
                else round(successful_publication_age, 3)
            ),
            "new_publication_age_hours": (
                None if publication_age is None else round(publication_age, 3)
            ),
            "homepage_timestamp_matches": home_matches,
            "news_timestamp_matches": news_matches,
            "stale": bool(stale_reasons),
            "reasons": stale_reasons,
        }

    production_match = None
    production_error = ""
    if base_url and atomic_manifest_valid:
        production_match, production_error = production_matches_main(
            root,
            base_url,
            publication_id,
            attempts,
            interval,
            timeout,
        )
    elif base_url:
        production_match = False
        production_error = "atomic publication manifest is missing"

    report["atomic_manifest_valid"] = atomic_manifest_valid
    report["production_matches_main"] = production_match
    report["production_error"] = production_error
    report["production_mismatch"] = production_match is False
    report["recovery_needed"] = (
        not atomic_manifest_valid
        or any(report["languages"][lang]["stale"] for lang in ("pl", "en"))
        or production_match is False
    )
    report["status"] = "recovery_needed" if report["recovery_needed"] else "healthy"
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--interval", type=float, default=0)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--now")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print("--now must be an ISO-8601 timestamp", file=sys.stderr)
        return 2
    report = assess_health(
        root,
        now=now,
        base_url=args.base_url,
        attempts=args.attempts,
        interval=args.interval,
        timeout=args.timeout,
    )
    output_path = args.output or (root / "data/content_update_health.json")
    write_json(output_path, report)
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(
                f"recovery_needed={'true' if report['recovery_needed'] else 'false'}\n"
            )
            for lang in ("pl", "en"):
                handle.write(
                    f"{lang}_stale="
                    f"{'true' if report['languages'][lang]['stale'] else 'false'}\n"
                )
            handle.write(
                "production_mismatch="
                f"{'true' if report['production_mismatch'] else 'false'}\n"
            )
    print(json.dumps(report, ensure_ascii=False))
    return 1 if args.strict and report["recovery_needed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
