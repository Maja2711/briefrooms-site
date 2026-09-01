#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "system" / "news_live_health.json"
LANGUAGES = {
    "pl": {
        "feed": Path("data/news/pl.json"),
        "news_page": Path("pl/aktualnosci.html"),
        "home_page": Path("pl/index.html"),
        "sections": {"polityka", "ekonomia", "zdrowie", "nauka", "sport"},
    },
    "en": {
        "feed": Path("data/news/en.json"),
        "news_page": Path("en/news.html"),
        "home_page": Path("en/index.html"),
        "sections": {
            "world-news", "asia-pacific", "europe", "middle-east",
            "business", "science", "health", "sport",
        },
    },
}
RUNTIME = "/scripts/news-live.js?v=5"
MARKER_META = 'name="briefrooms-live-news-marker" content="{}"'
FUTURE_TOLERANCE = timedelta(minutes=10)
MIN_SECTION = 9


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


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def story_complete(story: Any) -> bool:
    return isinstance(story, dict) and all(
        isinstance(story.get(field), str) and story[field].strip()
        for field in ("title", "link", "image", "source")
    )


def fetch(base_url: str, path: str, marker: str, attempt: int, timeout: float) -> bytes:
    target = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    separator = "&" if "?" in target else "?"
    url = f"{target}{separator}{urlencode({'news_health': marker, 'attempt': attempt})}"
    request = Request(
        url,
        headers={
            "User-Agent": "BriefRooms-live-news-watchdog/1.0",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        if int(getattr(response, "status", 200)) != 200:
            raise RuntimeError(f"{path} returned a non-200 status")
        return response.read()


def validate_local(root: Path, now: datetime, max_age_minutes: int) -> dict[str, Any]:
    status_path = root / "data/news/status.json"
    status = load_json(status_path)
    reasons: list[str] = []
    degraded: list[str] = []
    marker = str(status.get("marker") or "")
    generated = parse_time(status.get("generated_at"))
    age_minutes = None if generated is None else max(0, round((now - generated).total_seconds() / 60))

    if status.get("schema_version") != "news-live-status-v2":
        reasons.append("status_schema_mismatch")
    if not marker:
        reasons.append("status_marker_missing")
    if generated is None:
        reasons.append("status_timestamp_missing")
    elif age_minutes is not None and age_minutes > max_age_minutes:
        reasons.append("status_stale")

    language_report: dict[str, Any] = {}
    for lang, config in LANGUAGES.items():
        lang_reasons: list[str] = []
        lang_degraded: list[str] = []
        feed = load_json(root / config["feed"])
        if feed.get("schema_version") != "news-live-v2":
            lang_reasons.append("feed_schema_mismatch")
        if feed.get("language") != lang:
            lang_reasons.append("feed_language_mismatch")
        if feed.get("marker") != marker:
            lang_reasons.append("feed_marker_mismatch")
        feed_generated = parse_time(feed.get("generated_at"))
        if feed_generated is None:
            lang_reasons.append("feed_timestamp_missing")
        elif generated and abs((feed_generated - generated).total_seconds()) > 1:
            lang_reasons.append("feed_timestamp_mismatch")
        elif now - feed_generated > timedelta(minutes=max_age_minutes):
            lang_reasons.append("feed_stale")

        sections = feed.get("sections") if isinstance(feed.get("sections"), dict) else {}
        if set(sections) != config["sections"]:
            lang_reasons.append("section_set_mismatch")
        counts: dict[str, int] = {}
        for section_id in config["sections"]:
            stories = sections.get(section_id) if isinstance(sections.get(section_id), list) else []
            counts[section_id] = len(stories)
            if len(stories) < MIN_SECTION:
                lang_reasons.append(f"section_too_small:{section_id}")
            if any(not story_complete(story) for story in stories):
                lang_reasons.append(f"incomplete_story:{section_id}")
            for story in stories:
                published = parse_time(story.get("published_at")) if isinstance(story, dict) else None
                if published and published > now + FUTURE_TOLERANCE:
                    lang_reasons.append(f"future_story_timestamp:{section_id}")
                    break

        feed_health = feed.get("health") if isinstance(feed.get("health"), dict) else {}
        if feed_health.get("status") == "degraded":
            lang_degraded.append("source_degraded")
        if feed_health.get("source_errors"):
            lang_degraded.append("source_errors")

        expected_marker = MARKER_META.format(marker)
        for page_key in ("news_page", "home_page"):
            try:
                page = (root / config[page_key]).read_text(encoding="utf-8")
            except OSError:
                lang_reasons.append(f"missing_{page_key}")
                continue
            if RUNTIME not in page:
                lang_reasons.append(f"runtime_missing:{page_key}")
            if expected_marker not in page:
                lang_reasons.append(f"static_marker_mismatch:{page_key}")

        reasons.extend(f"{lang}:{item}" for item in lang_reasons)
        degraded.extend(f"{lang}:{item}" for item in lang_degraded)
        language_report[lang] = {
            "status": "failed" if lang_reasons else ("degraded" if lang_degraded else "healthy"),
            "reasons": lang_reasons,
            "warnings": lang_degraded,
            "section_counts": counts,
            "feed_generated_at": feed.get("generated_at"),
        }

    return {
        "marker": marker,
        "generated_at": status.get("generated_at"),
        "age_minutes": age_minutes,
        "reasons": reasons,
        "warnings": degraded,
        "languages": language_report,
    }


def validate_production(
    base_url: str,
    marker: str,
    *,
    attempts: int,
    interval: float,
    timeout: float,
) -> tuple[bool, list[str]]:
    if not base_url:
        return True, []
    paths = ["data/news/status.json", "data/news/pl.json", "data/news/en.json"]
    pages = ["pl/aktualnosci.html", "en/news.html", "pl/index.html", "en/index.html"]
    last: list[str] = []
    for attempt in range(1, max(1, attempts) + 1):
        last = []
        for path in paths:
            try:
                payload = json.loads(fetch(base_url, path, marker, attempt, timeout).decode("utf-8"))
                if payload.get("marker") != marker:
                    last.append(f"production_marker_mismatch:{path}")
            except (HTTPError, URLError, TimeoutError, RuntimeError, ValueError) as exc:
                last.append(f"production_fetch_failed:{path}:{type(exc).__name__}")
        expected_marker = MARKER_META.format(marker)
        for path in pages:
            try:
                source = fetch(base_url, path, marker, attempt, timeout).decode("utf-8", "replace")
                if RUNTIME not in source:
                    last.append(f"production_runtime_missing:{path}")
                if expected_marker not in source:
                    last.append(f"production_static_marker_mismatch:{path}")
            except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
                last.append(f"production_fetch_failed:{path}:{type(exc).__name__}")
        if not last:
            return True, []
        if attempt < attempts:
            time.sleep(max(0, interval))
    return False, last


def assess(
    root: Path,
    *,
    now: datetime,
    max_age_minutes: int,
    base_url: str = "",
    attempts: int = 1,
    interval: float = 0,
    timeout: float = 15,
) -> dict[str, Any]:
    local = validate_local(root, now, max_age_minutes)
    production_ok = False
    production_reasons: list[str] = []
    if not local["reasons"]:
        production_ok, production_reasons = validate_production(
            base_url,
            local["marker"],
            attempts=attempts,
            interval=interval,
            timeout=timeout,
        )
    elif not base_url:
        production_ok = True
    reasons = list(local["reasons"]) + production_reasons
    status = "failed" if reasons else ("degraded" if local["warnings"] else "healthy")
    return {
        "schema_version": "news-live-health-v1",
        "checked_at": now.isoformat(timespec="seconds"),
        "status": status,
        "max_age_minutes": max_age_minutes,
        "marker": local["marker"],
        "published_at": local["generated_at"],
        "age_minutes": local["age_minutes"],
        "production_checked": bool(base_url),
        "production_matches": production_ok if base_url else None,
        "reasons": reasons,
        "warnings": local["warnings"],
        "languages": local["languages"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--max-age-minutes", type=int, default=120)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--interval", type=float, default=0)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--now")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise SystemExit("--now must be ISO-8601")
    report = assess(
        args.root.resolve(),
        now=now,
        max_age_minutes=args.max_age_minutes,
        base_url=args.base_url,
        attempts=args.attempts,
        interval=args.interval,
        timeout=args.timeout,
    )
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False))
    return 1 if args.strict and report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
