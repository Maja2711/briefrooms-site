#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict PL BriefRooms news builder.

This is the active builder used by news-pl.yml.
It removes weather items, limits TVN24, keeps 'Dlaczego to ważne'
only when the line adds a concrete consequence, and adds top section tabs
for fast navigation between Polityka, Ekonomia and Sport.
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

BAD_WHY_RE = re.compile(
    r"(obserwacji życia publicznego|przejrzystości życia publicznego|test zaufania|nie jest to jednak samo w sobie sygnał|znaczenie tej informacji zależy|to warto śledzić|pojedynczej ciekawostki|prestiż, ranking, awans|warto sprawdzić|kontekst: Mundial|zmiany presji przed kolejnym spotkaniem|może mieć znaczenie dla cen)",
    re.I,
)

CONCRETE_WHY_RE = re.compile(
    r"(inflacj|NBP|stóp|stopy|budżet|paliw|energia|rachunki|transport|koszt|od kiedy|kogo obejmuje|ranking ATP|ranking WTA|Top 100|wynik:|etap:|awans|spadek|runda|punkt|procent|mld|mln|zł|euro|dolar)",
    re.I,
)

TVN24_RE = re.compile(r"(TVN24|tvn24\.pl)", re.I)


SECTION_TABS_CSS = """
    html{ scroll-behavior:smooth; }
    .section-tabs{
      position:sticky; top:0; z-index:20;
      display:flex; gap:10px; justify-content:center; align-items:center; flex-wrap:wrap;
      margin:8px auto 18px; padding:10px 12px;
      background:rgba(8,15,30,.72); backdrop-filter:blur(14px);
      border:1px solid rgba(255,255,255,.10); border-radius:999px;
      box-shadow:0 10px 28px rgba(0,0,0,.28);
    }
    .section-tabs a{
      display:inline-flex; align-items:center; justify-content:center;
      min-width:112px; padding:9px 16px; border-radius:999px;
      color:#fdf3e3; text-decoration:none; font-weight:800; letter-spacing:.01em;
      background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.12);
    }
    .section-tabs .brand-link{
      min-width:auto; gap:0; padding:8px 14px;
      background:linear-gradient(135deg, rgba(248,201,122,.30), rgba(255,255,255,.08));
      border-color:rgba(248,201,122,.46); color:#fff; box-shadow:0 8px 22px rgba(0,0,0,.20);
    }
    .section-tabs .brand-mark{
      display:inline-flex; align-items:center; justify-content:center;
      width:34px; height:28px; border-radius:10px;
      color:#101827; background:linear-gradient(135deg,#f8c97a,#fff1c7);
      font-weight:950; letter-spacing:-.08em;
    }
    .section-tabs a:hover,
    .section-tabs a:focus-visible{
      background:rgba(248,201,122,.18); border-color:rgba(248,201,122,.42); color:#fff;
      outline:none; transform:translateY(-1px);
    }
    section.card{ scroll-margin-top:92px; }
    @media (max-width:640px){
      .section-tabs{ border-radius:22px; justify-content:stretch; }
      .section-tabs a{ flex:1 1 30%; min-width:auto; padding:9px 10px; font-size:.92rem; }
      .section-tabs .brand-link{ flex:1 1 100%; justify-content:center; }
    }
"""

SECTION_TABS_HTML = """
<nav class="section-tabs" aria-label="Sekcje aktualności">
  <a class="brand-link" href="/pl/" aria-label="BRs — strona startowa"><span class="brand-mark">BRs</span></a>
  <a href="#polityka">Polityka</a>
  <a href="#ekonomia">Ekonomia</a>
  <a href="#sport">Sport</a>
</nav>
"""


def _text(item: dict) -> str:
    parts = []
    for key in ("title", "summary_raw", "ai_summary", "ai_why", "source_name", "link"):
        parts.append(str(item.get(key, "") or ""))
    return " ".join(parts)


def _clear_bad_why(item: dict) -> None:
    why = str(item.get("ai_why", "") or "").strip()
    if not why:
        item["ai_why"] = ""
        return
    if BAD_WHY_RE.search(why) or not CONCRETE_WHY_RE.search(why):
        item["ai_why"] = ""


def fetch_section_strict(section_key: str):
    items = _original_fetch(section_key)
    kept = []
    tvn24_used = 0

    for item in items:
        text = _text(item)

        # Weather has low added value on BriefRooms; users already have weather apps.
        if WEATHER_RE.search(text):
            continue

        # TVN24 is secondary/tertiary. Keep only one item per section.
        if TVN24_RE.search(text):
            if tvn24_used >= 1:
                continue
            tvn24_used += 1

        _clear_bad_why(item)
        kept.append(item)

    return kept


def _add_section_tabs(html: str) -> str:
    """Adds top anchor tabs and section ids to the rendered PL news page."""
    if "class=\"section-tabs\"" not in html:
        html = html.replace("</style>", SECTION_TABS_CSS + "\n  </style>")
        html = html.replace("<main>\n", "<main>\n" + SECTION_TABS_HTML + "\n", 1)

    # Permanent brand-mark normalization if an older cached template is used.
    html = html.replace(
        '<a class="brand-link" href="/pl/" aria-label="BriefRooms — strona startowa"><span class="brand-mark">BR</span><span>BriefRooms</span></a>',
        '<a class="brand-link" href="/pl/" aria-label="BRs — strona startowa"><span class="brand-mark">BRs</span></a>',
    )

    html = html.replace(
        '<section class="card">\n  <h2>Polityka / Kraj</h2>',
        '<section class="card" id="polityka">\n  <h2>Polityka / Kraj</h2>',
        1,
    )
    html = html.replace(
        '<section class="card">\n  <h2>Ekonomia / Biznes</h2>',
        '<section class="card" id="ekonomia">\n  <h2>Ekonomia / Biznes</h2>',
        1,
    )
    html = html.replace(
        '<section class="card">\n  <h2>Sport</h2>',
        '<section class="card" id="sport">\n  <h2>Sport</h2>',
        1,
    )
    return html


def render_html_strict(sections: dict) -> str:
    html = _original_render(sections)
    html = _add_section_tabs(html)

    # Safety net: remove low-value why lines that came from old cache.
    html = re.sub(
        r'\n\s*<div class="sec"><strong>Dlaczego to ważne:</strong>\s*[^<]*(?:obserwacji życia publicznego|przejrzystości życia publicznego|test zaufania|nie jest to jednak samo w sobie sygnał|znaczenie tej informacji zależy|to warto śledzić|pojedynczej ciekawostki|prestiż, ranking, awans|warto sprawdzić|kontekst: Mundial|może mieć znaczenie dla cen)[^<]*</div>',
        '',
        html,
        flags=re.I,
    )
    html = re.sub(r'\n\s*<div class="sec"><strong>Dlaczego to ważne:</strong>\s*</div>', '', html)
    return html


base.fetch_section = fetch_section_strict
base.render_html = render_html_strict

if __name__ == "__main__":
    base.main()
