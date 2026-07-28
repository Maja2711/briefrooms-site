from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import normalize_home_publish_count
from validate_news_home_publish import (
    ALLOWED_HOMEPAGE_COUNTS,
    NEWS_SECTION_IDS,
    NEWS_SECTION_MINIMUM,
    assert_fresh,
    homepage_timestamp,
    parse_datetime,
    validate_news_sections,
)


class NewsHomePublishValidationTests(unittest.TestCase):
    @staticmethod
    def news_page(lang: str, *, missing_image: bool = False, duplicate_url: bool = False) -> str:
        sections = []
        sequence = 0
        for section_id in NEWS_SECTION_IDS[lang]:
            cards = []
            for index in range(NEWS_SECTION_MINIMUM):
                sequence += 1
                item_number = 1 if duplicate_url and sequence == 2 else sequence
                image = (
                    ""
                    if missing_image and sequence == 1
                    else '<span class="news-thumb has-image"><img src="https://example.com/image.jpg" alt=""></span>'
                )
                cards.append(
                    "<li>"
                    f'<a class="news-main-link" href="https://example.com/{lang}/{item_number}">'
                    f"{image}<span class=\"news-text\">Unique report {sequence}</span></a>"
                    '<div class="ai-note"><div class="sec">A complete unique report summary.</div></div>'
                    "</li>"
                )
            sections.append(
                f'<section class="card" id="{section_id}"><ul class="news">'
                + "".join(cards)
                + "</ul></section>"
            )
        return f'<!doctype html><html lang="{lang}"><body>{"".join(sections)}</body></html>'

    def test_timezone_aware_timestamp_is_normalized(self) -> None:
        value = parse_datetime("2026-07-24T21:54+02:00")
        self.assertEqual(value.isoformat(), "2026-07-24T19:54:00+00:00")

    def test_stale_timestamp_fails_closed(self) -> None:
        now = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(AssertionError, "is stale"):
            assert_fresh(now - timedelta(hours=7), now, 360, "feed")

    def test_recent_timestamp_passes(self) -> None:
        now = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)
        assert_fresh(now - timedelta(minutes=359), now, 360, "feed")

    def test_only_eight_or_ten_homepage_briefs_are_allowed(self) -> None:
        self.assertEqual(ALLOWED_HOMEPAGE_COUNTS, {8, 10})
        self.assertNotIn(9, ALLOWED_HOMEPAGE_COUNTS)
        self.assertNotIn(11, ALLOWED_HOMEPAGE_COUNTS)

    def test_homepage_timestamp_accepts_equivalent_second_precision(self) -> None:
        source = (
            '<div id="latest-briefs" class="brief-grid" data-home-photo-only="true" '
            'data-home-updated-at="2026-07-24T21:37:00+00:00">'
        )
        self.assertEqual(
            homepage_timestamp(source, "en"),
            parse_datetime("2026-07-24T21:37+00:00"),
        )

    def test_normalizer_skips_stale_rendered_card_not_in_current_feed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feed_path = root / "home_brief.json"
            index_path = root / "index.html"
            valid_links = [f"/pl/briefy/current-{index}.html" for index in range(8)]
            feed_path.write_text(
                json.dumps(
                    {
                        "count": 8,
                        "latest": [
                            {"permalink": link, "title": f"Current {index}"}
                            for index, link in enumerate(valid_links)
                        ],
                    }
                ),
                encoding="utf-8",
            )
            cards = [
                '<a class="brief-card" href="/pl/briefy/stale.html">Stale</a>',
                *[
                    f'<a class="brief-card" href="{link}">Current</a>'
                    for link in valid_links
                ],
            ]
            index_path.write_text(
                normalize_home_publish_count.START
                + "\n".join(cards)
                + normalize_home_publish_count.END,
                encoding="utf-8",
            )
            original = normalize_home_publish_count.PATHS["pl"]
            normalize_home_publish_count.PATHS["pl"] = (feed_path, index_path)
            try:
                self.assertEqual(normalize_home_publish_count.normalize("pl"), 8)
            finally:
                normalize_home_publish_count.PATHS["pl"] = original
            normalized = index_path.read_text(encoding="utf-8")
            self.assertNotIn("stale.html", normalized)
            self.assertEqual(normalized.count('class="brief-card"'), 8)

    def test_news_section_contract_requires_six_unique_photo_cards(self) -> None:
        for lang in ("pl", "en"):
            counts = validate_news_sections(self.news_page(lang), lang)
            self.assertEqual(set(NEWS_SECTION_IDS[lang]), set(counts))
            self.assertTrue(all(count == NEWS_SECTION_MINIMUM for count in counts.values()))

    def test_news_section_contract_rejects_duplicate_urls(self) -> None:
        with self.assertRaisesRegex(AssertionError, "duplicate source URLs"):
            validate_news_sections(self.news_page("en", duplicate_url=True), "en")

    def test_news_section_contract_rejects_missing_thumbnail(self) -> None:
        with self.assertRaisesRegex(AssertionError, "without a source thumbnail"):
            validate_news_sections(self.news_page("pl", missing_image=True), "pl")

    def test_news_section_contract_keeps_pl_and_en_documents_separate(self) -> None:
        with self.assertRaisesRegex(AssertionError, "wrong document language"):
            validate_news_sections(self.news_page("en"), "pl")


if __name__ == "__main__":
    unittest.main()
