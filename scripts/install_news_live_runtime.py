#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    ROOT / "pl" / "aktualnosci.html",
    ROOT / "en" / "news.html",
    ROOT / "pl" / "index.html",
    ROOT / "en" / "index.html",
]
VERSION = "5"
TAG = f'<script src="/scripts/news-live.js?v={VERSION}" defer></script>'
PATTERN = re.compile(r'\s*<script\s+src=["\']/scripts/news-live\.js(?:\?[^"\']*)?["\']\s+defer></script>', re.I)
HOME_MAX_AGE = timedelta(days=3)
FUTURE_TOLERANCE = timedelta(minutes=10)
HOME_BLOCK = re.compile(
    r'(<!--\s*HOME_BRIEFS_START\s*-->)(.*?)(<!--\s*HOME_BRIEFS_END\s*-->)',
    re.I | re.S,
)
CARD_OPEN = re.compile(r'<a\b(?=[^>]*\bclass=["\'][^"\']*\bbrief-card\b[^"\']*["\'])[^>]*>', re.I)
HREF = re.compile(r'\bhref=["\']([^"\']+)["\']', re.I)
POLICY_ATTRS = (
    re.compile(r'\s+hidden(?=\s|>)', re.I),
    re.compile(r'\s+aria-hidden=["\'][^"\']*["\']', re.I),
    re.compile(r'\s+data-home-stale=["\'][^"\']*["\']', re.I),
    re.compile(r'\s+data-home-published-at=["\'][^"\']*["\']', re.I),
)


def _parse_published(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_fresh(value: Any, now: datetime) -> tuple[bool, str]:
    published = _parse_published(value)
    if published is None:
        return False, ""
    age = now - published
    fresh = -FUTURE_TOLERANCE <= age <= HOME_MAX_AGE
    return fresh, published.isoformat(timespec="seconds")


def _homepage_publication_map(lang: str) -> dict[str, Any]:
    path = ROOT / lang / "home_brief.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[str, Any] = {}
    for section in ("latest", "radar"):
        items = payload.get(section)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            permalink = str(item.get("permalink") or "").strip()
            if permalink:
                result[permalink] = item.get("published_at")
    return result


def _with_policy_attrs(opening: str, published_at: Any, now: datetime) -> str:
    cleaned = opening
    for pattern in POLICY_ATTRS:
        cleaned = pattern.sub("", cleaned)
    fresh, normalized = _is_fresh(published_at, now)
    attrs = []
    if normalized:
        attrs.append(f'data-home-published-at="{normalized}"')
    if not fresh:
        attrs.extend(('hidden', 'aria-hidden="true"', 'data-home-stale="true"'))
    if not attrs:
        return cleaned
    return cleaned[:-1].rstrip() + " " + " ".join(attrs) + ">"


def apply_homepage_freshness(source: str, lang: str, now: datetime | None = None) -> str:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    publication_map = _homepage_publication_map(lang)

    def update_block(match: re.Match[str]) -> str:
        block = match.group(2)

        def update_card(card_match: re.Match[str]) -> str:
            opening = card_match.group(0)
            href_match = HREF.search(opening)
            href = href_match.group(1) if href_match else ""
            return _with_policy_attrs(opening, publication_map.get(href), current)

        updated = CARD_OPEN.sub(update_card, block)
        return match.group(1) + updated + match.group(3)

    source = HOME_BLOCK.sub(update_block, source, count=1)
    container_pattern = re.compile(r'<div\b(?=[^>]*\bid=["\']latest-briefs["\'])[^>]*>', re.I)

    def mark_container(match: re.Match[str]) -> str:
        opening = re.sub(r'\s+data-home-freshness-policy=["\'][^"\']*["\']', "", match.group(0), flags=re.I)
        return opening[:-1].rstrip() + ' data-home-freshness-policy="max-72h-v1">'

    return container_pattern.sub(mark_container, source, count=1)


def install(path: Path) -> bool:
    old = path.read_text(encoding="utf-8")
    new = PATTERN.sub("", old)
    if path.name == "index.html" and path.parent.name in {"pl", "en"}:
        new = apply_homepage_freshness(new, path.parent.name)
    if "</body>" not in new:
        raise RuntimeError(f"closing body tag missing: {path.relative_to(ROOT)}")
    new = new.replace("</body>", TAG + "\n</body>", 1)
    if new == old:
        return False
    path.write_text(new, encoding="utf-8", newline="\n")
    print(f"installed live news runtime in {path.relative_to(ROOT)}")
    return True


def main() -> None:
    for path in PAGES:
        install(path)


if __name__ == "__main__":
    main()
