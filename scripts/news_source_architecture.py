#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

SOURCE_POLICY_VERSION = "curated-source-tiers-v2"
TARGET_MAX_SOURCE_CARDS = 3
EMERGENCY_MAX_SOURCE_CARDS = 5

# Lower rank means a stronger provenance class. Authority is a ranking input, not
# a publication bypass: every story still has to pass BriefRooms editorial filters.
TIER_ORDER = ("primary", "wire", "premium", "quality", "broad", "unknown")
TIER_AUTHORITY_BONUS = {
    "primary": 62.0,
    "wire": 56.0,
    "premium": 46.0,
    "quality": 26.0,
    "broad": 10.0,
    "unknown": 0.0,
}


@dataclass(frozen=True)
class SourceProfile:
    canonical_name: str
    tier: str
    source_type: str
    acquisition: str
    parent: str | None = None


# Architecture registry. Reuters/AP/PAP are deliberately modeled even where a
# stable/licensed direct adapter is not enabled yet. This prevents the ranking
# layer from being coupled to today's RSS availability.
_SOURCE_PROFILES: dict[str, SourceProfile] = {}


def _register(
    canonical_name: str,
    *,
    tier: str,
    source_type: str,
    acquisition: str,
    aliases: Iterable[str] = (),
    parent: str | None = None,
) -> None:
    profile = SourceProfile(
        canonical_name=canonical_name,
        tier=tier,
        source_type=source_type,
        acquisition=acquisition,
        parent=parent,
    )
    for alias in (canonical_name, *aliases):
        _SOURCE_PROFILES[normalize_source_name(alias)] = profile


def normalize_source_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("/", " ").split())


# Tier 0: institutions publishing the underlying decision/data/release.
_register("NBP", tier="primary", source_type="institution", acquisition="official", aliases=("Narodowy Bank Polski",))
_register("ECB", tier="primary", source_type="institution", acquisition="official", aliases=("European Central Bank",))
_register("Federal Reserve", tier="primary", source_type="institution", acquisition="official", aliases=("Federal Reserve Board", "FOMC"))
_register("Eurostat", tier="primary", source_type="institution", acquisition="official")
_register("European Commission", tier="primary", source_type="institution", acquisition="official", aliases=("Komisja Europejska",))
_register("WHO", tier="primary", source_type="institution", acquisition="official", aliases=("World Health Organization",))
_register("FDA", tier="primary", source_type="institution", acquisition="official", aliases=("U.S. FDA",))
_register("EMA", tier="primary", source_type="institution", acquisition="official", aliases=("European Medicines Agency",))
_register("NASA", tier="primary", source_type="institution", acquisition="official")
_register("NASA JPL", tier="primary", source_type="institution", acquisition="official", aliases=("JPL", "Jet Propulsion Laboratory"), parent="NASA")
_register("ESA", tier="primary", source_type="institution", acquisition="official", aliases=("European Space Agency",))

# Tier 1: news wires / agencies. Direct adapters may require commercial access.
_register("Reuters", tier="wire", source_type="wire", acquisition="licensed_adapter_preferred", aliases=("Reuters News",))
_register("Associated Press", tier="wire", source_type="wire", acquisition="licensed_adapter_preferred", aliases=("AP", "AP News"))
_register("PAP", tier="wire", source_type="wire", acquisition="direct_or_licensed", aliases=("Polska Agencja Prasowa",))
_register("Nauka w Polsce", tier="wire", source_type="specialist_wire_desk", acquisition="public_rss", parent="PAP")

# Tier 2: premium global desks.
_register("BBC News", tier="premium", source_type="global_media", acquisition="public_rss", aliases=("BBC", "BBC Business", "BBC Science", "BBC Health", "BBC Sport"))
_register("Financial Times", tier="premium", source_type="global_media", acquisition="best_effort_public_rss", aliases=("FT", "FT Markets"))
_register("Bloomberg", tier="premium", source_type="global_media", acquisition="best_effort_public_rss", aliases=("Bloomberg Markets", "Bloomberg Politics"))

# Tier 3: strong specialist / national desks already used by BriefRooms.
for _name in (
    "Rzeczpospolita",
    "The Guardian",
    "Bankier.pl",
    "Business Insider Polska",
    "TVP Sport",
    "Przegląd Sportowy / Onet Sport",
    "SportoweFakty WP",
):
    _register(_name, tier="quality", source_type="editorial_desk", acquisition="public_rss")

# Tier 4: broad-reach desks. Useful for coverage and speed, but they should not
# dominate a curated nine-card section when higher-authority alternatives exist.
for _name in (
    "TVN24",
    "Polsat News",
    "RMF24",
    "Polsat Sport",
    "RMF24 Sport",
    "Interia Sport",
):
    _register(_name, tier="broad", source_type="broad_media", acquisition="public_rss")


# Verified/best-effort feeds that enrich the existing English desks. Feed failure
# is non-fatal in the canonical fetcher; these are additional sources, not single
# points of failure.
EXTRA_FEEDS: dict[str, dict[str, tuple[tuple[str, str], ...]]] = {
    "en": {
        "business": (
            ("ECB", "https://www.ecb.europa.eu/rss/press.html"),
            ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
            ("Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss"),
            ("Financial Times", "https://www.ft.com/markets?format=rss"),
        ),
        "science": (
            ("ESA", "https://www.esa.int/rssfeed/Our_Activities/Space_Science"),
            ("NASA JPL", "https://www.jpl.nasa.gov/feeds/news/"),
        ),
        "health": (
            ("FDA", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"),
        ),
    },
    "pl": {},
}


def source_profile(source: Any) -> SourceProfile:
    key = normalize_source_name(source)
    return _SOURCE_PROFILES.get(
        key,
        SourceProfile(
            canonical_name=str(source or "unknown").strip() or "unknown",
            tier="unknown",
            source_type="unknown",
            acquisition="unknown",
        ),
    )


def source_authority_bonus(source: Any) -> float:
    return TIER_AUTHORITY_BONUS[source_profile(source).tier]


def source_tier_rank(source: Any) -> int:
    try:
        return TIER_ORDER.index(source_profile(source).tier)
    except ValueError:
        return len(TIER_ORDER) - 1


def extend_config(config: Any, lang: str) -> list[Any]:
    """Add curated reserve feeds without duplicating an existing URL."""
    additions_by_section = EXTRA_FEEDS.get(lang, {})
    extended: list[Any] = []
    for section_id, label, feeds in config:
        merged = list(feeds)
        seen_urls = {url for _, url in merged}
        for source, url in additions_by_section.get(section_id, ()): 
            if url not in seen_urls:
                merged.append((source, url))
                seen_urls.add(url)
        extended.append((section_id, label, merged))
    return extended


def tier_mix(stories: Iterable[dict[str, Any]]) -> dict[str, int]:
    result = {tier: 0 for tier in TIER_ORDER}
    for story in stories:
        result[source_profile(story.get("source")).tier] += 1
    return {tier: count for tier, count in result.items() if count}


def source_mix(stories: Iterable[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for story in stories:
        source = str(story.get("source") or "unknown").strip() or "unknown"
        result[source] = result.get(source, 0) + 1
    return result


def diversity_status(stories: Iterable[dict[str, Any]]) -> str:
    counts = source_mix(stories)
    if not counts:
        return "empty"
    maximum = max(counts.values())
    if maximum <= TARGET_MAX_SOURCE_CARDS:
        return "target"
    if maximum <= EMERGENCY_MAX_SOURCE_CARDS:
        return "emergency_fallback"
    return "violation"


def public_source_policy() -> dict[str, Any]:
    return {
        "version": SOURCE_POLICY_VERSION,
        "authority_order": list(TIER_ORDER[:-1]),
        "target_max_cards_per_source": TARGET_MAX_SOURCE_CARDS,
        "emergency_max_cards_per_source": EMERGENCY_MAX_SOURCE_CARDS,
        "rule": "Authority improves ranking but never bypasses editorial quality filters.",
        "preferred_sources": [
            "primary institutions",
            "Reuters / Associated Press / PAP",
            "BBC / Financial Times / Bloomberg",
        ],
        "licensed_adapter_candidates": ["Reuters", "Associated Press", "PAP"],
    }
