import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.news_story_dedupe import (
    audit_html,
    deduplicate_sections,
    load_recent_history,
    same_story,
    save_history,
)


class StoryDedupeTests(unittest.TestCase):
    def test_html_audit_ignores_repeated_source_and_ui_labels(self):
        html = """
        <ul>
          <li>
            <span class="news-text">Decisions taken by the Governing Council of the ECB</span>
            <div class="source-line">Source: ECB</div>
            <div class="ai-note"><div>Interest-rate and operational decisions were published.</div></div>
          </li>
          <li>
            <span class="news-text">ECB extends climate factors in collateral framework</span>
            <div class="source-line">Source: ECB</div>
            <div class="ai-note"><div>The collateral framework will include climate-related factors.</div></div>
          </li>
        </ul>
        """
        audit_html(html)

    def test_different_languages_map_to_one_event(self):
        pl = {"title": "Ambasadorowie UE zatwierdzili 21. pakiet sankcji wobec Rosji"}
        en = {"title": "EU envoys approved the 21st sanctions package against Russia"}
        self.assertTrue(same_story(pl, en))

    def test_summary_catches_different_headlines(self):
        first = {
            "title": "Europe reached a decision overnight",
            "summary": "EU envoys approved the 21st sanctions package against Russia.",
        }
        second = {
            "title": "Moscow faces new restrictions",
            "summary": "The 21st EU sanctions package targeting Russia was agreed by ambassadors.",
        }
        self.assertTrue(same_story(first, second))

    def test_new_material_stage_is_not_hidden(self):
        agreement = {"title": "EU envoys approve sanctions against Russia"}
        implementation = {"title": "EU sanctions against Russia become effective after publication"}
        self.assertFalse(same_story(agreement, implementation))

    def test_history_blocks_another_source_but_allows_same_card_to_remain(self):
        old = {"title": "EU envoys approve sanctions against Russia", "link": "https://a.example/story"}
        same_url = dict(old)
        other_source = {"title": "EU ambassadors approve sanctions against Russia", "link": "https://b.example/report"}
        sections, rejected = deduplicate_sections({"world": [same_url, other_source]}, [old])
        self.assertEqual([same_url], sections["world"])
        self.assertEqual("same_event_within_72h", rejected[0]["reason"])

    def test_same_current_card_is_kept_only_once_even_when_it_matches_history(self):
        old = {
            "title": "Sztuczna inteligencja a bron biologiczna",
            "link": "https://example.com/ai-biosecurity",
        }
        first = dict(old)
        duplicate = dict(old)
        sections, rejected = deduplicate_sections(
            {"health": [first], "science": [duplicate]},
            [old],
        )
        self.assertEqual([first], sections["health"])
        self.assertEqual([], sections["science"])
        self.assertEqual(1, len(rejected))
        self.assertEqual(first["title"], rejected[0]["duplicate_of"])

    def test_tracking_variant_of_current_url_is_kept_only_once(self):
        first = {
            "title": "AI and biological weapons require public health safeguards",
            "link": "https://example.com/ai-biosecurity?utm_source=rss",
        }
        duplicate = {
            "title": first["title"],
            "link": "https://example.com/ai-biosecurity",
        }
        sections, rejected = deduplicate_sections(
            {"health": [first], "science": [duplicate]},
        )
        self.assertEqual([first], sections["health"])
        self.assertEqual([], sections["science"])
        self.assertEqual(1, len(rejected))

    def test_history_expires_after_72_hours(self):
        now = datetime(2026, 7, 23, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            payload = {"stories": [
                {"title": "old", "published_at": (now - timedelta(hours=73)).isoformat()},
                {"title": "recent", "published_at": (now - timedelta(hours=71)).isoformat()},
            ]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(["recent"], [item["title"] for item in load_recent_history(path, now)])

    def test_history_is_written_with_event_signatures(self):
        now = datetime(2026, 7, 23, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            save_history({"world": [{"title": "EU envoys approve sanctions", "link": "https://a.example"}]}, path, now)
            story = json.loads(path.read_text(encoding="utf-8"))["stories"][0]
            self.assertTrue(story["event_signature"])

    def test_history_compacts_repeated_tracking_variants_of_the_same_url(self):
        now = datetime(2026, 7, 23, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            save_history(
                {
                    "world": [
                        {
                            "title": "First version of the report",
                            "link": "https://example.com/story?utm_source=rss",
                        },
                        {
                            "title": "Updated headline for the report",
                            "link": "https://example.com/story",
                        },
                    ]
                },
                path,
                now,
            )
            stories = json.loads(path.read_text(encoding="utf-8"))["stories"]
        self.assertEqual(1, len(stories))
        self.assertEqual("Updated headline for the report", stories[0]["title"])

    def test_rendered_html_gate_blocks_duplicate_cards(self):
        html = """
        <ul class="news">
          <li><span class="news-text">EU envoys approve 21st sanctions package against Russia</span></li>
          <li><span class="news-text">EU ambassadors agree 21st sanctions package targeting Russia</span></li>
        </ul>
        """
        with self.assertRaisesRegex(RuntimeError, "duplicate event"):
            audit_html(html)


if __name__ == "__main__":
    unittest.main()
