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
        index = 0
        for section, (minimum, _maximum) in news.SECTION_PUBLISH_BOUNDS.items():
            for _ in range(minimum):
                index += 1
                sections[section].append({
                    "title": f"Testowy materiał numer {index}",
                    "link": f"https://example.com/article-{index}",
                    "source_name": "Test Source",
                    "thumbnail_url": f"https://example.com/image-{index}.jpg",
                    "ai_summary": summary,
                    "ai_why": "",
                    "ai_uncertain": "",
                })
        return sections

    def test_publication_ranges_match_the_product_contract(self):
        self.assertEqual((5, 10), news.SECTION_PUBLISH_BOUNDS["polityka"])
        self.assertEqual((3, 5), news.SECTION_PUBLISH_BOUNDS["zdrowie"])
        self.assertEqual((3, 5), news.SECTION_PUBLISH_BOUNDS["nauka"])
        self.assertEqual((5, 10), news.SECTION_PUBLISH_BOUNDS["sport"])

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
        self.assertNotIn("Automatyczny skrót (RSS)", page)

    def test_render_is_fail_closed_when_comments_are_missing(self):
        with self.assertRaisesRegex(RuntimeError, "polityka has 0 items"):
            deep.render_html_strict(self.empty_sections())

    def test_politics_without_thumbnail_blocks_publication(self):
        sections = self.strict_sections()
        sections["polityka"][0]["thumbnail_url"] = ""
        with self.assertRaisesRegex(RuntimeError, "every politics item must have a thumbnail"):
            deep.render_html_strict(sections)

    def test_rss_image_is_extracted(self):
        entry = {"media_thumbnail": [{"url": "/images/story.jpg"}]}
        self.assertEqual(
            news.entry_image(entry, "https://example.com/feed.xml"),
            "https://example.com/images/story.jpg",
        )

    def test_article_metadata_fills_a_missing_politics_thumbnail(self):
        response = mock.Mock(
            status_code=200,
            text='<html><head><meta property="og:image" content="/media/politics.jpg"></head></html>',
        )
        with mock.patch.object(news.requests, "get", return_value=response):
            image = news.article_image("https://example.com/politics/story")
        self.assertEqual("https://example.com/media/politics.jpg", image)

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

    def test_batch_results_pass_through_the_production_finalizer(self):
        sections = self.empty_sections()
        summary = (
            "Rząd opublikował szczegółowe zasady programu, które zaczną obowiązywać po zakończeniu konsultacji społecznych."
        )
        for index, section in enumerate(("polityka", "biznes", "zdrowie", "nauka", "sport", "polityka", "biznes", "sport")):
            sections[section].append({
                "title": f"Testowy materiał numer {index + 1}",
                "link": f"https://example.com/batch-pl-{index + 1}",
                "source_name": "Test Source",
                "thumbnail_url": f"https://example.com/batch-{index + 1}.jpg",
                "summary_raw": summary,
                "_section_key": section,
            })

        def fake_batch(*, items, **_kwargs):
            result = {}
            for index, item in enumerate(items):
                item_id = f"pl-{index}"
                item["_comment_batch_id"] = item_id
                result[item_id] = {
                    "summary": summary,
                    "reviewed": True,
                    "quality_status": news.QUALITY_STATUS,
                    "quality_version": news.QUALITY_VERSION,
                    "model": "test-generation",
                }
            return result

        with (
            mock.patch.object(news, "summarize_news_items", side_effect=fake_batch),
            mock.patch.object(news, "save_cache"),
            mock.patch.object(news, "verify_note_pl", return_value=""),
        ):
            news.summarize_sections_pl(sections)
            finalized = news.finalize_sections(sections)

        accepted = [item for items in finalized.values() for item in items]
        self.assertEqual(8, len(accepted))
        self.assertTrue(all(item["comment_generation_status"] == "ai_review_approved" for item in accepted))


if __name__ == "__main__":
    unittest.main()
