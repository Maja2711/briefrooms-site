import sys
import types
import unittest
from unittest import mock
from pathlib import Path
from datetime import timezone


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.modules.setdefault("feedparser", mock.Mock())
sys.modules.setdefault("requests", mock.Mock())
if "dateutil" not in sys.modules:
    dateutil_stub = types.ModuleType("dateutil")
    dateutil_stub.tz = mock.Mock()
    dateutil_stub.tz.gettz.return_value = timezone.utc
    sys.modules["dateutil"] = dateutil_stub

import fetch_news_pl as news  # noqa: E402
import fetch_news_pl_deep as deep  # noqa: E402


class PolishNewsBuilderTests(unittest.TestCase):
    def empty_sections(self):
        return {
            "polityka": [],
            "biznes": [],
            "zdrowie": [],
            "nauka": [],
            "sport": [],
        }

    def strict_sections(self):
        sections = self.empty_sections()
        summary = (
            "Redakcja opisała konkretne ustalenia sprawy, które mają znaczenie dla odbiorców i wynikają bezpośrednio z materiału źródłowego."
        )
        for index, section in enumerate(("polityka", "biznes", "zdrowie", "nauka", "sport", "polityka", "biznes", "sport")):
            sections[section].append({
                "title": f"Testowy materiał numer {index + 1}",
                "link": f"https://example.com/article-{index + 1}",
                "source_name": "Test Source",
                "ai_summary": summary,
                "ai_why": "",
                "ai_uncertain": "",
            })
        return sections

    def test_health_and_science_have_dedicated_feeds(self):
        self.assertTrue(any("naukawpolsce.pl/zdrowie" in news.feed_url(feed) for feed in news.FEEDS["zdrowie"]))
        self.assertTrue(any("naukawpolsce.pl/naukowy" in news.feed_url(feed) for feed in news.FEEDS["nauka"]))

    def test_topic_filters_reject_unrelated_items(self):
        self.assertFalse(
            news.is_rejected_item(
                "zdrowie",
                "Nowe badanie kliniczne terapii raka",
                "Lekarze opisali wyniki pacjentów.",
                "https://example.com/artykuly/zdrowie-1",
            )
        )
        self.assertTrue(
            news.is_rejected_item(
                "nauka",
                "Zmiana składu zarządu spółki",
                "Firma ogłosiła decyzję właścicielską.",
                "https://example.com/artykuly/biznes-1",
            )
        )

    def test_source_names_are_contextual(self):
        link = "https://www.reuters.com/world/example-story/"
        self.assertEqual(news.source_name_for(link, section_key="zdrowie"), "Reuters")
        self.assertEqual(news.source_name_for(link, section_key="sport"), "Reuters Sports")

    def test_rendered_page_is_valid_and_has_empty_states(self):
        page = deep.render_html_strict(self.strict_sections())
        self.assertIn('<a href="#zdrowie">Zdrowie</a>', page)
        self.assertIn('<a href="#nauka">Nauka</a>', page)
        self.assertIn('<section class="card" id="zdrowie">', page)
        self.assertIn('<section class="card" id="nauka">', page)
        self.assertIn('<time datetime="', page)
        self.assertIn("</time></p>", page)
        self.assertIn("Testowy materiał numer 1", page)

    def test_render_is_fail_closed_when_comments_are_missing(self):
        with self.assertRaisesRegex(RuntimeError, "only 0 strictly approved comments"):
            deep.render_html_strict(self.empty_sections())

    def test_rss_image_is_extracted(self):
        entry = {"media_thumbnail": [{"url": "/images/story.jpg"}]}
        self.assertEqual(
            news.entry_image(entry, "https://example.com/feed.xml"),
            "https://example.com/images/story.jpg",
        )

    def test_missing_ai_never_publishes_rss_fallback(self):
        with (
            mock.patch.dict("os.environ", {"OPENAI_API_KEY": ""}),
            mock.patch.object(news, "CACHE", {}),
            mock.patch.object(news, "save_cache") as save_cache,
        ):
            result = news.ai_summarize_pl(
                "Testowy tytuł",
                "Testowy opis materiału zawiera wystarczająco dużo treści do przygotowania komentarza.",
                "https://example.com/artykuly/test",
                "nauka",
            )
        self.assertEqual("", result["summary"])
        self.assertTrue(result["model"].startswith("unavailable"))
        self.assertFalse(result["reviewed"])
        save_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
