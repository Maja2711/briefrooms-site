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

FULL_COMMENT = (
    "Rząd opublikował szczegółowe zasady programu po zakończeniu konsultacji społecznych. "
    "Nowe przepisy obejmą wszystkie samorządy i zaczną obowiązywać od początku przyszłego roku. "
    "Zmiana określa sposób finansowania projektu oraz obowiązki instytucji odpowiedzialnych za jego wykonanie. "
    "Kolejnym etapem będzie wydanie rozporządzeń technicznych przed uruchomieniem programu."
)


def approved_item(index: int) -> dict:
    return {
        "title": f"Testowy materiał numer {index}",
        "link": f"https://example.com/article-{index}",
        "source_name": "Test Source",
        "thumbnail_url": f"https://example.com/image-{index}.jpg",
        "full_brief": FULL_COMMENT,
        "ai_summary": FULL_COMMENT,
        "ai_why": "",
        "ai_uncertain": "",
        "summary_basis": "article_text_ai_reviewed",
        "comment_generation_status": "ai_review_approved",
        "comment_quality_status": news.QUALITY_STATUS,
        "comment_quality_version": news.QUALITY_VERSION,
    }


class PolishNewsBuilderTests(unittest.TestCase):
    def empty_sections(self):
        return {"polityka": [], "biznes": [], "zdrowie": [], "nauka": [], "sport": []}

    def strict_sections(self):
        sections = self.empty_sections()
        index = 0
        for section, (minimum, _maximum) in news.SECTION_PUBLISH_BOUNDS.items():
            for _ in range(minimum):
                index += 1
                sections[section].append(approved_item(index))
        return sections

    def test_publication_ranges_match_the_product_contract(self):
        self.assertEqual((5, 10), news.SECTION_PUBLISH_BOUNDS["polityka"])
        self.assertEqual((3, 5), news.SECTION_PUBLISH_BOUNDS["zdrowie"])
        self.assertEqual((3, 5), news.SECTION_PUBLISH_BOUNDS["nauka"])
        self.assertEqual((5, 10), news.SECTION_PUBLISH_BOUNDS["sport"])

    def test_native_polish_feed_encoding_is_repaired_before_publication(self):
        broken = "USA rozważajš uderzenie. Mówiš o tysišcach żołnierzy."
        expected = "USA rozważają uderzenie. Mówią o tysiącach żołnierzy."
        self.assertEqual(expected, news.repair_polish_feed_encoding(broken))

    def test_health_and_science_have_dedicated_feeds(self):
        self.assertTrue(any("naukawpolsce.pl/zdrowie" in news.feed_url(feed) for feed in news.FEEDS["zdrowie"]))
        self.assertTrue(any("naukawpolsce.pl/naukowy" in news.feed_url(feed) for feed in news.FEEDS["nauka"]))

    def test_topic_filters_reject_unrelated_items(self):
        self.assertFalse(news.is_rejected_item("zdrowie", "Nowe badanie kliniczne terapii raka", "Lekarze opisali wyniki pacjentów.", "https://example.com/zdrowie"))
        self.assertTrue(news.is_rejected_item("nauka", "Zmiana składu zarządu spółki", "Firma ogłosiła decyzję właścicielską.", "https://example.com/biznes"))

    def test_source_names_are_contextual(self):
        link = "https://www.reuters.com/world/example-story/"
        self.assertEqual(news.source_name_for(link, section_key="zdrowie"), "Reuters")
        self.assertEqual(news.source_name_for(link, section_key="sport"), "Reuters Sports")

    def test_rendered_page_uses_homepage_cards_and_sections(self):
        page = deep.render_html_strict(self.strict_sections())
        self.assertIn('<a href="#zdrowie">Zdrowie</a>', page)
        self.assertIn('<section class="card" id="zdrowie">', page)
        self.assertIn("Testowy materiał numer 1", page)
        self.assertIn("briefrooms-newsroom-v2", page)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", page)
        self.assertIn("height:190px", page)

    def test_render_is_fail_closed_when_full_comments_are_missing(self):
        with self.assertRaisesRegex(RuntimeError, "only 0 homepage-grade comments"):
            deep.render_html_strict(self.empty_sections())

    def test_missing_thumbnail_uses_fallback_art(self):
        sections = self.strict_sections()
        sections["polityka"][0]["thumbnail_url"] = ""
        page = deep.render_html_strict(sections)
        self.assertIn('class="news-thumb"', page)

    def test_rss_image_is_extracted(self):
        entry = {"media_thumbnail": [{"url": "/images/story.jpg"}]}
        self.assertEqual(news.entry_image(entry, "https://example.com/feed.xml"), "https://example.com/images/story.jpg")

    def test_article_metadata_fills_a_missing_politics_thumbnail(self):
        response = mock.Mock(status_code=200, text='<meta property="og:image" content="/media/politics.jpg">')
        with mock.patch.object(news.requests, "get", return_value=response):
            image = news.article_image("https://example.com/politics/story")
        self.assertEqual("https://example.com/media/politics.jpg", image)

    def test_missing_ai_never_publishes_rss_fallback(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": ""}), mock.patch.object(news, "CACHE", {}), mock.patch.object(news, "save_cache") as save_cache:
            result = news.ai_summarize_pl("Testowy tytuł", "Opis materiału zawiera wystarczająco dużo treści do komentarza.", "https://example.com/test", "nauka")
        self.assertEqual("", result["summary"])
        self.assertFalse(result["reviewed"])
        save_cache.assert_not_called()

    def test_finalizer_requires_homepage_quality_metadata(self):
        sections = self.empty_sections()
        for index, section in enumerate(("polityka", "biznes", "zdrowie", "nauka", "sport", "polityka", "biznes", "sport"), 1):
            sections[section].append(approved_item(index))
        finalized = deep.finalize_sections_strict(sections)
        accepted = [item for items in finalized.values() for item in items]
        self.assertEqual(8, len(accepted))
        self.assertTrue(all(item["comment_generation_status"] == "ai_review_approved" for item in accepted))


if __name__ == "__main__":
    unittest.main()
