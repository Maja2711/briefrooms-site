#!/usr/bin/env python3
"""Load recently approved news cards as a bounded publication reserve."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from comment_quality import (
    QUALITY_STATUS,
    QUALITY_VERSION,
    review_digest,
    valid_display_title,
    validate_comment,
)
from external_media_policy import external_image_url
from news_story_dedupe import _canonical_url, load_recent_history

ROOT = Path(__file__).resolve().parents[1]
PAGE_PATHS = {
    "pl": ROOT / "pl" / "aktualnosci.html",
    "en": ROOT / "en" / "news.html",
}
HISTORY_PATHS = {
    "pl": ROOT / "data" / "news_story_history_pl.json",
    "en": ROOT / "data" / "news_story_history_en.json",
}
SECTION_IDS = {
    "pl": {
        "polityka": "polityka",
        "ekonomia": "biznes",
        "zdrowie": "zdrowie",
        "nauka": "nauka",
        "sport": "sport",
    },
    "en": {
        "world-news": "world",
        "asia-pacific": "asia_pacific",
        "europe": "europe",
        "middle-east": "middle_east",
        "business": "business",
        "science": "science",
        "health": "health",
        "sport": "sport",
    },
}


def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
    value = dict(attrs).get("class") or ""
    return {item for item in value.split() if item}


class NewsCardParser(HTMLParser):
    """Extract the visible card contract without relying on HTML regexes."""

    def __init__(self, lang: str):
        super().__init__(convert_charrefs=True)
        self.lang = lang
        self.section: str | None = None
        self.card: dict[str, Any] | None = None
        self.cards = {key: [] for key in SECTION_IDS[lang].values()}
        self.capture: str | None = None
        self.capture_depth = 0
        self.capture_text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        classes = _classes(attrs)
        if tag == "section":
            self.section = SECTION_IDS[self.lang].get(str(attributes.get("id") or ""))
        if tag == "li" and self.section:
            self.card = {
                "section": self.section,
                "title": "",
                "link": "",
                "thumbnail_url": "",
                "source_name": "",
                "full_brief": "",
            }
        if self.card is None:
            return
        if self.capture:
            self.capture_depth += 1
            return
        if tag == "a" and "news-main-link" in classes:
            self.card["link"] = str(attributes.get("href") or "").strip()
        elif tag == "img" and not self.card["thumbnail_url"]:
            self.card["thumbnail_url"] = str(attributes.get("src") or "").strip()
        elif "news-text" in classes:
            self._start_capture("title")
        elif "source-line" in classes and not self.card["source_name"]:
            self._start_capture("source_name")
        elif tag == "div" and "sec" in classes and not self.card["full_brief"]:
            self._start_capture("full_brief")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if self.capture:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.capture_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.capture:
            self.capture_depth -= 1
            if self.capture_depth == 0:
                value = " ".join(" ".join(self.capture_text).split())
                if self.card is not None:
                    self.card[self.capture] = value
                self.capture = None
                self.capture_text = []
            return
        if tag == "li" and self.card is not None:
            section = str(self.card.get("section") or "")
            if section in self.cards:
                self.cards[section].append(self.card)
            self.card = None
        elif tag == "section":
            self.section = None

    def _start_capture(self, field: str) -> None:
        self.capture = field
        self.capture_depth = 1
        self.capture_text = []


def parse_news_page(source: str, lang: str) -> dict[str, list[dict[str, Any]]]:
    if lang not in PAGE_PATHS:
        raise ValueError(f"Unsupported language: {lang}")
    parser = NewsCardParser(lang)
    parser.feed(source)
    parser.close()
    return parser.cards


def _source_name(value: object) -> str:
    text = " ".join(str(value or "").split())
    if ":" in text:
        text = text.split(":", 1)[1].strip()
    return text


def load_news_section_reserve(
    lang: str,
    *,
    page_path: Path | None = None,
    history_path: Path | None = None,
    now=None,
    image_lookup=None,
) -> dict[str, list[dict[str, Any]]]:
    """Return only recent cards that still pass the current publication gates."""
    if lang not in PAGE_PATHS:
        raise ValueError(f"Unsupported language: {lang}")
    result = {key: [] for key in SECTION_IDS[lang].values()}
    selected_page = page_path or PAGE_PATHS[lang]
    selected_history = history_path or HISTORY_PATHS[lang]
    try:
        parsed = parse_news_page(selected_page.read_text(encoding="utf-8"), lang)
    except (OSError, UnicodeError):
        return result

    history = load_recent_history(selected_history, now=now)
    by_url = {
        _canonical_url(item): item
        for item in history
        if _canonical_url(item)
    }
    seen: set[str] = set()
    for section_key, cards in parsed.items():
        for card in cards:
            link = str(card.get("link") or "").strip()
            canonical = _canonical_url({"link": link})
            historical = by_url.get(canonical)
            if not canonical or canonical in seen or not historical:
                continue
            title = str(card.get("title") or "").strip()
            text = str(card.get("full_brief") or "").strip()
            source_name = _source_name(card.get("source_name"))
            image = external_image_url(card.get("thumbnail_url"), link)
            if not image and callable(image_lookup):
                try:
                    image = external_image_url(image_lookup(link), link)
                except Exception:
                    image = ""
            quality = validate_comment(text, lang)
            if not (
                valid_display_title(title, lang)
                and source_name
                and image
                and quality.valid
            ):
                continue
            seen.add(canonical)
            approved = {
                "title": title,
                "link": link,
                "source_name": source_name,
                "thumbnail_url": image,
                "summary_raw": quality.text,
                "summary": quality.text,
                "full_brief": quality.text,
                "ai_summary": quality.text,
                "ai_key_point": quality.text,
                "ai_why": "",
                "ai_why_it_matters": "",
                "ai_uncertain": "",
                "published_at": historical.get("published_at"),
                "published_at_inferred": bool(
                    historical.get("published_at_inferred")
                ),
                "summary_basis": "article_text_ai_reviewed",
                "comment_generation_status": "ai_review_approved",
                "comment_quality_status": QUALITY_STATUS,
                "comment_quality_version": QUALITY_VERSION,
                "comment_review_digest": review_digest(quality.text),
                "section_comment_source": "recent_approved_news_page",
                "article_read_status": "reused_recent_article_review",
                "_section_reserve": True,
                "_full_article_comment_approved": True,
            }
            result[section_key].append(approved)
    return result
