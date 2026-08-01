from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.publish_emergency_news import update_homepage, update_news_page


class LayoutPreservingNewsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
        self.story = {
            "title": "Fresh story",
            "link": "https://example.com/story",
            "image": "https://example.com/image.jpg",
            "source": "Example",
            "summary": "Fresh source summary.",
            "category": "Polityka / Kraj",
        }

    def test_news_refresh_preserves_layout_outside_section_list(self) -> None:
        original = (
            '<!doctype html><html><head><style>.approved-layout{display:grid}</style></head>'
            '<body data-page="news"><header>KEEP HEADER</header>'
            '<nav class="section-tabs"><a href="#polityka">Polityka</a></nav>'
            '<section class="card" id="polityka"><h2>Polityka</h2>'
            '<ul class="news"><li>OLD</li></ul></section>'
            '<footer>KEEP FOOTER</footer></body></html>'
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.html"
            path.write_text(original, encoding="utf-8")
            update_news_page(path, "pl", {"polityka": [self.story]}, "marker-1", self.now)
            updated = path.read_text(encoding="utf-8")
        self.assertIn("KEEP HEADER", updated)
        self.assertIn("KEEP FOOTER", updated)
        self.assertIn(".approved-layout{display:grid}", updated)
        self.assertNotIn("<li>OLD</li>", updated)
        self.assertIn("Fresh story", updated)
        self.assertIn("briefrooms-shared-section-tabs", updated)
        self.assertIn('content="marker-1"', updated)

    def test_homepage_refresh_changes_only_marked_cards_and_timestamp(self) -> None:
        original = (
            '<!doctype html><html><head><style>.homepage-layout{display:grid}</style></head><body>'
            '<header>KEEP HOME HEADER</header>'
            '<span class="pill" id="updated-at">Aktualizacja: old</span>'
            '<div id="latest-briefs" class="brief-grid" data-home-updated-at="old">'
            '<!-- HOME_BRIEFS_START --><a>OLD CARD</a><!-- HOME_BRIEFS_END -->'</n            '</div><aside>KEEP SIDEBAR</aside></body></html>'
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.html"
            path.write_text(original, encoding="utf-8")
            update_homepage(path, "pl", [self.story], "marker-2", self.now)
            updated = path.read_text(encoding="utf-8")
        self.assertIn("KEEP HOME HEADER", updated)
        self.assertIn("KEEP SIDEBAR", updated)
        self.assertIn(".homepage-layout{display:grid}", updated)
        self.assertNotIn("OLD CARD", updated)
        self.assertIn("Fresh story", updated)
        self.assertIn("Czytaj źródło", updated)
        self.assertIn('content="marker-2"', updated)


if __name__ == "__main__":
    unittest.main()
