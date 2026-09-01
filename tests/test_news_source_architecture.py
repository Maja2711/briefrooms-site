from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts import publish_live_news as base
from scripts import publish_curated_news as curated
from scripts.news_source_architecture import (
    EMERGENCY_MAX_SOURCE_CARDS,
    SOURCE_POLICY_VERSION,
    TARGET_MAX_SOURCE_CARDS,
    extend_config,
    source_authority_bonus,
    source_profile,
)


def story(source: str, index: int, now: datetime) -> dict:
    return {
        "title": f"Rząd przyjął ustawę o bezpieczeństwie publicznym numer {index}",
        "link": f"https://example.com/{source.lower().replace(' ', '-')}-{index}",
        "image": f"https://example.com/{index}.jpg",
        "source": source,
        "summary": "Nowe przepisy dotyczą bezpieczeństwa publicznego i administracji państwowej.",
        "published_at": (now - timedelta(minutes=index)).isoformat(),
        "published_at_basis": "source",
    }


class CuratedSourceArchitectureTests(unittest.TestCase):
    def test_requested_high_authority_sources_are_modeled(self) -> None:
        self.assertEqual(source_profile("Reuters").tier, "wire")
        self.assertEqual(source_profile("AP News").tier, "wire")
        self.assertEqual(source_profile("PAP").tier, "wire")
        self.assertEqual(source_profile("BBC News").tier, "premium")
        self.assertEqual(source_profile("Financial Times").tier, "premium")
        self.assertEqual(source_profile("Bloomberg Markets").tier, "premium")
        self.assertEqual(source_profile("ECB").tier, "primary")
        self.assertEqual(source_profile("Federal Reserve").tier, "primary")
        self.assertEqual(source_profile("FDA").tier, "primary")
        self.assertEqual(source_profile("ESA").tier, "primary")

    def test_pap_science_desk_inherits_wire_tier(self) -> None:
        profile = source_profile("Nauka w Polsce")
        self.assertEqual(profile.tier, "wire")
        self.assertEqual(profile.parent, "PAP")

    def test_authority_order_beats_broad_media_for_equal_story_value(self) -> None:
        self.assertGreater(source_authority_bonus("ECB"), source_authority_bonus("Reuters"))
        self.assertGreater(source_authority_bonus("Reuters"), source_authority_bonus("BBC News"))
        self.assertGreater(source_authority_bonus("BBC News"), source_authority_bonus("Rzeczpospolita"))
        self.assertGreater(source_authority_bonus("Rzeczpospolita"), source_authority_bonus("TVN24"))

    def test_curated_score_uses_source_authority(self) -> None:
        now = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
        primary = story("ECB", 1, now)
        broad = story("TVN24", 1, now)
        self.assertGreater(
            curated.curated_editorial_value_score(primary, "ekonomia", now),
            curated.curated_editorial_value_score(broad, "ekonomia", now),
        )

    def test_verified_primary_and_premium_feeds_extend_english_desks(self) -> None:
        config = extend_config(base.EN, "en")
        by_section = {section_id: feeds for section_id, _, feeds in config}
        business_sources = {source for source, _ in by_section["business"]}
        science_sources = {source for source, _ in by_section["science"]}
        health_sources = {source for source, _ in by_section["health"]}
        self.assertTrue({"ECB", "Federal Reserve", "Bloomberg Markets", "Financial Times"} <= business_sources)
        self.assertTrue({"ESA", "NASA JPL"} <= science_sources)
        self.assertIn("FDA", health_sources)

    def test_optional_premium_feed_failure_is_not_required_source_failure(self) -> None:
        required, optional = curated._split_source_errors(
            "en",
            [
                "Financial Times: HTTP 403",
                "Bloomberg Markets: timeout",
                "BBC Business: connection reset",
            ],
        )
        self.assertEqual(required, ["BBC Business: connection reset"])
        self.assertEqual(
            optional,
            ["Financial Times: HTTP 403", "Bloomberg Markets: timeout"],
        )

    def test_three_source_pool_targets_three_cards_per_publisher(self) -> None:
        now = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
        candidates = []
        for source_index, source in enumerate(("Reuters", "BBC News", "TVN24")):
            candidates.extend(story(source, source_index * 20 + i, now) for i in range(6))

        selected, health = curated.select_sections(
            [("polityka", "Polityka", [])],
            {"polityka": candidates},
            {"sections": {}},
            now,
        )
        rows = selected["polityka"]
        counts: dict[str, int] = {}
        for item in rows:
            counts[item["source"]] = counts.get(item["source"], 0) + 1

        self.assertEqual(len(rows), 9)
        self.assertLessEqual(max(counts.values()), TARGET_MAX_SOURCE_CARDS)
        self.assertEqual(health["polityka"]["source_diversity_status"], "target")
        self.assertEqual(health["polityka"]["source_policy_version"], SOURCE_POLICY_VERSION)

    def test_two_source_shortage_uses_explicit_emergency_not_silent_dominance(self) -> None:
        now = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
        candidates = [story("Reuters", i, now) for i in range(7)]
        candidates += [story("BBC News", 20 + i, now) for i in range(7)]

        selected, health = curated.select_sections(
            [("world-news", "World", [])],
            {"world-news": candidates},
            {"sections": {}},
            now,
        )
        counts: dict[str, int] = {}
        for item in selected["world-news"]:
            counts[item["source"]] = counts.get(item["source"], 0) + 1

        self.assertEqual(len(selected["world-news"]), 9)
        self.assertGreater(max(counts.values()), TARGET_MAX_SOURCE_CARDS)
        self.assertLessEqual(max(counts.values()), EMERGENCY_MAX_SOURCE_CARDS)
        self.assertEqual(health["world-news"]["source_diversity_status"], "emergency_fallback")


if __name__ == "__main__":
    unittest.main()
