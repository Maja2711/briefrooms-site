from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_permanent_briefs as permanent


TECHNICAL_TEXT = (
    "home_brief.json loads",
    "Karty uzupełnią się po wczytaniu home_brief.json",
    "Latest briefs are refreshed automatically",
    "Najnowsze briefy odświeżają się automatycznie",
    "Update: no data",
    "Aktualizacja: brak danych",
)


class BalancedHTMLParser(HTMLParser):
    VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() not in self.VOID:
            self.stack.append(tag.lower())

    def handle_startendtag(self, tag: str, attrs) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if not self.stack or self.stack[-1] != lowered:
            raise AssertionError(f"Unexpected closing tag </{lowered}>; stack={self.stack[-5:]}")
        self.stack.pop()

    def close(self) -> None:
        super().close()
        if self.stack:
            raise AssertionError(f"Unclosed HTML tags: {self.stack[-10:]}")


def marker_block(source: str) -> str:
    match = re.search(
        rf"{re.escape(permanent.HOME_BRIEFS_START)}(.*?){re.escape(permanent.HOME_BRIEFS_END)}",
        source,
        flags=re.DOTALL,
    )
    if not match:
        raise AssertionError("Static homepage brief markers are missing")
    return match.group(1)


class HomepageStaticTests(unittest.TestCase):
    def test_repository_homepages_contain_real_static_briefs(self) -> None:
        for lang, directory in (("pl", "briefy"), ("en", "briefs")):
            page = ROOT / lang / "index.html"
            source = page.read_text(encoding="utf-8")
            block = marker_block(source)
            hrefs = re.findall(r'<a class="brief-card" href="([^"]+)">', block)
            self.assertGreaterEqual(len(hrefs), 1, lang)
            self.assertLessEqual(len(hrefs), permanent.HOME_CARD_LIMIT, lang)
            self.assertEqual(block.count('class="brief-title"'), len(hrefs), lang)
            self.assertEqual(block.count('class="brief-desc"'), len(hrefs), lang)
            self.assertEqual(block.count('class="brief-source"'), len(hrefs), lang)
            self.assertEqual(block.count('class="brief-link"'), len(hrefs), lang)
            for href in hrefs:
                self.assertRegex(
                    href,
                    rf"^/{lang}/{directory}/[a-z0-9-]+-[0-9a-f]{{12}}\.html$",
                    lang,
                )
                self.assertTrue((ROOT / href.lstrip("/")).is_file(), href)
            for text in TECHNICAL_TEXT:
                self.assertNotIn(text, source, f"{lang}: {text}")
            self.assertNotIn(" onerror=", block.lower(), lang)
            for image in re.findall(r"<img\b[^>]*>", block, flags=re.IGNORECASE):
                self.assertIn('loading="lazy"', image)
                self.assertIn('alt=""', image)

    def test_visible_date_matches_feed_generation_date(self) -> None:
        for lang in ("pl", "en"):
            feed = json.loads((ROOT / lang / "home_brief.json").read_text(encoding="utf-8"))
            source = (ROOT / lang / "index.html").read_text(encoding="utf-8")
            expected = permanent.iso_datetime(feed["updated_at"])
            self.assertIn(f'data-home-updated-at="{expected}"', source, lang)
            label = permanent.LANGUAGES[lang]["updated_label"] + permanent._home_date(
                feed["updated_at"], lang
            )
            self.assertIn(f'<span class="pill" id="updated-at">{label}</span>', source, lang)

    def test_repository_homepages_have_balanced_html(self) -> None:
        for lang in ("pl", "en"):
            parser = BalancedHTMLParser()
            parser.feed((ROOT / lang / "index.html").read_text(encoding="utf-8"))
            parser.close()

    def test_missing_approved_feed_preserves_existing_static_cards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "index.html"
            original = (
                '<!doctype html><html><body><span class="pill" id="updated-at">Old</span>'
                '<div id="latest-briefs" class="brief-grid" data-home-updated-at="2026-07-18T10:00:00+00:00">'
                f'{permanent.HOME_BRIEFS_START}<a class="brief-card" href="/pl/briefy/old-aaaaaaaaaaaa.html">Old</a>'
                f'{permanent.HOME_BRIEFS_END}</div></body></html>'
            )
            page.write_text(original, encoding="utf-8")
            rendered = permanent.render_homepage_static(
                page,
                {"updated_at": "2026-07-19T10:00:00+00:00", "latest": [], "radar": []},
                "pl",
            )
            self.assertIsNone(rendered)
            self.assertEqual(page.read_text(encoding="utf-8"), original)

    def test_home_card_escapes_text_and_rejects_unsafe_image(self) -> None:
        item = {
            "title": '<script>alert("title")</script>',
            "full_brief": '<img src=x onerror=alert("summary")>',
            "source": '<b onclick="bad">Source</b>',
            "category": '<svg onload="bad">',
            "image": "javascript:alert(1)",
            "permalink": "/en/briefs/safe-aaaaaaaaaaaa.html",
        }
        card = permanent.render_home_card(item, "en")
        self.assertNotIn("<script>", card)
        self.assertNotIn("<svg", card)
        self.assertNotIn("javascript:", card)
        self.assertNotIn("<img ", card)
        self.assertIn("&lt;script&gt;", card)
        self.assertIn('href="/en/briefs/safe-aaaaaaaaaaaa.html"', card)

    def test_sitemap_generator_removes_retired_topic_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sitemap = Path(directory) / "sitemap.xml"
            sitemap.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                '<url><loc>https://briefrooms.com/en/geo/topic.html</loc></url>'
                '<url><loc>https://briefrooms.com/en/geopolitics.html</loc></url>'
                '</urlset>',
                encoding="utf-8",
            )
            output = permanent._sitemap_bytes(sitemap, {"pl": [], "en": []}).decode("utf-8")
            self.assertNotIn("/en/geo/topic.html", output)
            self.assertIn("/en/geopolitics.html", output)
            ET.fromstring(output)

    def test_retired_topic_is_crawlable_but_not_in_sitemap(self) -> None:
        robots = (ROOT / "robots.txt").read_text(encoding="utf-8")
        sitemap = (ROOT / "sitemap.xml").read_text(encoding="utf-8")
        topic = (ROOT / "en/geo/topic.html").read_text(encoding="utf-8")
        self.assertNotIn("Disallow: /en/geo/topic.html", robots)
        self.assertNotIn("/en/geo/topic.html", sitemap)
        self.assertIn("https://briefrooms.com/en/geopolitics.html", sitemap)
        self.assertIn('name="robots" content="noindex,follow"', topic)
        self.assertNotIn("http-equiv=\"refresh\"", topic.lower())


if __name__ == "__main__":
    unittest.main()
