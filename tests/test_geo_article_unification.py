from pathlib import Path
import unittest

from scripts.apply_geo_article_unification import CSS_HREF, JS_SRC, ROOT, TARGETS


class GeoArticleUnificationTests(unittest.TestCase):
    def test_all_library_books_use_shared_assets_once(self):
        for relative in TARGETS:
            with self.subTest(page=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertEqual(text.count(CSS_HREF), 1)
                self.assertEqual(text.count(JS_SRC), 1)

    def test_shared_theme_contains_required_light_background(self):
        css = (ROOT / "assets/geo-article-unified.css").read_text(encoding="utf-8")
        self.assertIn("#f5eee5", css)
        self.assertIn(".geo-x-share", css)

    def test_share_helper_supports_both_languages_and_all_pages(self):
        js = (ROOT / "assets/geo-article-unified.js").read_text(encoding="utf-8")
        self.assertIn("Udostępnij na X", js)
        self.assertIn("Share on X", js)
        for relative in TARGETS:
            path = "/" + relative
            self.assertIn(path, js)


if __name__ == "__main__":
    unittest.main()
