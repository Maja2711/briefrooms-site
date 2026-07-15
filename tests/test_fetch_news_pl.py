import sys
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

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
        page = deep.render_html_strict(self.empty_sections())
        self.assertIn('<a href="#zdrowie">Zdrowie</a>', page)
        self.assertIn('<a href="#nauka">Nauka</a>', page)
        self.assertIn('<section class="card" id="zdrowie">', page)
        self.assertIn('<section class="card" id="nauka">', page)
        self.assertIn('<time datetime="', page)
        self.assertIn("</time></p>", page)
        self.assertEqual(page.count("Brak nowych materiałów spełniających kryteria jakości."), 5)

    def test_rss_image_is_extracted(self):
        entry = {"media_thumbnail": [{"url": "/images/story.jpg"}]}
        self.assertEqual(
            news.entry_image(entry, "https://example.com/feed.xml"),
            "https://example.com/images/story.jpg",
        )

    def test_fallback_summary_does_not_poison_ai_cache(self):
        with (
            mock.patch.dict("os.environ", {"OPENAI_API_KEY": ""}),
            mock.patch.object(news, "CACHE", {}),
            mock.patch.object(news, "save_cache") as save_cache,
        ):
            result = news.ai_summarize_pl(
                "Testowy tytuł",
                "Testowy opis materiału.",
                "https://example.com/artykuly/test",
                "nauka",
            )
        self.assertTrue(result["model"].startswith("fallback"))
        save_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
