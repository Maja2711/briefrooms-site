from __future__ import annotations

import json
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import publish_urgent_news as urgent
from scripts.news_event_intelligence_v4 import cluster_events
from scripts.news_source_expansion_v3 import deduplicate_dispatches


NOW = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)


def row(source: str, title: str, number: int, *, minutes: int = 10, summary: str = "") -> dict:
    return {
        "title": title,
        "source": source,
        "summary": summary or title,
        "link": f"https://example.com/breaking-{number}",
        "image": f"https://images.example.com/breaking-{number}.jpg",
        "published_at": (NOW - timedelta(minutes=minutes)).isoformat(),
        "published_at_basis": "source",
    }


class UrgentNewsTests(unittest.TestCase):
    def test_hourly_publisher_is_not_imported_as_an_execution_entrypoint(self) -> None:
        self.assertEqual(urgent.MAX_CARDS, 3)
        self.assertEqual(urgent.MAX_SOURCE_AGE, timedelta(minutes=75))
        self.assertEqual(urgent.MAX_DISPLAY, timedelta(minutes=90))

    def test_tv_program_promotions_never_enter_urgent_fast_lane(self) -> None:
        for title in (
            '"Śniadanie Rymanowskiego" w Polsat News i Interii',
            "Sportowy wieczór (10.10.2026)",
            "CNN Tonight (October 10, 2026)",
        ):
            with self.subTest(title=title):
                candidate = row("BBC News", title, 1)
                self.assertFalse(urgent.fresh_candidate(candidate, NOW))

    def test_single_editorial_source_is_not_sufficient_for_breaking(self) -> None:
        stories, _ = urgent.build_lang(
            "en", [row("BBC News", "Russia launches missile attack on Ukraine", 1)],
            {}, [], NOW,
        )
        self.assertEqual(stories, [])

    def test_two_independent_sources_may_confirm_breaking(self) -> None:
        news = [
            row("BBC News", "Russia launches missile attack on Ukraine", 1),
            row("The Guardian", "Russia launches missile attack on Ukraine", 2),
        ]
        selected, seen = urgent.build_lang("en", news, {}, [], NOW)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["independent_evidence_paths"], 2)
        self.assertEqual(len(seen), 1)
        self.assertEqual(selected[0]["news_expires_at"],
                         (NOW - timedelta(minutes=10) + timedelta(minutes=90)).isoformat(timespec="seconds"))

    def test_reuters_republications_are_one_lineage_not_two_confirmations(self) -> None:
        title = "Russia launches missile attack on Ukraine"
        copies = [
            row("BBC News", title, 1, summary="Reuters reports Russia launched the attack on Ukraine"),
            row("The Guardian", title, 2, summary="According to Reuters, Russia launched the attack on Ukraine"),
        ]
        stories, _ = urgent.build_lang("en", copies, {}, [], NOW)
        self.assertEqual(stories, [])

    def test_primary_first_party_decision_needs_no_second_publisher(self) -> None:
        event = row("ECB", "ECB cuts interest rates in monetary policy decision", 1)
        stories, _ = urgent.build_lang("en", [event], {}, [], NOW)
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0]["origin_source"], "ECB")

    def test_speculative_or_untimestamped_or_old_material_is_rejected(self) -> None:
        speculative = row("ECB", "ECB may cut interest rates tomorrow", 1)
        missing = row("ECB", "ECB cuts interest rates in monetary policy decision", 2)
        missing["published_at"] = None
        old = row("ECB", "ECB cuts interest rates in monetary policy decision", 3, minutes=76)
        stories, _ = urgent.build_lang("en", [speculative, missing, old], {}, [], NOW)
        self.assertEqual(stories, [])

    def test_canonical_story_is_not_republished_in_breaking_rail(self) -> None:
        event = row("ECB", "ECB cuts interest rates in monetary policy decision", 1)
        stories, _ = urgent.build_lang("en", [event], {}, [event], NOW)
        self.assertEqual(stories, [])

    def test_first_exposure_clock_never_resets_on_repeat(self) -> None:
        story = row("ECB", "ECB cuts interest rates in monetary policy decision", 1)
        first, state = urgent.build_lang("en", [story], {}, [], NOW)
        self.assertEqual(len(first), 1)
        later = NOW + timedelta(minutes=9)
        again, _ = urgent.build_lang("en", [story], state, [], later)
        self.assertEqual(again[0]["news_first_seen_at"], first[0]["news_first_seen_at"])
        self.assertEqual(again[0]["news_expires_at"], first[0]["news_expires_at"])

    def test_underfill_is_valid_and_future_timestamp_fails(self) -> None:
        payload, _ = urgent.assemble({"pl": [], "en": []}, {}, {}, NOW, {})
        urgent.validate(payload, NOW)
        self.assertEqual(payload["languages"]["pl"]["stories"], [])
        invalid = json.loads(json.dumps(payload))
        invalid["checked_at"] = (NOW + timedelta(minutes=10)).isoformat()
        with self.assertRaises(ValueError):
            urgent.validate(invalid, NOW)

    def test_browser_rail_never_writes_regular_news_containers(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts/urgent-news-live.js"
        completed = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = script.read_text(encoding="utf-8")
        self.assertIn("45 * 60 * 1000", source)
        self.assertIn("3 * 60 * 1000", source)
        self.assertIn("briefrooms-urgent-news-v1", source)
        self.assertNotIn("latest-briefs').innerHTML", source)


if __name__ == "__main__":
    unittest.main()
