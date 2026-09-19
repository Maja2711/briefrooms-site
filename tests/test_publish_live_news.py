from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from scripts.publish_live_news import MIN_SECTION, TARGET, normalized_identity, parse_entry_time, select_sections
from scripts.dedupe_home_brief_stories import same_topic
from scripts.publish_source_expansion_v3 import homepage_ranked_select


class LiveNewsPublisherTests(unittest.TestCase):
    def test_every_section_targets_nine_cards(self) -> None:
        self.assertEqual(TARGET, 9)
        self.assertEqual(MIN_SECTION, TARGET)

    def test_parse_entry_time_uses_feed_timestamp(self) -> None:
        entry = SimpleNamespace(published_parsed=(2026, 8, 3, 6, 30, 0, 0, 0, 0))
        value = parse_entry_time(entry)
        self.assertEqual(value, datetime(2026, 8, 3, 6, 30, tzinfo=timezone.utc))

    def test_identity_ignores_tracking_query(self) -> None:
        first = normalized_identity({"link": "https://example.com/story?utm_source=rss"})
        second = normalized_identity({"link": "https://example.com/story?source=home"})
        self.assertEqual(first, second)

    def test_section_uses_recent_previous_items_only_as_bounded_fallback(self) -> None:
        now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
        config = [("section", "Section", [("Example", "https://example.com/rss")])]
        fetched = {
            "section": [
                {"title": f"Fresh {index}", "link": f"https://example.com/fresh-{index}", "image": f"https://example.com/fresh-{index}.jpg", "source": "Example", "summary": "Fresh", "published_at": now.isoformat(), "published_at_basis": "source"}
                for index in range(MIN_SECTION - 1)
            ]
        }
        previous = {
            "sections": {
                "section": [
                    {"title": "Previous", "link": "https://example.com/previous", "image": "https://example.com/previous.jpg", "source": "Example", "summary": "Previous", "published_at": (now - timedelta(hours=3)).isoformat()}
                ]
            }
        }
        sections, health = select_sections(config, fetched, previous, now)
        self.assertEqual(len(sections["section"]), MIN_SECTION)
        self.assertEqual(health["section"]["carried_count"], 1)

    def test_homepage_topic_guard_collapses_same_source_noise_package(self) -> None:
        first = {
            "source": "Nauka w Polsce",
            "category": "Zdrowie",
            "title": "Eksperci: zmiany prawne to najskuteczniejszy sposób walki z hałasem w naszym otoczeniu",
            "summary": "Eksperci omawiają ograniczanie hałasu środowiskowego.",
            "published_at": "2026-09-18T10:50:00+00:00",
            "link": "https://example.com/halas-prawo",
        }
        second = {
            "source": "Nauka w Polsce",
            "category": "Zdrowie",
            "title": "Skąd się bierze hałas w miastach?",
            "summary": "Źródła hałasu w przestrzeni miejskiej.",
            "published_at": "2026-09-18T10:40:00+00:00",
            "link": "https://example.com/halas-miasta",
        }
        self.assertTrue(same_topic(first, second))

    def test_homepage_topic_guard_does_not_merge_unrelated_person_stories(self) -> None:
        first = {
            "source": "Example",
            "category": "Polityka",
            "title": "Trump spotka się z premierem Kanady",
            "summary": "Rozmowy dotyczą handlu.",
            "published_at": "2026-09-18T10:50:00+00:00",
            "link": "https://example.com/trump-kanada",
        }
        second = {
            "source": "Example",
            "category": "Polityka",
            "title": "Trump komentuje wyniki wyborów w Kalifornii",
            "summary": "Prezydent odniósł się do głosowania.",
            "published_at": "2026-09-18T10:40:00+00:00",
            "link": "https://example.com/trump-kalifornia",
        }
        self.assertFalse(same_topic(first, second))

    def test_homepage_ranker_prefers_global_quality_and_suppresses_topic_duplicate(self) -> None:
        now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
        def row(source: str, title: str, idx: int, section: str, summary: str | None = None) -> dict:
            return {
                "source": source,
                "publisher_source": source,
                "title": title,
                "summary": summary or (title + " — pełny opis materiału z dodatkowymi faktami i kontekstem."),
                "link": f"https://example.com/{section}/{idx}",
                "image": f"https://example.com/{idx}.jpg",
                "published_at": (now - timedelta(minutes=idx)).isoformat(),
                "published_at_basis": "source",
                "canonical_event_id": f"evt_{section}_{idx}",
                "corroboration_score": 50.0,
                "claim_adjusted_corroboration_score": 50.0,
                "independent_evidence_paths": 2,
                "claim_consistency_status": "consistent",
                "contradiction_score": 0.0,
                "provenance_role": "original",
                "origin_source": source,
            }
        sections = {
            "zdrowie": [
                row("Nauka w Polsce", "Eksperci: jak ograniczyć hałas w naszym otoczeniu", 1, "zdrowie"),
                row("Nauka w Polsce", "Skąd się bierze hałas w miastach?", 2, "zdrowie"),
                row("Nauka w Polsce", "Nowa metoda obrazowania komórek", 3, "zdrowie"),
            ],
            "polityka": [
                row("Source P", "Parlament przyjął ustawę o cyberbezpieczeństwie", 10, "polityka"),
                row("Source P", "Rząd przedstawił plan energetyczny", 11, "polityka"),
                row("Source P2", "Samorządy dostaną nowe finansowanie", 12, "polityka"),
                row("Source P2", "Ministerstwo zmienia zasady zamówień", 13, "polityka"),
            ],
            "ekonomia": [
                row("Source E", "Inflacja spadła poniżej prognoz", 20, "ekonomia"),
                row("Source E", "Bank centralny utrzymał stopy procentowe", 21, "ekonomia"),
                row("Source E2", "Eksport przemysłowy przyspieszył", 22, "ekonomia"),
                row("Source E2", "Rynek pracy dodał nowe etaty", 23, "ekonomia"),
            ],
            "nauka": [
                row("Source N", "Teleskop wykrył atmosferę odległej planety", 30, "nauka"),
                row("Source N", "Nowy materiał magazynuje energię cieplną", 31, "nauka"),
                row("Source N2", "Badacze opisali mechanizm regeneracji nerwów", 32, "nauka"),
                row("Source N2", "Robot laboratoryjny przyspiesza syntezę leków", 33, "nauka"),
            ],
            "sport": [
                row("Source S", "Polska wygrała mecz kwalifikacyjny", 40, "sport"),
                row("Source S", "Rekord kraju w biegu na 400 metrów", 41, "sport"),
                row("Source S2", "Tenisista awansował do finału turnieju", 42, "sport"),
                row("Source S2", "Kolarz wygrał etap wyścigu", 43, "sport"),
            ],
        }
        selected, diagnostics = homepage_ranked_select(
            sections,
            {key: key.title() for key in sections},
            limit=10,
            now=now,
        )
        titles = [item["title"] for item in selected]
        noise_count = sum("hałas" in title.casefold() for title in titles)
        self.assertEqual(len(selected), 10)
        self.assertEqual(noise_count, 1)
        self.assertEqual(diagnostics["version"], "homepage-editorial-v2")
        self.assertLessEqual(max(diagnostics["source_mix"].values()), 3)


if __name__ == "__main__":
    unittest.main()
