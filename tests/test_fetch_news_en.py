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
        summary = (
            "The report describes concrete findings that are supported by the source material and relevant to readers."
        )
        for index, section in enumerate(sections):
            sections[section].append({
                "title": f"Test report number {index + 1}",
                "link": f"https://example.com/en-article-{index + 1}",
                "source_name": "Test Source",
                "thumbnail_url": f"https://example.com/image-{index + 1}.jpg",
                "ai_key_point": summary,
                "ai_summary": summary,
                "ai_why_it_matters": "",
                "ai_uncertain": "",
            })
        return sections

    def test_render_is_fail_closed_when_comments_are_missing(self):
        with self.assertRaisesRegex(RuntimeError, "only 0 strictly approved comments"):
            context.render_html_plain(self.empty_sections())

    def test_rendered_page_contains_only_strict_comments(self):
        page = context.render_html_plain(self.strict_sections())
        self.assertIn('<a href="#health">Health</a>', page)
        self.assertIn('<a href="#science">Science</a>', page)
        self.assertIn("Test report number 1", page)
        self.assertEqual(8, page.count('<span class="news-thumb has-image">'))
        self.assertEqual(8, page.count("<img "))

    def test_render_blocks_an_item_without_a_thumbnail(self):
        sections = self.strict_sections()
        sections["world"][0]["thumbnail_url"] = ""
        with self.assertRaisesRegex(RuntimeError, "items have no thumbnail"):
            context.render_html_plain(sections)

    def test_rss_thumbnail_uses_the_largest_available_image(self):
        entry = {
            "media_thumbnail": [
                {"url": "https://example.com/small.jpg", "width": "240"},
                {"url": "https://example.com/large.jpg", "width": "800"},
            ]
        }
        self.assertEqual(
            "https://example.com/large.jpg",
            base.entry_image(entry, "https://example.com/feed.xml"),
        )

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
            mock.patch.object(
                base,
                "entry_image",
                side_effect=["https://example.com/blocked.jpg", "https://example.com/reachable.jpg"],
            ),
            mock.patch.object(base, "image_is_fetchable", side_effect=lambda url: "reachable" in url),
            mock.patch.object(base, "article_image", return_value=""),
        ):
            selected = base.fetch_section("world", summarize=False)

        self.assertEqual(["Reachable image report"], [item["title"] for item in selected])

    def test_plain_wrapper_never_restores_rss_fallback(self):
        self.assertEqual("", context._plain_summary("Title", "", "Raw RSS fallback text."))

    def test_batch_results_pass_through_the_production_finalizer(self):
        sections = self.empty_sections()
        summary = "The government published detailed programme rules that will take effect after the consultation period ends."
        for index, section in enumerate(sections):
            sections[section].append({
                "title": f"Test report number {index + 1}",
                "link": f"https://example.com/batch-en-{index + 1}",
                "source_name": "Test Source",
                "thumbnail_url": f"https://example.com/batch-image-{index + 1}.jpg",
                "summary_raw": summary,
            })

        def fake_batch(*, items, **_kwargs):
            result = {}
            for index, item in enumerate(items):
                item_id = f"en-{index}"
                item["_comment_batch_id"] = item_id
                result[item_id] = {
                    "summary": summary,
                    "reviewed": True,
                    "quality_status": base.QUALITY_STATUS,
                    "quality_version": base.QUALITY_VERSION,
                    "model": "test-generation",
                }
            return result

        with (
            mock.patch.object(base, "summarize_news_items", side_effect=fake_batch),
            mock.patch.object(base, "save_cache"),
        ):
            base.summarize_sections_en(sections)
            finalized = base.finalize_sections(sections)

        accepted = [item for items in finalized.values() for item in items]
        self.assertEqual(8, len(accepted))
        self.assertTrue(all(item["comment_generation_status"] == "ai_review_approved" for item in accepted))


if __name__ == "__main__":
    unittest.main()
