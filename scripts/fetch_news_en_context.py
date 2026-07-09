#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plain EN news builder for BriefRooms.

Active wrapper used by news-en.yml. It keeps the feed selection from
scripts/fetch_news_en.py, but makes the public AI comment deliberately simple:
one clean article-level summary based on the title/RSS text. It removes generic
"Why it matters" rows such as health/science/policy boilerplate.
"""

import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import fetch_news_en as base  # noqa: E402

_original_fetch_section = base.fetch_section
_original_render_html = base.render_html

WEATHER_RE = re.compile(
    r"\b(weather|storm|storms|thunderstorm|rain|wind|hail|heatwave|temperature|forecast|met office|weather warning|snow|flood warning)\b",
    re.I,
)
BAD_AI_TEXT_RE = re.compile(
    r"\b(For health news|For science news|single-source item selected from a priority BriefRooms feed|"
    r"it matters because|the useful context is|The concrete channel is|This is a macro signal|"
    r"geopolitical signal|public decisions, safety or daily life|check the source)\b",
    re.I,
)

NEWS_TABS_CSS = """
    html{ scroll-behavior:smooth; }
    .section-tabs{position:sticky;top:0;z-index:20;display:flex;gap:10px;justify-content:center;align-items:center;flex-wrap:wrap;margin:8px auto 18px;padding:10px 12px;background:rgba(8,15,30,.72);backdrop-filter:blur(14px);border:1px solid rgba(255,255,255,.10);border-radius:999px;box-shadow:0 10px 28px rgba(0,0,0,.28)}
    .section-tabs a{display:inline-flex;align-items:center;justify-content:center;min-width:96px;padding:9px 14px;border-radius:999px;color:#fdf3e3;text-decoration:none;font-weight:800;letter-spacing:.01em;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);white-space:nowrap}
    .section-tabs .brand-link{min-width:auto;gap:0;padding:8px 14px;background:linear-gradient(135deg,rgba(248,201,122,.30),rgba(255,255,255,.08));border-color:rgba(248,201,122,.46);color:#fff;box-shadow:0 8px 22px rgba(0,0,0,.20)}
    .section-tabs .brand-mark{display:inline-flex;align-items:center;justify-content:center;width:34px;height:28px;border-radius:10px;color:#101827;background:linear-gradient(135deg,#f8c97a,#fff1c7,#38d6c9);font-weight:950;letter-spacing:-.08em}
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


def _plain_summary(title: str, snippet: str, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", (snippet or fallback or title or "").strip())
    text = re.sub(r"^(Key point|Why it matters|Summary)\s*:\s*", "", text, flags=re.I)
    if not text or BAD_AI_TEXT_RE.search(text):
        text = re.sub(r"\s+", " ", (fallback or title or "").strip())
    if len(text) > 360:
        text = text[:360].rsplit(" ", 1)[0]
    return base.ensure_period(text)


def _item_text(item: dict) -> str:
    return " ".join(str(item.get(k, "") or "") for k in ("title", "summary_raw", "ai_key_point", "ai_summary", "source_name", "link"))


def fetch_section_plain(section_key: str, excluded_links=None, excluded_topics=None):
    items = _original_fetch_section(section_key, excluded_links, excluded_topics)
    cleaned = []
    for it in items:
        if WEATHER_RE.search(_item_text(it)):
            continue

        summary = _plain_summary(
            it.get("title", ""),
            it.get("ai_key_point") or it.get("ai_summary") or it.get("summary_raw", ""),
            it.get("summary_raw", ""),
        )
        it["ai_key_point"] = summary
        it["ai_summary"] = summary
        it["ai_why_it_matters"] = ""

        if BAD_AI_TEXT_RE.search(str(it.get("ai_uncertain", "") or "")):
            it["ai_uncertain"] = ""

        it["ai_model"] = ((it.get("ai_model") or "") + "+plain-summary-v1").strip("+")
        cleaned.append(it)
    return cleaned


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


def render_html_plain(sections: dict) -> str:
    html = _original_render_html(sections)
    html = re.sub(r'\n\s*<div class="sec"><strong>Why it matters:</strong>.*?</div>', '', html, flags=re.I | re.S)
    html = re.sub(r'\n\s*<div class="sec"><strong>Warning:</strong>\s*(?:Warning:\s*)+', '\n    <div class="sec"><strong>Warning:</strong> ', html, flags=re.I)
    return add_news_tabs_en(html)


base.fetch_section = fetch_section_plain
base.render_html = render_html_plain

if __name__ == "__main__":
    base.main()
