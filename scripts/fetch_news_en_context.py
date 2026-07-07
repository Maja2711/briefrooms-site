#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Contextual EN news builder for BriefRooms.

This wrapper keeps the existing EN feed selection/rendering logic from
scripts/fetch_news_en.py, but:
- removes low-value weather items;
- shows "Why it matters" only when it adds concrete value;
- avoids generic slogans and source-checking filler;
- adds a BRs section navigation bar for the EN News room.
"""

import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import fetch_news_en as base  # noqa: E402

MACRO_ECON_RE = re.compile(
    r"\b(inflation|central bank|fed|federal reserve|ecb|interest rates?|rate cuts?|rate hikes?|gdp|jobs report|"
    r"unemployment|wages?|payrolls?|consumer prices?|cpi|ppi|retail sales|industrial production|"
    r"bond yields?|treasury yields?|deficit|budget|tax|tariffs?|trade balance|exports?|imports?|"
    r"stocks?|markets?|earnings|oil prices?|gas prices?|energy prices?|mortgage|credit|recession|growth)\b",
    re.I,
)
FUEL_ENERGY_RE = re.compile(r"\b(oil|gasoline|petrol|diesel|natural gas|electricity|energy prices?|power bills?|fuel)\b", re.I)
PUBLIC_PERSON_RE = re.compile(
    r"\b(wealth|net worth|salary|pension|allowance|expenses?|assets?|disclosure|ethics|conflict of interest)\b",
    re.I,
)
LOCAL_INCIDENT_RE = re.compile(
    r"\b(accident|crash|collision|attack|incident|arrest|detained|investigation|prosecutor|police|court|trial|"
    r"charge|fire|explosion|outage|shooting|assault|evacuation|emergency services)\b",
    re.I,
)
PUBLIC_POLICY_RE = re.compile(
    r"\b(bill|law|legislation|regulation|ban|subsidy|benefit|welfare|tax credit|budget|government plan|"
    r"public spending|ministry|parliament|congress|senate|cabinet|policy|rule|program|reform)\b",
    re.I,
)
HEALTH_RE = re.compile(r"\b(health|hospital|doctor|patient|disease|infection|vaccine|drug|medicine|trial|who|cdc|fda|outbreak|public health)\b", re.I)
SCIENCE_RE = re.compile(r"\b(science|study|research|space|nasa|esa|planet|galaxy|climate|technology|ai|artificial intelligence|discovery|experiment)\b", re.I)
SPORT_RE = re.compile(r"\b(match|game|score|league|tournament|championship|world cup|football|soccer|tennis|basketball|nba|nfl|nhl|mlb|f1|formula 1|grand prix|wins?|beats?|defeats?|qualifies?|advances?)\b", re.I)
GEOPOLITICS_RE = re.compile(
    r"\b(war|ceasefire|sanctions?|nato|security|defen[cs]e|missile|drone|border|summit|treaty|"
    r"iran|israel|gaza|ukraine|russia|china|taiwan|north korea|south china sea)\b",
    re.I,
)
WEATHER_RE = re.compile(
    r"\b(weather|storm|storms|thunderstorm|rain|wind|hail|heatwave|temperature|forecast|met office|weather warning|snow|flood warning)\b",
    re.I,
)
ROUND_RE = re.compile(r"\b(final|semi-final|semifinal|quarter-final|quarterfinal|round of 16|round of 32|first round|second round|third round|group stage|play-off|playoff|qualifier|qualifying)\b", re.I)
SCORE_RE = re.compile(r"\b\d{1,2}\s*[:–-]\s*\d{1,2}\b|\b\d{1,2}-\d{1,2}\b")
GENERIC_BAD_WHY_RE = re.compile(
    r"\b(single-source item selected from a priority BriefRooms feed|selected from a priority feed|"
    r"it matters because it affects public decisions, safety or daily life|"
    r"it may affect prices, companies, jobs or household decisions|"
    r"the source should be checked|useful follow-up|whether the result changes ranking, pressure or tactical expectations|"
    r"accountability story rather than a macroeconomic signal|expectations of transparency)\b",
    re.I,
)


_original_fetch_section = base.fetch_section
_original_render_html = base.render_html


NEWS_TABS_CSS = """
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
      min-width:96px; padding:9px 14px; border-radius:999px;
      color:#fdf3e3; text-decoration:none; font-weight:800; letter-spacing:.01em;
      background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.12);
      white-space:nowrap;
    }
    .section-tabs .brand-link{
      min-width:auto; gap:0; padding:8px 14px;
      background:linear-gradient(135deg, rgba(248,201,122,.30), rgba(255,255,255,.08));
      border-color:rgba(248,201,122,.46); color:#fff; box-shadow:0 8px 22px rgba(0,0,0,.20);
    }
    .section-tabs .brand-mark{
      display:inline-flex; align-items:center; justify-content:center;
      width:34px; height:28px; border-radius:10px;
      color:#101827; background:linear-gradient(135deg,#f8c97a,#fff1c7,#38d6c9);
      font-weight:950; letter-spacing:-.08em;
    }
    .section-tabs a:hover,
    .section-tabs a:focus-visible{
      background:rgba(248,201,122,.18); border-color:rgba(248,201,122,.42); color:#fff;
      outline:none; transform:translateY(-1px);
    }
    section.card{ scroll-margin-top:92px; }
    @media (max-width:760px){
      .section-tabs{ border-radius:22px; justify-content:stretch; }
      .section-tabs a{ flex:1 1 31%; min-width:auto; padding:9px 10px; font-size:.88rem; }
      .section-tabs .brand-link{ flex:1 1 100%; justify-content:center; }
    }
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


def _clip_sentence(text: str, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return ""
    if len(text) <= limit:
        return base.ensure_period(text)
    cut = text[:limit].rsplit(" ", 1)[0]
    return base.ensure_period(cut)


def is_weather_item(title: str, snippet: str) -> bool:
    return bool(WEATHER_RE.search(f"{title} {snippet}"))


def why_is_useful_en(why: str) -> bool:
    why = re.sub(r"\s+", " ", (why or "").strip())
    if not why or len(why) < 60:
        return False
    if GENERIC_BAD_WHY_RE.search(why):
        return False
    return True


def context_why_it_matters_en(section_key: str, title: str, snippet: str) -> str:
    """Return a concrete EN editorial note only when it adds useful article-level value. Empty string = omit line."""
    text = f"{title} {snippet}"

    if is_weather_item(title, snippet):
        return ""

    if section_key == "sport" or SPORT_RE.search(text):
        facts = []
        round_name = ROUND_RE.search(text)
        score = SCORE_RE.search(text)
        if round_name:
            facts.append(f"stage: {round_name.group(0)}")
        if score:
            facts.append(f"score/result: {score.group(0)}")
        if facts:
            return "Sports takeaway: " + ", ".join(facts) + ". The significance is the direct consequence shown in the item: result, qualification, elimination, table position or next round."
        return ""

    if FUEL_ENERGY_RE.search(text):
        return (
            "The concrete channel is cost: fuel and energy prices can quickly affect transport, household bills and business margins. "
            "The key distinction is whether the item describes a one-off move or a broader cost trend."
        )

    if section_key == "business" and MACRO_ECON_RE.search(text):
        return (
            "This is a macro signal: it matters through inflation, demand, company costs or central-bank expectations. "
            "The market impact usually depends on the gap between the data and what investors expected."
        )

    if PUBLIC_POLICY_RE.search(text):
        return (
            "The practical consequence is the core: who is affected, when the rule starts and who pays or benefits. "
            "That mechanism is more useful than a generic statement that policy matters."
        )

    if HEALTH_RE.search(text):
        return (
            "For health news, the useful context is scale, risk group and whether guidance changes for patients, doctors or public systems. "
            "That separates a system-level warning from a single case."
        )

    if SCIENCE_RE.search(text):
        return (
            "For science news, the useful context is method, source and confirmation. "
            "The key is what the finding changes in understanding the topic, not just the striking headline."
        )

    if section_key in {"world", "asia_pacific", "europe", "middle_east"} and GEOPOLITICS_RE.search(text):
        return (
            "This is useful as a geopolitical signal if it changes leverage, escalation risk, alliances or negotiating room. "
            "The concrete value is the shift in behaviour by governments, militaries or markets."
        )

    # Omit low-value generic why for ordinary politics, public-person and incident items.
    return ""


def fetch_section_contextual(section_key: str, excluded_links=None, excluded_topics=None):
    items = _original_fetch_section(section_key, excluded_links, excluded_topics)
    filtered = []
    for it in items:
        title = it.get("title", "") or ""
        snippet = it.get("summary_raw", "") or it.get("ai_key_point", "") or ""
        if is_weather_item(title, snippet):
            continue
        why = context_why_it_matters_en(section_key, title, snippet)
        it["ai_why_it_matters"] = _clip_sentence(why, 360) if why_is_useful_en(why) else ""
        it["ai_model"] = ((it.get("ai_model") or "") + "+contextual-why-en-v2").strip("+")
        filtered.append(it)
    return filtered


def add_news_tabs_en(html: str) -> str:
    if "class=\"section-tabs\"" not in html:
        html = html.replace("</style>", NEWS_TABS_CSS + "\n  </style>", 1)
        html = html.replace("<main>\n", "<main>\n" + NEWS_TABS_HTML + "\n", 1)

    for title, section_id in SECTION_IDS.items():
        html = html.replace(
            f'<section class="card">\n  <h2>{title}</h2>',
            f'<section class="card" id="{section_id}">\n  <h2>{title}</h2>',
            1,
        )
    return html


def render_html_contextual(sections: dict) -> str:
    html = _original_render_html(sections)
    # Remove empty Why-it-matters rows if the base renderer still created the label.
    html = re.sub(r"\n\s*<div class=\"sec\"><strong>Why it matters:</strong>\s*</div>", "", html)
    html = add_news_tabs_en(html)
    return html


base.fetch_section = fetch_section_contextual
base.render_html = render_html_contextual

if __name__ == "__main__":
    base.main()
