#!/usr/bin/env python3
"""Shared Hot X validation, URL cleanup and unique-item selection."""
from __future__ import annotations

import re
import unicodedata
import urllib.parse
from collections import Counter
from typing import Iterable, Sequence

TOTAL_ITEMS = 8
INITIAL_VISIBLE_ITEMS = 2
MAX_ITEMS_PER_CATEGORY = 2

X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
TRACKING_PARAMS = {
    "src",
    "ref",
    "ref_src",
    "s",
    "t",
    "twclid",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}
DIRECT_POST = re.compile(r"^/[^/\s]+/status/\d+(?:/)?$", re.I)
SEARCH_PATH = re.compile(r"^/(?:search|explore)(?:/)?$", re.I)


def normalize_title(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", text)


def clean_x_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in X_HOSTS:
        return ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
    if not (DIRECT_POST.match(path) or SEARCH_PATH.match(path)):
        return ""
    kept = []
    for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=False):
        if key.casefold() in TRACKING_PARAMS or key.casefold().startswith("utm_"):
            continue
        kept.append((key, re.sub(r"\s+", " ", item).strip()))
    query = urllib.parse.urlencode(sorted(kept), doseq=True)
    return urllib.parse.urlunsplit(("https", "x.com", path, query, ""))


def is_direct_post(value: object) -> bool:
    cleaned = clean_x_url(value)
    return bool(cleaned and DIRECT_POST.match(urllib.parse.urlsplit(cleaned).path))


def item_url(item: dict) -> str:
    tweet = clean_x_url(item.get("tweet_url"))
    if is_direct_post(tweet):
        return tweet
    return clean_x_url(item.get("search_url"))


def comment_text(item: dict, lang: str) -> str:
    return str(item.get(f"comment_{lang}") or item.get(f"summary_{lang}") or "").strip()


def valid_item(item: object, *, substantive: bool = False) -> bool:
    if not isinstance(item, dict) or not item_url(item):
        return False
    if not str(item.get("title_pl") or "").strip() or not str(item.get("title_en") or "").strip():
        return False
    for lang in ("pl", "en"):
        comment = re.sub(r"\s+", " ", comment_text(item, lang)).strip()
        if not comment:
            return False
        if substantive and (len(comment) < 100 or len(comment.split()) < 14):
            return False
    return True


def fingerprints(item: dict) -> set[str]:
    values: set[str] = set()
    for field in ("tweet_url", "search_url"):
        url = clean_x_url(item.get(field))
        if url:
            values.add(f"url:{url}")
    for field in ("title_en", "title_pl"):
        title = normalize_title(item.get(field))
        if title:
            values.add(f"{field}:{title}")
    return values


def _ordered_group(items: Iterable[dict]) -> list[dict]:
    indexed = list(enumerate(items))
    indexed.sort(key=lambda pair: (not is_direct_post(pair[1].get("tweet_url")), pair[0]))
    return [item for _, item in indexed]


def select_unique(
    groups: Sequence[Iterable[dict]],
    *,
    target: int = TOTAL_ITEMS,
    max_per_category: int = MAX_ITEMS_PER_CATEGORY,
    substantive: bool = False,
) -> list[dict]:
    """Merge groups in priority order and reject any repeated URL or title."""
    selected: list[dict] = []
    seen: set[str] = set()
    category_counts: Counter[str] = Counter()
    for group in groups:
        for source_item in _ordered_group(group):
            if not valid_item(source_item, substantive=substantive):
                continue
            item = dict(source_item)
            item["tweet_url"] = clean_x_url(item.get("tweet_url"))
            item["search_url"] = clean_x_url(item.get("search_url"))
            ids = fingerprints(item)
            if not ids or ids & seen:
                continue
            category = normalize_title(item.get("category")) or "other"
            if category_counts[category] >= max_per_category:
                continue
            selected.append(item)
            seen.update(ids)
            category_counts[category] += 1
            if len(selected) >= target:
                return selected
    return selected


def duplicate_free(items: Sequence[dict]) -> bool:
    return len(select_unique([items], target=len(items))) == len(items)
