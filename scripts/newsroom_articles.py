#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared full-article comment pipeline for PL and EN section news pages.

The strict mode reuses or generates article-derived comments and returns only
independently approved items. The source-first mode is used by the broad News
pages: it selects a bounded set of source cards without spending AI quota, so a
comment provider cannot block publication. Existing approved reserve comments
remain attached, while fresh cards contain only title, source, image and link.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from comment_quality import QUALITY_STATUS, QUALITY_VERSION, validate_comment
from read_and_summarize_articles import (
    MIN_ARTICLE_CHARS,
    ai_summarize_batch,
    fetch_article_text,
    load_cache,
    save_cache,
)
from news_story_dedupe import _canonical_url

ROOT = Path(__file__).resolve().parents[1]
HOME_FILES = {
    "pl": ROOT / "pl" / "home_brief.json",
    "en": ROOT / "en" / "home_brief.json",
}
MIN_APPROVED_PER_SECTION = 6
APPROVED_TARGET_PER_SECTION = 8
BASE_NEW_ITEMS_PER_SECTION = 4
REJECTION_RESERVE = 3
MAX_NEW_ITEMS_PER_SECTION = 18


def round_robin_section_items(
    sections: dict[str, list[dict]],
):
    """Yield one candidate per section per round to keep AI capacity balanced."""
    longest = max((len(items) for items in sections.values()), default=0)
    for index in range(longest):
        for section_key, items in sections.items():
            if index < len(items):
                yield section_key, items[index]


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def approved_homepage_comments(lang: str) -> dict[str, str]:
    """Return validated homepage comments keyed by the exact source URL."""
    data = _read_json(HOME_FILES[lang], {})
    result: dict[str, str] = {}
    for section in ("latest", "radar"):
        for item in data.get(section, []) or []:
            link = str(item.get("link") or "").strip()
            text = str(item.get("full_brief") or "").strip()
            quality = validate_comment(text, lang)
            if not link or not quality.valid:
                continue
            if not (
                item.get("comment_quality_status") == QUALITY_STATUS
                and item.get("comment_quality_version") == QUALITY_VERSION
                and item.get("comment_generation_status") == "ai_review_approved"
                and item.get("summary_basis") == "article_text_ai_reviewed"
            ):
                continue
            result[link] = quality.text
    return result


def _accept(item: dict, text: str, lang: str, source: str) -> bool:
    quality = validate_comment(text, lang)
    if not quality.valid:
        return False
    item["full_brief"] = quality.text
    item["ai_summary"] = quality.text
    item["ai_key_point"] = quality.text
    item["ai_why"] = ""
    item["ai_why_it_matters"] = ""
    item["ai_uncertain"] = ""
    item["summary_basis"] = "article_text_ai_reviewed"
    item["comment_generation_status"] = "ai_review_approved"
    item["comment_quality_status"] = QUALITY_STATUS
    item["comment_quality_version"] = QUALITY_VERSION
    item["section_comment_source"] = source
    item["_full_article_comment_approved"] = True
    return True


def _section_candidate_parts(items: list[dict]) -> tuple[list[dict], list[dict]]:
    approved_reserve = [
        item
        for item in items
        if item.get("_section_reserve") is True
        and item.get("_full_article_comment_approved") is True
    ]
    reserve_urls = {
        _canonical_url(item)
        for item in approved_reserve
        if _canonical_url(item)
    }
    fresh: list[dict] = []
    seen_fresh_urls: set[str] = set()
    for item in items:
        if item.get("_section_reserve") is True:
            continue
        canonical = _canonical_url(item)
        if canonical and (canonical in reserve_urls or canonical in seen_fresh_urls):
            continue
        if canonical:
            seen_fresh_urls.add(canonical)
        fresh.append(item)
    return fresh[:MAX_NEW_ITEMS_PER_SECTION], approved_reserve


def _bounded_section_candidates(items: list[dict]) -> list[dict]:
    fresh, approved_reserve = _section_candidate_parts(items)
    needed = max(0, MIN_APPROVED_PER_SECTION - len(approved_reserve))
    fresh_limit = min(
        MAX_NEW_ITEMS_PER_SECTION,
        max(BASE_NEW_ITEMS_PER_SECTION, needed + REJECTION_RESERVE),
    )
    return fresh[:fresh_limit] + approved_reserve


def enrich_sections_with_homepage_quality(
    sections: dict[str, list[dict]],
    lang: str,
    *,
    keep_unapproved: bool = False,
) -> dict[str, list[dict]]:
    """Select section cards and optionally attach strict AI comments.

    ``keep_unapproved=True`` is the source-first publication mode. It performs
    no article fetches and no model calls; this preserves AI capacity for the
    homepage and guarantees that a rejected comment cannot remove a valid news
    card. The default remains the historical strict-comment mode.
    """
    if lang not in {"pl", "en"}:
        raise ValueError(f"Unsupported language: {lang}")

    if keep_unapproved:
        return {
            section_key: _bounded_section_candidates(items)
            for section_key, items in sections.items()
        }

    homepage = approved_homepage_comments(lang)
    cache = load_cache()
    sequence = 0
    candidate_parts = {
        section_key: _section_candidate_parts(items)
        for section_key, items in sections.items()
    }
    processed: dict[str, list[dict]] = {
        section_key: []
        for section_key in sections
    }
    offsets: dict[str, int] = {}

    def process_wave(wave: dict[str, list[dict]]) -> None:
        nonlocal sequence
        candidates: list[dict] = []
        records: dict[str, dict] = {}
        for section_key, item in round_robin_section_items(wave):
            processed[section_key].append(item)
            item["_full_article_comment_approved"] = False
            link = str(item.get("link") or "").strip()
            title = str(item.get("title") or "").strip()

            existing = homepage.get(link)
            if existing and _accept(item, existing, lang, "approved_homepage_comment"):
                item["article_read_status"] = "reused_homepage_article_review"
                continue

            article_text, status = fetch_article_text(link)
            item["article_read_status"] = status
            item["article_text_chars"] = len(article_text)
            if len(article_text) < MIN_ARTICLE_CHARS:
                item["comment_generation_status"] = "rejected_or_unavailable"
                item["summary_basis"] = "rss_only_insufficient_article_text"
                continue

            item_id = f"section-{lang}-{sequence}"
            sequence += 1
            candidates.append(
                {
                    "id": item_id,
                    "title": title,
                    "link": link,
                    "article_text": article_text,
                }
            )
            records[item_id] = item

        generated = ai_summarize_batch(candidates, lang, cache) if candidates else {}
        for item_id, item in records.items():
            text = str(generated.get(item_id) or "").strip()
            if text and _accept(item, text, lang, "shared_full_article_pipeline"):
                continue
            item["comment_generation_status"] = "rejected_or_unavailable"
            item["summary_basis"] = "article_text_ai_rejected"

    initial_wave: dict[str, list[dict]] = {}
    for section_key, (fresh, approved_reserve) in candidate_parts.items():
        needed = max(0, MIN_APPROVED_PER_SECTION - len(approved_reserve))
        initial_count = min(
            len(fresh),
            max(BASE_NEW_ITEMS_PER_SECTION, needed + REJECTION_RESERVE),
        )
        initial_wave[section_key] = fresh[:initial_count]
        offsets[section_key] = initial_count
    process_wave(initial_wave)

    while True:
        backfill_wave: dict[str, list[dict]] = {}
        for section_key, (fresh, approved_reserve) in candidate_parts.items():
            approved_count = len(approved_reserve) + sum(
                item.get("_full_article_comment_approved") is True
                for item in processed[section_key]
            )
            shortage = max(0, APPROVED_TARGET_PER_SECTION - approved_count)
            start = offsets[section_key]
            if not shortage or start >= len(fresh):
                continue
            count = min(len(fresh) - start, shortage + REJECTION_RESERVE)
            backfill_wave[section_key] = fresh[start : start + count]
            offsets[section_key] += count
        if not backfill_wave:
            break
        process_wave(backfill_wave)

    save_cache(cache)
    return {
        section_key: [
            item
            for item in processed[section_key] + candidate_parts[section_key][1]
            if item.get("_full_article_comment_approved") is True
        ]
        for section_key in sections
    }
