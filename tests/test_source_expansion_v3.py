from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts import news_source_expansion_v3 as expansion


NOW = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)


def story(source: str, title: str, summary: str, index: int = 0, image: str = "https://example.com/image.jpg") -> dict:
    return {
        "source": source,
        "title": title,
        "summary": summary,
        "link": f"https://example.com/story-{index}",
        "image": image,
        "published_at": (NOW - timedelta(minutes=index)).isoformat(),
        "published_at_basis": "source",
    }


class SourceExpansionV3Tests(unittest.TestCase):
    def test_wire_adapters_are_registered_without_committed_credentials(self) -> None:
        adapters = {item.canonical_source: item for item in expansion.WIRE_ADAPTERS}
        self.assertEqual(set(adapters), {"Reuters", "Associated Press", "PAP"})
        self.assertTrue(all(item.env_var.startswith("BRIEFROOMS_") for item in adapters.values()))

    def test_adapter_config_accepts_only_http_feed_urls(self) -> None:
        feeds, diagnostics = expansion.configured_wire_feeds(
            {
                "BRIEFROOMS_REUTERS_FEEDS_JSON": '{"en":{"world-news":"https://wire.example/reuters.xml"}}',
                "BRIEFROOMS_AP_FEEDS_JSON": '{"en":{"business":"file:///tmp/ap.xml"}}',
            }
        )
        self.assertEqual(feeds["en"]["world-news"], [("Reuters", "https://wire.example/reuters.xml")])
        self.assertTrue(diagnostics["Reuters"]["configured"])
        self.assertFalse(diagnostics["Associated Press"]["configured"])
        self.assertEqual(diagnostics["Associated Press"]["error"], "no_valid_feed_urls")

    def test_direct_wire_story_is_original(self) -> None:
        result = expansion.annotate_story_provenance(
            story("Reuters", "Fed keeps rates unchanged", "Federal Reserve decision", 1)
        )
        self.assertEqual(result["origin_source"], "Reuters")
        self.assertEqual(result["provenance_role"], "original")
        self.assertEqual(result["origin_confidence"], 1.0)

    def test_pap_first_party_desk_is_original_desk(self) -> None:
        result = expansion.annotate_story_provenance(
            story("Nauka w Polsce", "Nowe badanie", "Polscy badacze opublikowali wyniki", 2)
        )
        self.assertEqual(result["origin_source"], "PAP")
        self.assertEqual(result["provenance_role"], "original_desk")

    def test_polish_reuters_attribution_is_detected(self) -> None:
        result = expansion.annotate_story_provenance(
            story(
                "TVN24",
                "Amazon pozwany przez regulatora",
                "Jak poinformował Reuters, Federalna Komisja Handlu pozwała Amazon.",
                3,
            )
        )
        self.assertEqual(result["origin_source"], "Reuters")
        self.assertEqual(result["origin_basis"], "explicit_wire_attribution")
        self.assertEqual(result["provenance_role"], "republication")

    def test_bare_reuters_company_mention_is_not_false_origin(self) -> None:
        result = expansion.annotate_story_provenance(
            story(
                "TVN24",
                "Reuters uruchamia nowy produkt",
                "Firma Reuters przedstawiła usługę dla klientów finansowych.",
                4,
            )
        )
        self.assertIsNone(result["origin_source"])
        self.assertEqual(result["provenance_role"], "publisher_unverified")

    def test_same_wire_dispatch_collapses_and_direct_original_wins(self) -> None:
        direct = story(
            "Reuters",
            "FTC sues Amazon over advertising auction practices",
            "The Federal Trade Commission sued Amazon over advertising auction practices and alleged price manipulation.",
            5,
        )
        bbc = story(
            "BBC News",
            "FTC sues Amazon over advertising auctions",
            "Reuters reported that the Federal Trade Commission sued Amazon over advertising auction practices and alleged price manipulation.",
            6,
        )
        guardian = story(
            "The Guardian",
            "Amazon faces FTC lawsuit over ad auction practices",
            "According to Reuters, the Federal Trade Commission sued Amazon over advertising auction practices and alleged price manipulation.",
            7,
        )
        rows, diagnostics = expansion.deduplicate_dispatches([bbc, guardian, direct])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "Reuters")
        self.assertEqual(rows[0]["dispatch_cluster_size"], 3)
        self.assertEqual(rows[0]["suppressed_publishers"], ["BBC News", "The Guardian"])
        self.assertEqual(diagnostics["suppressed_count"], 2)
        self.assertEqual(diagnostics["direct_original_wins"], 1)

    def test_unrelated_reuters_attributions_do_not_collapse(self) -> None:
        rates = story(
            "BBC News",
            "Fed holds rates steady after meeting",
            "Reuters reported that the Federal Reserve held rates steady after its meeting.",
            8,
        )
        oil = story(
            "The Guardian",
            "Oil rises as OPEC cuts supply",
            "Reuters reported oil prices rose after OPEC announced supply cuts.",
            9,
        )
        rows, diagnostics = expansion.deduplicate_dispatches([rates, oil])
        self.assertEqual(len(rows), 2)
        self.assertEqual(diagnostics["suppressed_count"], 0)

    def test_renderable_republication_can_fallback_if_original_has_no_image(self) -> None:
        direct = story(
            "Reuters",
            "ECB signals policy change after inflation report",
            "ECB signalled a policy change after the inflation report surprised markets.",
            10,
            image="",
        )
        republished = story(
            "BBC News",
            "ECB signals policy change after inflation report",
            "Reuters reported the ECB signalled a policy change after the inflation report surprised markets.",
            11,
        )
        rows, _ = expansion.deduplicate_dispatches([direct, republished])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "BBC News")
        self.assertEqual(rows[0]["origin_source"], "Reuters")
        self.assertEqual(rows[0]["provenance_role"], "republication")


if __name__ == "__main__":
    unittest.main()
