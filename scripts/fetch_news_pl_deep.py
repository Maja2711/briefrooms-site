#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Homepage-grade PL Aktualności builder.

This is the active wrapper used by news-pl.yml. Selection and section structure
remain unchanged, but comments now use the exact full-article pipeline and
quality contract used by the homepage. RSS-only comments are never published.
The page is rendered as large two-column homepage-style cards while preserving
Polityka, Ekonomia, Zdrowie, Nauka and Sport sections.
"""

from __future__ import annotations

import re

import fetch_news_pl_hybrid as hybrid
from comment_quality import QUALITY_STATUS, QUALITY_VERSION, validate_comment
from newsroom_articles import enrich_sections_with_homepage_quality
from newsroom_style import apply_newsroom_style
from news_story_dedupe import assert_no_duplicate_stories

base = hybrid.base
_original_fetch = base.fetch_section
_original_render = base.render_html
_original_finalize_sections = base.finalize_sections

WEATHER_RE = re.compile(
    r"(tvnmeteo|pogoda|pogodowy|burza|burze|radar burz|mapa opad|opady|deszcz|ulewa|wiatr|wichura|grad|śnieg|mróz|upał|temperatura|prognoza|IMGW|meteop)",
    re.I,
)
TVN24_RE = re.compile(r"(TVN24|tvn24\.pl)", re.I)
SECTION_MAXIMUMS = {key: bounds[1] for key, bounds in base.SECTION_PUBLISH_BOUNDS.items()}
MIN_TOTAL_APPROVED = 8

SECTION_TABS_CSS = """
    html{ scroll-behavior:smooth; }
    .section-tabs{position:sticky;top:0;z-index:20;display:flex;gap:10px;justify-content:center;align-items:center;flex-wrap:wrap;margin:8px auto 18px;padding:10px 12px;background:rgba(8,15,30,.72);backdrop-filter:blur(14px);border:1px solid rgba(255,255,255,.10);border-radius:999px;box-shadow:0 10px 28px rgba(0,0,0,.28)}
    .section-tabs a{display:inline-flex;align-items:center;justify-content:center;min-width:112px;padding:9px 16px;border-radius:999px;color:#fdf3e3;text-decoration:none;font-weight:800;letter-spacing:.01em;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12)}
    .section-tabs .brand-link{min-width:auto;gap:0;padding:8px 14px;background:linear-gradient(135deg,rgba(248,201,122,.30),rgba(255,255,255,.08));border-color:rgba(248,201,122,.46);color:#fff;box-shadow:0 8px 22px rgba(0,0,0,.20)}
    .section-tabs .brand-mark{display:inline-flex;align-items:center;justify-content:center;width:34px;height:28px;border-radius:10px;color:#101827;background:linear-gradient(135deg,#087f9a,#23d5cc 42%,#d6fbff);font-weight:950;letter-spacing:-.08em}
    .section-tabs a:hover,.section-tabs a:focus-visible{background:rgba(248,201,122,.18);border-color:rgba(248,201,122,.42);color:#fff;outline:none;transform:translateY(-1px)}
    section.card{scroll-margin-top:92px}
    @media(max-width:640px){.section-tabs{border-radius:22px;justify-content:stretch}.section-tabs a{flex:1 1 30%;min-width:auto;padding:9px 10px;font-size:.92rem}.section-tabs .brand-link{flex:1 1 100%;justify-content:center}}
"""

SECTION_TABS_HTML = """
<nav class="section-tabs" aria-label="Sekcje aktualności">
  <a class="brand-link" href="/pl/" aria-label="BRs — strona startowa"><span class="brand-mark">BRs</span></a>
  <a href="#polityka">Polityka</a>
  <a href="#ekonomia">Ekonomia</a>
  <a href="#zdrowie">Zdrowie</a>
  <a href="#nauka">Nauka</a>
  <a href="#sport">Sport</a>
</nav>
"""


def _item_text(item: dict) -> str:
    return " ".join(str(item.get(key, "") or "") for key in ("title", "summary_raw", "source_name", "link"))


def fetch_section_strict(section_key: str, summarize: bool = True):
    items = _original_fetch(section_key, summarize=summarize)
    kept: list[dict] = []
    tvn24_used = 0
    for item in items:
        text = _item_text(item)
        if WEATHER_RE.search(text) and section_key not in {"zdrowie", "nauka"}:
            continue
        if TVN24_RE.search(text):
            if tvn24_used >= 1:
                continue
            tvn24_used += 1
        if section_key == "polityka" and not str(item.get("thumbnail_url") or "").strip():
            continue
        kept.append(item)
    return kept


def summarize_sections_pl_full(sections: dict) -> None:
    # English-source items still need a Polish visible title before the shared
    # full-article comment is generated.
    for section_key, items in sections.items():
        translated_items: list[dict] = []
        for item in items:
            if item.get("_source_was_english"):
                translated = hybrid.translate_english_item_to_polish(item, section_key)
                if not translated or not translated.get("title_pl"):
                    continue
                original_source = item.get("source_name", "Źródło")
                item["title"] = translated["title_pl"]
                item["source_name"] = f"{original_source} · źródło anglojęzyczne — brief po polsku"
            translated_items.append(item)
        sections[section_key] = translated_items

    enriched = enrich_sections_with_homepage_quality(sections, "pl")
    sections.clear()
    sections.update(enriched)


def finalize_sections_strict(sections: dict) -> dict:
    sections = _original_finalize_sections(sections)
    final: dict[str, list[dict]] = {}
    for section_key, items in sections.items():
        approved: list[dict] = []
        for item in items:
            text = str(item.get("full_brief") or item.get("ai_summary") or "")
            quality = validate_comment(text, "pl")
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
            item["ai_summary"] = quality.text
            item["ai_why"] = ""
            item["ai_uncertain"] = ""
            approved.append(item)
        final[section_key] = approved[: SECTION_MAXIMUMS.get(section_key, 10)]
    return final


def _add_section_tabs(html: str) -> str:
    if 'class="section-tabs"' not in html:
        html = html.replace("</style>", SECTION_TABS_CSS + "\n  </style>", 1)
        html = html.replace("<main>\n", "<main>\n" + SECTION_TABS_HTML + "\n", 1)
    replacements = {
        "Polityka / Kraj": "polityka",
        "Ekonomia / Biznes": "ekonomia",
        "Zdrowie": "zdrowie",
        "Nauka": "nauka",
        "Sport": "sport",
    }
    for title, section_id in replacements.items():
        html = html.replace(
            f'<section class="card">\n  <h2>{title}</h2>',
            f'<section class="card" id="{section_id}">\n  <h2>{title}</h2>',
            1,
        )
    return html


def render_html_strict(sections: dict) -> str:
    accepted = [item for items in sections.values() for item in items]
    if len(accepted) < MIN_TOTAL_APPROVED:
        raise RuntimeError(
            f"PL news publication kept on last-good version: only {len(accepted)} homepage-grade comments"
        )
    for item in accepted:
        quality = validate_comment(item.get("full_brief") or item.get("ai_summary", ""), "pl")
        if not quality.valid:
            raise RuntimeError(
                f"PL news publication blocked by full-article comment audit: {item.get('title', '')[:80]}"
            )
    assert_no_duplicate_stories(sections)
    html = _original_render(sections)
    html = _add_section_tabs(html)
    html = re.sub(r'\n\s*<div class="sec"><strong>Dlaczego to ważne:</strong>.*?</div>', "", html, flags=re.I | re.S)
    html = html.replace("<strong>Najważniejsze:</strong> ", "")
    return apply_newsroom_style(html, "pl")


base.fetch_section = fetch_section_strict
base.summarize_sections_pl = summarize_sections_pl_full
base.finalize_sections = finalize_sections_strict
base.render_html = render_html_strict

if __name__ == "__main__":
    base.main()
