#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared full-article comment pipeline for PL and EN section news pages.

The homepage and the section news pages must use the same publication contract:
- reuse an already approved homepage comment for an identical source link;
- otherwise read the complete source article and run the same generator and
  independent reviewer used by ``read_and_summarize_articles.py``;
- never publish an RSS-only comment as a fallback;
- fail closed per item, without lowering the language-quality threshold.
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

ROOT = Path(__file__).resolve().parents[1]
HOME_FILES = {
    "pl": ROOT / "pl" / "home_brief.json",
    "en": ROOT / "en" / "home_brief.json",
}


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


def enrich_sections_with_homepage_quality(sections: dict[str, list[dict]], lang: str) -> dict[str, list[dict]]:
    """Attach homepage-grade comments and remove items that do not pass.

    The function mutates item dictionaries and returns a section mapping that
    contains only approved items. One unreadable article never invalidates other
    articles or sections.
    """
    if lang not in {"pl", "en"}:
        raise ValueError(f"Unsupported language: {lang}")

    homepage = approved_homepage_comments(lang)
    cache = load_cache()
    candidates: list[dict] = []
    records: dict[str, dict] = {}
    sequence = 0

    for section_key, items in sections.items():
        for item in items:
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

    generated = ai_summarize_batch(candidates, lang, cache)
    for item_id, item in records.items():
        text = str(generated.get(item_id) or "").strip()
        if text and _accept(item, text, lang, "shared_full_article_pipeline"):
            continue
        item["comment_generation_status"] = "rejected_or_unavailable"
        item["summary_basis"] = "article_text_ai_rejected"

    save_cache(cache)
    return {
        section_key: [
            item
            for item in items
            if item.get("_full_article_comment_approved") is True
        ]
        for section_key, items in sections.items()
    }
