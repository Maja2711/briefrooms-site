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


def validate(lang: str, max_age_minutes: int, now: datetime | None = None) -> None:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    news_rel, feed_rel, index_rel = LANG_PATHS[lang]
    news = (ROOT / news_rel).read_text(encoding="utf-8")
    feed = json.loads((ROOT / feed_rel).read_text(encoding="utf-8"))
    index = (ROOT / index_rel).read_text(encoding="utf-8")

    rendered_news_at = news_timestamp(news, lang)
    if lang == "en":
        if rendered_news_at.date() != now.date():
            raise AssertionError(
                f"en news is stale: rendered={rendered_news_at.date()}, expected={now.date()}"
            )
    else:
        assert_fresh(rendered_news_at, now, max_age_minutes, f"{lang} news")
    updated_at = parse_datetime(str(feed.get("updated_at", "")))
    assert_fresh(updated_at, now, max_age_minutes, f"{lang} homepage feed")

    latest = feed.get("latest")
    if feed.get("language") != lang or not isinstance(latest, list) or len(latest) < 3:
        raise AssertionError(f"{lang} homepage feed is incomplete")
    if int(feed.get("count", -1)) != len(latest):
        raise AssertionError(f"{lang} homepage count does not match latest items")

    news_cards = news.count('class="ai-note"')
    if news_cards < 3:
        raise AssertionError(f"{lang} news page has only {news_cards} rendered cards")

    marker = updated_at.isoformat(timespec="minutes")
    if f'data-home-updated-at="{marker}"' not in index:
        raise AssertionError(f"{lang} index.html does not contain the current homepage feed timestamp")

    for item in latest:
        permalink = str(item.get("permalink", ""))
        if not permalink.startswith(f"/{lang}/") or not (ROOT / permalink.lstrip("/")).is_file():
            raise AssertionError(f"{lang} permanent brief is missing: {permalink or '<empty>'}")

    print(
        f"OK {lang}: news={rendered_news_at.isoformat()}, "
        f"home={updated_at.isoformat()}, cards={news_cards}, briefs={len(latest)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", choices=sorted(LANG_PATHS), required=True)
    parser.add_argument("--max-age-minutes", type=int, default=360)
    args = parser.parse_args()
    validate(args.lang, args.max_age_minutes)


if __name__ == "__main__":
    main()
