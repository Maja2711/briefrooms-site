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
