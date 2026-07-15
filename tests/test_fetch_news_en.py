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

    def test_plain_wrapper_never_restores_rss_fallback(self):
        self.assertEqual("", context._plain_summary("Title", "", "Raw RSS fallback text."))


if __name__ == "__main__":
    unittest.main()
