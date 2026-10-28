from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import pl_sport_priority as priority
from scripts import publish_live_news_filtered as filtered
from scripts import publish_source_expansion_v3 as publisher


NOW = datetime(2026, 10, 28, 19, 0, tzinfo=timezone.utc)
ARTICLE = "https://polskieradio24.pl/sport/hurkacz-rune-mecz"


class PLPrioritySportTests(unittest.TestCase):
    def test_curated_hurkacz_fixture_is_validated_not_invented(self) -> None:
        events = {"events": [{
            "event_date_warsaw": "2026-10-28", "source": "Polskie Radio 24",
            "publisher_host": "polskieradio24.pl",
            "title": "Hubert Hurkacz – Holger Rune: dziś mecz w Bazylei",
            "link": ARTICLE,
            "published_at": "2026-10-28T14:37:00+00:00",
        }]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            path.write_text(json.dumps(events), encoding="utf-8")
            with (
                patch.object(priority, "EVENTS_PATH", path),
                patch.object(priority.base, "page_metadata", return_value=(
                    "https://polskieradio24.pl/images/hurkacz.jpg",
                    NOW - timedelta(hours=4, minutes=23),
                )),
            ):
                rows, warnings = priority.collect(NOW, listings=())
                self.assertEqual(len(rows), 1)
                self.assertFalse(warnings)
                self.assertTrue(rows[0]["sport_priority_fixture"])
                self.assertEqual(rows[0]["published_at"], "2026-10-28T14:37:00+00:00")
                next_day, _ = priority.collect(
                    NOW + timedelta(days=1), listings=(),
                )
                self.assertEqual(next_day, [])

    def test_fixture_requires_real_photo_and_source_date(self) -> None:
        with patch.object(priority.base, "page_metadata", return_value=("", NOW)):
            self.assertIsNone(priority.verified_story(
                "Hubert Hurkacz zagra mecz w Bazylei",
                ARTICLE, "Polskie Radio 24", NOW,
            ))
        with patch.object(priority.base, "page_metadata", return_value=(
            "https://polskieradio24.pl/image.jpg", None
        )):
            self.assertIsNone(priority.verified_story(
                "Hubert Hurkacz zagra mecz w Bazylei",
                ARTICLE, "Polskie Radio 24", NOW,
            ))
        with patch.object(priority.base, "page_metadata", return_value=(
            "https://polskieradio24.pl/image.jpg", NOW - timedelta(days=3)
        )):
            self.assertIsNone(priority.verified_story(
                "Hubert Hurkacz zagra mecz w Bazylei",
                ARTICLE, "Polskie Radio 24", NOW,
            ))

    def test_listing_discovers_top_athlete_fixture_not_generic_chatter(self) -> None:
        html = (
            '<a href="/sport/hurkacz-rune-mecz">Hurkacz kontra Rune. Dziś mecz ATP</a>'
            '<a href="/sport/hurkacz-rozmowa">Hurkacz opowiedział o wakacjach</a>'
            '<a href="https://malicious.example/sport/hurkacz">Hurkacz gra dziś</a>'
        )
        rows = priority.discover_links(
            html, "https://polskieradio24.pl/", "polskieradio24.pl",
            priority._athlete_patterns(),
        )
        self.assertEqual(rows, [
            ("Hurkacz kontra Rune. Dziś mecz ATP", ARTICLE),
        ])

    def test_priority_event_receives_a_slot_before_generic_sport(self) -> None:
        now = NOW
        generic = [{
            "title": f"Sportowe wydarzenie numer {i} w Polsce",
            "summary": f"Osobne wydarzenie sportowe numer {i}",
            "source": f"Publisher {i % 5}",
            "link": f"https://example.com/sport-{i}",
            "image": f"https://example.com/sport-{i}.jpg",
            "published_at": (now - timedelta(minutes=i)).isoformat(),
        } for i in range(20)]
        event = {
            "title": "Hubert Hurkacz – Holger Rune: dziś mecz o ćwierćfinał ATP",
            "summary": "Dziś mecz o ćwierćfinał ATP Basel",
            "source": "Polskie Radio 24",
            "link": ARTICLE,
            "image": "https://polskieradio24.pl/hurkacz.jpg",
            "published_at": (now - timedelta(hours=4)).isoformat(),
            "sport_priority_fixture": True,
        }
        selected, _ = filtered.select_sections(
            [("sport", "Sport", []), ("polityka", "Polityka", [])],
            {"sport": generic + [event], "polityka": []},
            {"sections": {}}, now,
        )
        self.assertTrue(any(s.get("sport_priority_fixture") for s in selected["sport"]))
        self.assertEqual(selected["sport"][0]["link"], ARTICLE)

    def test_discovery_failure_does_not_impact_other_sections_or_english(self) -> None:
        grouped = {"sport": [], "polityka": [], "zdrowie": []}
        with (
            patch.object(publisher, "_original_fetch_all", return_value=(
                grouped, {"sport": "Sport"}, [],
            )),
            patch.object(priority, "collect", side_effect=RuntimeError("offline")),
        ):
            result, _, errors = publisher.fetch_all([], NOW)
        self.assertEqual(errors, [])
        self.assertEqual(result, grouped)
        with (
            patch.object(publisher, "_original_fetch_all", return_value=(
                {"sport": [], "world-news": []}, {"sport": "Sport"}, [],
            )),
            patch.object(priority, "collect", side_effect=AssertionError("EN must not run")),
        ):
            result, _, _ = publisher.fetch_all([], NOW)
        self.assertEqual(result["sport"], [])


if __name__ == "__main__":
    unittest.main()
