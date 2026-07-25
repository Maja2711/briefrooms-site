#!/usr/bin/env python3
"""Fail closed unless news and homepage briefs were published as one fresh unit."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANG_PATHS = {
    "pl": ("pl/aktualnosci.html", "pl/home_brief.json", "pl/index.html"),
    "en": ("en/news.html", "en/home_brief.json", "en/index.html"),
}
ALLOWED_HOMEPAGE_COUNTS = {8, 10}


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def assert_fresh(value: datetime, now: datetime, max_age_minutes: int, label: str) -> None:
    age = (now - value).total_seconds() / 60
    if age < -10 or age > max_age_minutes:
        raise AssertionError(f"{label} is stale: age={age:.1f} minutes")


def news_timestamp(source: str, lang: str) -> datetime:
    if lang == "pl":
        match = re.search(r'Ostatnia aktualizacja:\s*<time datetime="([^"]+)"', source)
        if not match:
            raise AssertionError("PL news update timestamp is missing")
        return parse_datetime(match.group(1))
    match = re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", source)
    if not match:
        raise AssertionError("EN news update date is missing")
    return datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc)


def homepage_timestamp(source: str, lang: str) -> datetime:
    match = re.search(
        r'<div\b(?=[^>]*\bid=["\']latest-briefs["\'])'
        r'[^>]*\bdata-home-updated-at=["\']([^"\']+)["\']',
        source,
        flags=re.IGNORECASE,
    )
    if not match:
        raise AssertionError(f"{lang} index.html homepage timestamp is missing")
    return parse_datetime(match.group(1))


def rendered_home_count(source: str, lang: str) -> int:
    start = "<!-- HOME_BRIEFS_START -->"
    end = "<!-- HOME_BRIEFS_END -->"
    before, separator, remainder = source.partition(start)
    if not separator:
        raise AssertionError(f"{lang} homepage start marker is missing")
    block, separator, _ = remainder.partition(end)
    if not separator:
        raise AssertionError(f"{lang} homepage end marker is missing")
    return len(re.findall(r'<a\s+class="brief-card"\s+href=', block, flags=re.IGNORECASE))


def validate(lang: str, max_age_minutes: int, now: datetime | None = None) -> None:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    news_rel, feed_rel, index_rel = LANG_PATHS[lang]
    news = (ROOT / news_rel).read_text(encoding="utf-8")
    feed = json.loads((ROOT / feed_rel).read_text(encoding="utf-8"))
    index = (ROOT / index_rel).read_text(encoding="utf-8")

    rendered_news_at = news_timestamp(news, lang)
    if lang == "en":
        if rendered_news_at.date() != now.date():
            raise AssertionError(f"en news is stale: rendered={rendered_news_at.date()}, expected={now.date()}")
    else:
        assert_fresh(rendered_news_at, now, max_age_minutes, f"{lang} news")
    updated_at = parse_datetime(str(feed.get("updated_at", "")))
    assert_fresh(updated_at, now, max_age_minutes, f"{lang} homepage feed")

    latest = feed.get("latest")
    if feed.get("language") != lang or not isinstance(latest, list):
        raise AssertionError(f"{lang} homepage feed is incomplete")
    if len(latest) not in ALLOWED_HOMEPAGE_COUNTS:
        raise AssertionError(f"{lang} homepage must contain exactly 8 or 10 briefs; got {len(latest)}")
    if int(feed.get("count", -1)) != len(latest):
        raise AssertionError(f"{lang} homepage count does not match latest items")

    rendered_count = rendered_home_count(index, lang)
    if rendered_count not in ALLOWED_HOMEPAGE_COUNTS:
        raise AssertionError(f"{lang} rendered homepage must contain exactly 8 or 10 briefs; got {rendered_count}")
    if rendered_count != len(latest):
        raise AssertionError(f"{lang} rendered homepage count {rendered_count} does not match feed {len(latest)}")

    news_cards = news.count('class="ai-note"')
    if news_cards < 3:
        raise AssertionError(f"{lang} news page has only {news_cards} rendered cards")

    rendered_home_at = homepage_timestamp(index, lang)
    if rendered_home_at != updated_at:
        raise AssertionError(f"{lang} index.html does not contain the current homepage feed timestamp")

    for item in latest:
        permalink = str(item.get("permalink", ""))
        if not permalink.startswith(f"/{lang}/") or not (ROOT / permalink.lstrip("/")).is_file():
            raise AssertionError(f"{lang} permanent brief is missing: {permalink or '<empty>'}")
        image = str(item.get("image", "")).strip()
        if not re.match(r"^https?://[^\s]+$", image, flags=re.IGNORECASE):
            raise AssertionError(
                f"{lang} homepage brief has no publishable source image: "
                f"{item.get('title') or permalink or '<untitled>'}"
            )

    print(f"OK {lang}: news={rendered_news_at.isoformat()}, home={updated_at.isoformat()}, cards={news_cards}, briefs={len(latest)}, rendered={rendered_count}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", choices=sorted(LANG_PATHS), required=True)
    parser.add_argument("--max-age-minutes", type=int, default=360)
    args = parser.parse_args()
    validate(args.lang, args.max_age_minutes)


if __name__ == "__main__":
    main()
