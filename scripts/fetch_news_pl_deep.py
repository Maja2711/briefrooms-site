#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict PL BriefRooms news builder.

This is the active builder used by news-pl.yml.
It removes weather items, limits TVN24, and keeps 'Dlaczego to ważne'
only when the line adds a concrete consequence.
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


def render_html_strict(sections: dict) -> str:
    html = _original_render(sections)

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
