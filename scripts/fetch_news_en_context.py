#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Contextual EN news builder for BriefRooms.

This wrapper keeps the existing EN feed selection/rendering logic from
scripts/fetch_news_en.py, but replaces generic "Why it matters" text with a
context-aware editorial note, mirroring the improved PL model.
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
    r"\b(wealth|net worth|salary|pension|allowance|expenses?|assets?|disclosure|minister|president|prime minister|"
    r"lawmaker|mp|senator|mayor|governor|public official|politician|ethics|conflict of interest)\b",
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
SPORT_RE = re.compile(r"\b(match|game|score|league|tournament|championship|world cup|football|soccer|tennis|basketball|nba|nfl|nhl|mlb|f1|formula 1|grand prix|wins?|beats?|defeats?)\b", re.I)
GEOPOLITICS_RE = re.compile(
    r"\b(war|ceasefire|sanctions?|nato|security|defen[cs]e|missile|drone|border|summit|treaty|"
    r"iran|israel|gaza|ukraine|russia|china|taiwan|north korea|south china sea)\b",
    re.I,
)
GENERIC_BAD_WHY_RE = re.compile(
    r"\b(single-source item selected from a priority BriefRooms feed|selected from a priority feed|"
    r"it matters because it affects public decisions, safety or daily life|"
    r"it may affect prices, companies, jobs or household decisions)\b",
    re.I,
)


def _clip_sentence(text: str, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return base.ensure_period(text)
    cut = text[:limit].rsplit(" ", 1)[0]
    return base.ensure_period(cut)


def context_why_it_matters_en(section_key: str, title: str, snippet: str) -> str:
    """Return a concrete, contextual EN editorial note. No generic slogans."""
    text = f"{title} {snippet}"

    if section_key == "sport" or SPORT_RE.search(text):
        return (
            "This is a results-and-form signal: its importance depends on the table, qualification picture or momentum around the next fixture. "
            "The useful follow-up is whether the result changes ranking, pressure or tactical expectations."
        )

    if PUBLIC_PERSON_RE.search(text):
        return (
            "This matters as an accountability story rather than a macroeconomic signal. "
            "It points to how public figures disclose income, assets or privileges, and whether those facts meet expectations of transparency."
        )

    if LOCAL_INCIDENT_RE.search(text):
        return (
            "The significance is local and institutional: the key issue is safety, procedure and how authorities respond. "
            "The important question is whether this is an isolated event or evidence of a broader operational weakness."
        )

    if FUEL_ENERGY_RE.search(text):
        return (
            "Energy and fuel stories matter because they pass quickly into transport costs, household bills and business margins. "
            "The key is whether this is a one-off price move or the beginning of a wider cost trend."
        )

    if section_key == "business" and MACRO_ECON_RE.search(text):
        return (
            "This is a macro signal: it can change expectations for inflation, demand, company costs or central-bank policy. "
            "What matters most is the gap between the data and market expectations, not the headline number alone."
        )

    if PUBLIC_POLICY_RE.search(text):
        return (
            "This matters because policy decisions usually create concrete costs, rights or obligations for citizens and institutions. "
            "The practical test is who is affected, when the rule starts and who pays for it."
        )

    if section_key in {"world", "asia_pacific", "europe", "middle_east"} and GEOPOLITICS_RE.search(text):
        return (
            "This is important as a geopolitical signal: it may shift alliances, risk perception or negotiating leverage. "
            "The next thing to watch is whether leaders, militaries or markets treat it as escalation, de-escalation or positioning."
        )

    if HEALTH_RE.search(text):
        return (
            "In health stories, the importance depends on scale, evidence and whether guidance changes for patients, doctors or public systems. "
            "The headline should be checked against the source, the population affected and the strength of the recommendation."
        )

    if SCIENCE_RE.search(text):
        return (
            "This matters if the finding changes how a problem is understood, not merely because it sounds striking. "
            "The useful checks are the method, the source and whether the result has independent confirmation."
        )

    if section_key == "business":
        return (
            "This is worth watching if it reveals a change in company behaviour, consumer demand or regulatory pressure. "
            "The key is separating a one-off corporate story from information that can change market decisions."
        )

    return (
        "This is a news signal whose value depends on whether it explains a wider process or only a single event. "
        "The source should be checked for details before drawing broader conclusions."
    )


_original_fetch_section = base.fetch_section


def fetch_section_contextual(section_key: str, excluded_links=None, excluded_topics=None):
    items = _original_fetch_section(section_key, excluded_links, excluded_topics)
    for it in items:
        title = it.get("title", "") or ""
        snippet = it.get("summary_raw", "") or it.get("ai_key_point", "") or ""
        why = context_why_it_matters_en(section_key, title, snippet)
        it["ai_why_it_matters"] = _clip_sentence(why, 360)
        it["ai_model"] = ((it.get("ai_model") or "") + "+contextual-why-en-v1").strip("+")
        if GENERIC_BAD_WHY_RE.search(it.get("ai_why_it_matters", "")):
            it["ai_why_it_matters"] = _clip_sentence(context_why_it_matters_en(section_key, title, snippet), 360)
    return items


base.fetch_section = fetch_section_contextual

if __name__ == "__main__":
    base.main()
