#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stricter quality wrapper for PL BriefRooms news.

Rules:
- remove weather items;
- keep TVN24 as secondary source only;
- make the main 'Komentarz / Najważniejsze' carry the useful summary;
- show 'Dlaczego to ważne' only when it contains a concrete consequence.
"""

import re
import fetch_news_pl_deep as deep

base = deep.hybrid.base

_original_fetch = base.fetch_section
_original_render = base.render_html

WEATHER_RE = re.compile(
    r"(tvnmeteo|pogoda|pogodowy|burza|burze|radar burz|mapa opad|opady|deszcz|ulewa|wiatr|wichura|grad|śnieg|mróz|upał|temperatura|prognoza|IMGW|meteop)",
    re.I,
)

BAD_WHY_RE = re.compile(
    r"(obserwacji życia publicznego|przejrzystości życia publicznego|test zaufania|nie jest to jednak samo w sobie sygnał|znaczenie tej informacji zależy|to warto śledzić|pojedynczej ciekawostki|prestiż, ranking, awans|warto sprawdzić|kontekst: Mundial|zmiany presji przed kolejnym spotkaniem)",
    re.I,
)

CONCRETE_WHY_RE = re.compile(
    r"(inflacj|NBP|stóp|stopy|budżet|paliw|energia|rachunki|transport|koszt|od kiedy|kogo obejmuje|ranking ATP|ranking WTA|Top 100|wynik:|etap:|awans|spadek|runda|punkt|procent|mld|mln|zł|euro|dolar)",
    re.I,
)


def _txt(item):
    return " ".join(str(item.get(k, "") or "") for k in ("title", "summary_raw", "ai_summary", "source_name", "link"))


def fetch_section_quality(section_key: str):
    items = _original_fetch(section_key)
    out = []
    tvn24_used = 0

    for it in items:
        text = _txt(it)
        if WEATHER_RE.search(text):
            continue

        if "tvn24" in text.lower():
            if tvn24_used >= 1:
                continue
            tvn24_used += 1

        why = (it.get("ai_why") or "").strip()
        if BAD_WHY_RE.search(why) or not CONCRETE_WHY_RE.search(why):
            it["ai_why"] = ""

        out.append(it)

    return out


def render_html_quality(sections):
    html = _original_render(sections)

    # Final safety net for already cached/generated HTML pieces.
    html = re.sub(r'\n\s*<div class="sec"><strong>Dlaczego to ważne:</strong>\s*(?:[^<]*(?:obserwacji życia publicznego|przejrzystości życia publicznego|test zaufania|nie jest to jednak samo w sobie sygnał|znaczenie tej informacji zależy|to warto śledzić|pojedynczej ciekawostki|prestiż, ranking, awans|warto sprawdzić|kontekst: Mundial)[^<]*)</div>', '', html, flags=re.I)
    html = re.sub(r'\n\s*<div class="sec"><strong>Dlaczego to ważne:</strong>\s*</div>', '', html)
    return html


base.fetch_section = fetch_section_quality
base.render_html = render_html_quality

if __name__ == "__main__":
    base.main()
