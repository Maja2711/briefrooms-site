from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from scripts.publish_live_news import MIN_SECTION, TARGET, normalized_identity, parse_entry_time, select_sections


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


if __name__ == "__main__":
    unittest.main()
