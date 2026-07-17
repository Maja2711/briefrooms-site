import sys
import types
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.modules.setdefault("feedparser", mock.Mock())
sys.modules.setdefault("requests", mock.Mock())
if "dateutil" not in sys.modules:
    dateutil_stub = types.ModuleType("dateutil")
    dateutil_stub.tz = mock.Mock()
    sys.modules["dateutil"] = dateutil_stub

import fetch_news_en_context as context  # noqa: E402

base = context.base
FULL_COMMENT = (
    "The government published detailed programme rules after the public consultation ended. "
    "The new requirements will cover every local authority and take effect at the start of next year. "
    "The change defines the funding mechanism and the duties of the institutions responsible for delivery. "
    "The next step is the publication of technical regulations before the programme begins."
)


def approved_item(index: int) -> dict:
    return {
        "title": f"Test report number {index}",
        "link": f"https://example.com/en-article-{index}",
        "source_name": "Test Source",
        "thumbnail_url": f"https://example.com/image-{index}.jpg",
        "full_brief": FULL_COMMENT,
        "ai_key_point": FULL_COMMENT,
        "ai_summary": FULL_COMMENT,
        "ai_why_it_matters": "",
        "ai_uncertain": "",
        "summary_basis": "article_text_ai_reviewed",
        "comment_generation_status": "ai_review_approved",
        "comment_quality_status": base.QUALITY_STATUS,
        "comment_quality_version": base.QUALITY_VERSION,
    }


class EnglishNewsBuilderTests(unittest.TestCase):
    def empty_sections(self):
        return {
            "world": [],
            "asia_pacific": [],
            "europe": [],
            "middle_east": [],
            "business": [],
            "science": [],
            "health": [],
            "sport": [],
        }

    def strict_sections(self):
        sections = self.empty_sections()
        for index, section in enumerate(sections, 1):
            sections[section].append(approved_item(index))
        return sections

    def test_render_is_fail_closed_when_comments_are_missing(self):
        with self.assertRaisesRegex(RuntimeError, "only 0 homepage-grade comments"):
            context.render_html_full(self.empty_sections())

    def test_rendered_page_contains_homepage_style_full_comments(self):
        page = context.render_html_full(self.strict_sections())
        self.assertIn('<a href="#health">Health</a>', page)
        self.assertIn('<a href="#science">Science</a>', page)
        self.assertIn("Test report number 1", page)
        self.assertEqual(8, page.count('<span class="news-thumb has-image">'))
        self.assertIn("briefrooms-newsroom-v2", page)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", page)
        self.assertIn("height:190px", page)
        self.assertNotIn("BriefRooms • AI comment", page)

    def test_missing_thumbnail_uses_visual_fallback(self):
        sections = self.strict_sections()
        sections["world"][0]["thumbnail_url"] = ""
        page = context.render_html_full(sections)
        self.assertIn('class="news-thumb"', page)

    def test_rss_thumbnail_uses_the_largest_available_image(self):
        entry = {
            "media_thumbnail": [
                {"url": "https://example.com/small.jpg", "width": "240"},
                {"url": "https://example.com/large.jpg", "width": "800"},
            ]
        }
        self.assertEqual("https://example.com/large.jpg", base.entry_image(entry, "https://example.com/feed.xml"))

    def test_thumbnail_availability_requires_a_real_image_response(self):
        response = mock.Mock()
        response.ok = True
        response.headers = {"Content-Type": "image/jpeg"}
        response.iter_content.return_value = iter([b"image-bytes"])
        base.IMAGE_FETCH_CACHE.clear()
        with mock.patch.object(base.requests, "get", return_value=response):
            self.assertTrue(base.image_is_fetchable("https://example.com/photo.jpg"))
        response.close.assert_called_once_with()

    def test_fetch_section_skips_a_hotlink_blocked_thumbnail(self):
        entries = [
            {"title": "Blocked image report", "link": "https://example.com/blocked", "summary": "First report."},
            {"title": "Reachable image report", "link": "https://example.com/reachable", "summary": "Second report."},
        ]
        parsed = types.SimpleNamespace(entries=entries)
        with (
            mock.patch.object(base, "FEEDS", {"world": ["https://example.com/feed.xml"]}),
            mock.patch.object(base.feedparser, "parse", return_value=parsed),
            mock.patch.object(base, "should_keep_item", return_value=True),
            mock.patch.object(base, "entry_image", side_effect=["https://example.com/blocked.jpg", "https://example.com/reachable.jpg"]),
            mock.patch.object(base, "image_is_fetchable", side_effect=lambda url: "reachable" in url),
            mock.patch.object(base, "article_image", return_value=""),
        ):
            selected = base.fetch_section("world", summarize=False)
        self.assertEqual(["Reachable image report"], [item["title"] for item in selected])

    def test_finalizer_requires_homepage_quality_metadata(self):
        sections = self.empty_sections()
        for index, section in enumerate(sections, 1):
            sections[section].append(approved_item(index))
        finalized = context.finalize_sections_full(sections)
        accepted = [item for items in finalized.values() for item in items]
        self.assertEqual(8, len(accepted))
        self.assertTrue(all(item["comment_generation_status"] == "ai_review_approved" for item in accepted))


if __name__ == "__main__":
    unittest.main()
