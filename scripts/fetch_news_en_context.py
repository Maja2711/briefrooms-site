#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Homepage-grade EN News builder.

The active EN workflow keeps its World, Asia-Pacific, Europe, Middle East,
Business, Science, Health and Sport sections, but visible comments now come
from the same full-article generator, deterministic validator and independent
reviewer as the homepage. The generated page uses the same large image cards.
"""

from __future__ import annotations

import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import fetch_news_en as base  # noqa: E402
from comment_quality import QUALITY_STATUS, QUALITY_VERSION, validate_comment  # noqa: E402
from newsroom_articles import enrich_sections_with_homepage_quality  # noqa: E402
from newsroom_style import apply_newsroom_style  # noqa: E402
from news_story_dedupe import assert_no_duplicate_stories  # noqa: E402

_original_fetch_section = base.fetch_section
_original_render_html = base.render_html
_original_finalize_sections = base.finalize_sections

WEATHER_RE = re.compile(
    r"\b(weather|storm|storms|thunderstorm|rain|wind|hail|heatwave|temperature|forecast|met office|weather warning|snow|flood warning)\b",
    re.I,
)
MIN_TOTAL_APPROVED = 24
MIN_PER_SECTION = 3
MAX_PER_SECTION = 9

NEWS_TABS_CSS = """
    html{ scroll-behavior:smooth; }
    .section-tabs{position:sticky;top:0;z-index:20;display:flex;gap:10px;justify-content:center;align-items:center;flex-wrap:wrap;margin:8px auto 18px;padding:10px 12px;background:rgba(8,15,30,.72);backdrop-filter:blur(14px);border:1px solid rgba(255,255,255,.10);border-radius:999px;box-shadow:0 10px 28px rgba(0,0,0,.28)}
    .section-tabs a{display:inline-flex;align-items:center;justify-content:center;min-width:96px;padding:9px 14px;border-radius:999px;color:#fdf3e3;text-decoration:none;font-weight:800;letter-spacing:.01em;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);white-space:nowrap}
    .section-tabs .brand-link{min-width:auto;gap:0;padding:8px 14px;background:linear-gradient(135deg,rgba(248,201,122,.30),rgba(255,255,255,.08));border-color:rgba(248,201,122,.46);color:#fff;box-shadow:0 8px 22px rgba(0,0,0,.20)}
    .section-tabs .brand-mark{display:inline-flex;align-items:center;justify-content:center;width:34px;height:28px;border-radius:10px;color:#101827;background:linear-gradient(135deg,#087f9a,#23d5cc 38%,#78e7f7 70%,#d6fbff);font-weight:950;letter-spacing:-.08em}
    .section-tabs a:hover,.section-tabs a:focus-visible{background:rgba(248,201,122,.18);border-color:rgba(248,201,122,.42);color:#fff;outline:none;transform:translateY(-1px)}
    section.card{scroll-margin-top:92px}
    @media(max-width:760px){.section-tabs{border-radius:22px;justify-content:stretch}.section-tabs a{flex:1 1 31%;min-width:auto;padding:9px 10px;font-size:.88rem}.section-tabs .brand-link{flex:1 1 100%;justify-content:center}}
"""

NEWS_TABS_HTML = """
<nav class="section-tabs" aria-label="News sections">
  <a class="brand-link" href="/en/" aria-label="BRs — home"><span class="brand-mark">BRs</span></a>
  <a href="#world-news">World News</a>
  <a href="#asia-pacific">Asia-Pacific</a>
  <a href="#europe">Europe</a>
  <a href="#middle-east">Middle East</a>
  <a href="#business">Business</a>
  <a href="#science">Science</a>
  <a href="#health">Health</a>
  <a href="#sport">Sport</a>
</nav>
"""

SECTION_IDS = {
    "World News": "world-news",
    "Asia-Pacific": "asia-pacific",
    "Europe": "europe",
    "Middle East": "middle-east",
    "Business": "business",
    "Science": "science",
    "Health": "health",
    "Sport": "sport",
}


def _item_text(item: dict) -> str:
    return " ".join(str(item.get(key, "") or "") for key in ("title", "summary_raw", "source_name", "link"))


def fetch_section_full(section_key: str, excluded_links=None, excluded_topics=None, summarize: bool = True):
    items = _original_fetch_section(section_key, excluded_links, excluded_topics, summarize=summarize)
    return [item for item in items if not WEATHER_RE.search(_item_text(item))]


def summarize_sections_en_full(sections: dict) -> None:
    enriched = enrich_sections_with_homepage_quality(sections, "en")
    sections.clear()
    sections.update(enriched)


def finalize_sections_full(sections: dict) -> dict:
    sections = _original_finalize_sections(sections)
    final: dict[str, list[dict]] = {}
    for section_key, items in sections.items():
        approved: list[dict] = []
        for item in items:
            text = str(item.get("full_brief") or item.get("ai_key_point") or item.get("ai_summary") or "")
            quality = validate_comment(text, "en")
            if not quality.valid:
                continue
            if not (
                item.get("comment_quality_status") == QUALITY_STATUS
                and item.get("comment_quality_version") == QUALITY_VERSION
                and item.get("comment_generation_status") == "ai_review_approved"
                and item.get("summary_basis") == "article_text_ai_reviewed"
            ):
                continue
            item["full_brief"] = quality.text
            item["ai_key_point"] = quality.text
            item["ai_summary"] = quality.text
            item["ai_why_it_matters"] = ""
            item["ai_uncertain"] = ""
            approved.append(item)
        final[section_key] = approved[:MAX_PER_SECTION]
    return final


def add_news_tabs_en(html: str) -> str:
    if 'class="section-tabs"' not in html:
        html = html.replace("</style>", NEWS_TABS_CSS + "\n  </style>", 1)
        html = html.replace("<main>\n", "<main>\n" + NEWS_TABS_HTML + "\n", 1)
    for title, section_id in SECTION_IDS.items():
        html = html.replace(
            f'<section class="card">\n  <h2>{title}</h2>',
            f'<section class="card" id="{section_id}">\n  <h2>{title}</h2>',
            1,
        )
    return html


def render_html_full(sections: dict) -> str:
    accepted = [item for items in sections.values() for item in items]
    underfilled = {
        section_key: len(items)
        for section_key, items in sections.items()
        if len(items) < MIN_PER_SECTION
    }
    if underfilled:
        details = ", ".join(f"{section}={count}" for section, count in underfilled.items())
        raise RuntimeError(
            "EN news publication kept on last-good version: "
            f"each section requires {MIN_PER_SECTION}-{MAX_PER_SECTION} approved items; {details}"
        )
    if len(accepted) < MIN_TOTAL_APPROVED:
        raise RuntimeError(
            f"EN news publication kept on last-good version: only {len(accepted)} homepage-grade comments"
        )
    for item in accepted:
        quality = validate_comment(
            item.get("full_brief") or item.get("ai_key_point") or item.get("ai_summary", ""),
            "en",
        )
        if not quality.valid:
            raise RuntimeError(
                f"EN news publication blocked by full-article comment audit: {item.get('title', '')[:80]}"
            )
    assert_no_duplicate_stories(sections)
    html = _original_render_html(sections)
    html = re.sub(r'\n\s*<div class="sec"><strong>Why it matters:</strong>.*?</div>', "", html, flags=re.I | re.S)
    html = html.replace("<strong>Key point:</strong> ", "")
    html = add_news_tabs_en(html)
    return apply_newsroom_style(html, "en")


base.fetch_section = fetch_section_full
base.summarize_sections_en = summarize_sections_en_full
base.finalize_sections = finalize_sections_full
base.render_html = render_html_full

if __name__ == "__main__":
    base.main()
