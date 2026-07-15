#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict PL BriefRooms news builder.

Active builder used by news-pl.yml. It keeps the existing feed/selection logic,
removes low-value weather items, limits TVN24 to one item per section, and keeps
AI comments as simple article summaries. It removes the separate "Dlaczego to
ważne" layer, because those rows too easily become generic boilerplate.
"""

import re
import fetch_news_pl_hybrid as hybrid

base = hybrid.base
_original_fetch = base.fetch_section
_original_render = base.render_html

WEATHER_RE = re.compile(
    r"(tvnmeteo|pogoda|pogodowy|burza|burze|radar burz|mapa opad|opady|deszcz|ulewa|wiatr|wichura|grad|śnieg|mróz|upał|temperatura|prognoza|IMGW|meteop)",
    re.I,
)
TVN24_RE = re.compile(r"(TVN24|tvn24\.pl)", re.I)
BAD_AI_RE = re.compile(
    r"(Dlaczego to ważne|Najważniejsze:|warto sprawdzić|wpływa na decyzje publiczne|"
    r"obserwacji życia publicznego|test zaufania|pojedynczej ciekawostki|sam fakt jest punktem wyjścia)",
    re.I,
)

SECTION_TABS_CSS = """
    html{ scroll-behavior:smooth; }
    .section-tabs{position:sticky;top:0;z-index:20;display:flex;gap:10px;justify-content:center;align-items:center;flex-wrap:wrap;margin:8px auto 18px;padding:10px 12px;background:rgba(8,15,30,.72);backdrop-filter:blur(14px);border:1px solid rgba(255,255,255,.10);border-radius:999px;box-shadow:0 10px 28px rgba(0,0,0,.28)}
    .section-tabs a{display:inline-flex;align-items:center;justify-content:center;min-width:112px;padding:9px 16px;border-radius:999px;color:#fdf3e3;text-decoration:none;font-weight:800;letter-spacing:.01em;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12)}
    .section-tabs .brand-link{min-width:auto;gap:0;padding:8px 14px;background:linear-gradient(135deg,rgba(248,201,122,.30),rgba(255,255,255,.08));border-color:rgba(248,201,122,.46);color:#fff;box-shadow:0 8px 22px rgba(0,0,0,.20)}
    .section-tabs .brand-mark{display:inline-flex;align-items:center;justify-content:center;width:34px;height:28px;border-radius:10px;color:#101827;background:linear-gradient(135deg,#f8c97a,#fff1c7);font-weight:950;letter-spacing:-.08em}
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


def _plain_summary(title: str, summary: str, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", (summary or fallback or title or "").strip())
    text = re.sub(r"^(Najważniejsze|Dlaczego to ważne)\s*:\s*", "", text, flags=re.I)
    if not text or BAD_AI_RE.search(text):
        text = re.sub(r"\s+", " ", (fallback or title or "").strip())
    if len(text) > 360:
        text = text[:360].rsplit(" ", 1)[0]
    return base.ensure_period(text)


def _text(item: dict) -> str:
    return " ".join(str(item.get(k, "") or "") for k in ("title", "summary_raw", "ai_summary", "ai_why", "source_name", "link"))


def _clean_uncertain(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip())
    value = re.sub(r"^(Uwaga|Ostrożnie)\s*:\s*", "", value, flags=re.I)
    return base.ensure_period(value) if value else ""


def fetch_section_strict(section_key: str):
    items = _original_fetch(section_key)
    kept = []
    tvn24_used = 0

    for item in items:
        text = _text(item)
        if WEATHER_RE.search(text) and section_key not in {"zdrowie", "nauka"}:
            continue

        if TVN24_RE.search(text):
            if tvn24_used >= 1:
                continue
            tvn24_used += 1

        item["ai_summary"] = _plain_summary(
            item.get("title", ""),
            item.get("ai_summary", ""),
            item.get("summary_raw", ""),
        )
        item["ai_why"] = ""
        item["ai_uncertain"] = _clean_uncertain(item.get("ai_uncertain", ""))

        kept.append(item)

    return kept


def _add_section_tabs(html: str) -> str:
    if 'class="section-tabs"' not in html:
        html = html.replace("</style>", SECTION_TABS_CSS + "\n  </style>")
        html = html.replace("<main>\n", "<main>\n" + SECTION_TABS_HTML + "\n", 1)

    html = html.replace(
        '<a class="brand-link" href="/pl/" aria-label="BriefRooms — strona startowa"><span class="brand-mark">BR</span><span>BriefRooms</span></a>',
        '<a class="brand-link" href="/pl/" aria-label="BRs — strona startowa"><span class="brand-mark">BRs</span></a>',
    )
    html = html.replace('<section class="card">\n  <h2>Polityka / Kraj</h2>', '<section class="card" id="polityka">\n  <h2>Polityka / Kraj</h2>', 1)
    html = html.replace('<section class="card">\n  <h2>Ekonomia / Biznes</h2>', '<section class="card" id="ekonomia">\n  <h2>Ekonomia / Biznes</h2>', 1)
    html = html.replace('<section class="card">\n  <h2>Zdrowie</h2>', '<section class="card" id="zdrowie">\n  <h2>Zdrowie</h2>', 1)
    html = html.replace('<section class="card">\n  <h2>Nauka</h2>', '<section class="card" id="nauka">\n  <h2>Nauka</h2>', 1)
    html = html.replace('<section class="card">\n  <h2>Sport</h2>', '<section class="card" id="sport">\n  <h2>Sport</h2>', 1)
    return html


def render_html_strict(sections: dict) -> str:
    html = _original_render(sections)
    html = _add_section_tabs(html)

    # Remove the separate generic "why" row; the AI comment is now a direct article summary.
    html = re.sub(r'\n\s*<div class="sec"><strong>Dlaczego to ważne:</strong>.*?</div>', '', html, flags=re.I | re.S)
    html = re.sub(r'(<strong>Uwaga:</strong>)\s*(?:Uwaga|Ostrożnie)\s*:\s*', r'\1 ', html, flags=re.I)
    html = re.sub(r"[ \t]+(?=\n)", "", html)
    return html


base.fetch_section = fetch_section_strict
base.render_html = render_html_strict

if __name__ == "__main__":
    base.main()
