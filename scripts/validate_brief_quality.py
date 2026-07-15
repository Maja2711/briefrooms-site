#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final fail-closed editorial gate for BriefRooms article comments."""

from __future__ import annotations

import json
from pathlib import Path

from comment_quality import (
    MAX_SENTENCES,
    MIN_SENTENCES,
    QUALITY_STATUS,
    QUALITY_VERSION,
    normalize_text,
    validate_comment,
)

FILES = [(Path("pl/home_brief.json"), "pl"), (Path("en/home_brief.json"), "en")]
MAX_VISIBLE_ITEMS = 12
REQUIRED_BASIS = "article_text_ai_reviewed"
REQUIRED_GENERATION_STATUS = "ai_review_approved"


def publishable_comment(item: dict, lang: str) -> tuple[str, tuple[str, ...]]:
    if item.get("summary_basis") != REQUIRED_BASIS:
        return "", ("not_ai_reviewed_article_text",)
    if item.get("comment_generation_status") != REQUIRED_GENERATION_STATUS:
        return "", ("ai_review_not_approved",)
    value = item.get("full_brief")
    if not isinstance(value, str):
        return "", ("missing_full_brief",)
    result = validate_comment(value, lang)
    return (result.text, ()) if result.valid else ("", result.reasons)


def process(path: Path, lang: str) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    rejected: list[dict] = []

    for section in ("latest", "radar"):
        old_items = data.get(section, []) or []
        kept: list[dict] = []
        for item in old_items:
            for key in ("title", "source", "category"):
                if isinstance(item.get(key), str):
                    item[key] = normalize_text(item[key])

            comment, reasons = publishable_comment(item, lang)
            if not comment:
                rejected.append({
                    "source": item.get("source", ""),
                    "title": item.get("title", ""),
                    "reasons": list(reasons),
                })
                continue

            result = validate_comment(comment, lang)
            item["full_brief"] = result.text
            item["details"] = result.text
            item["summary"] = " ".join(result.sentences[:2])
            item["comment_quality_status"] = QUALITY_STATUS
            item["comment_quality_version"] = QUALITY_VERSION
            kept.append(item)

        kept = kept[:MAX_VISIBLE_ITEMS]
        if kept != old_items:
            data[section] = kept
            changed = True

    data["count"] = len(data.get("latest", []) or []) + len(data.get("radar", []) or [])
    data["comment_quality_gate"] = {
        "version": QUALITY_VERSION,
        "status": QUALITY_STATUS,
        "mode": "fail_closed_full_comment",
        "pl": "Publikowany jest wyłącznie komentarz utworzony z tekstu artykułu, zatwierdzony w niezależnym przeglądzie AI i przyjęty w całości przez rygorystyczną kontrolę językową. Jedno wadliwe zdanie odrzuca cały komentarz.",
        "en": "Only an article-derived comment approved by an independent AI review and accepted in full by the strict language gate is published. One defective sentence rejects the whole comment.",
        "min_article_sentences": MIN_SENTENCES,
        "max_article_sentences": MAX_SENTENCES,
        "rejected_count": len(rejected),
        "rejected_examples": rejected[:12],
        "raw_article_or_rss_fallback": "forbidden",
        "partial_sentence_salvage": "forbidden",
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    changed = False
    for path, lang in FILES:
        changed = process(path, lang) or changed
    print("Strict brief quality gate applied" if changed else "Strict brief quality gate unchanged")


if __name__ == "__main__":
    main()
