from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.publish_section_news import (
    SectionPublicationError,
    inject_marker,
    validate_page,
)


def page(lang: str, counts: dict[str, int], *, missing_image: bool = False) -> str:
    sections = []
    sequence = 0
    for section_id, count in counts.items():
        cards = []
        for _ in range(count):
            sequence += 1
            image = "" if missing_image and sequence == 1 else (
                f'<span class="news-thumb has-image"><img src="https://img.example/{sequence}.jpg" alt=""></span>'
            )
            cards.append(
                "<li>"
                f'<a class="news-main-link" href="https://news.example/{lang}/{sequence}">'
                f"{image}"
                '<span class="news-title-wrap">'
                f'<span class="news-text">Story {sequence}</span>'
                '<span class="source-line">Source: Example</span>'
                "</span></a></li>"
            )
        sections.append(
            f'<section class="card" id="{section_id}"><ul class="news">'
            + "".join(cards)
            + "</ul></section>"
        )
    return (
        f'<!doctype html><html lang="{lang}"><head>'
        '<meta name="briefrooms-news-updated-at" content="2026-08-01T07:00:00+00:00">'
        "</head><body>"
        + "".join(sections)
        + "</body></html>"
    )


class SectionNewsFastLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 1, 7, 30, tzinfo=timezone.utc)
        self.pl = {
            "polityka": 6,
            "ekonomia": 6,
            "zdrowie": 6,
            "nauka": 6,
            "sport": 6,
        }
        self.en = {
            "world-news": 6,
            "asia-pacific": 6,
            "europe": 6,
            "middle-east": 6,
            "business": 6,
            "science": 6,
            "health": 6,
            "sport": 6,
        }

    def write(self, directory: str, name: str, source: str) -> Path:
        path = Path(directory) / name
        path.write_text(source, encoding="utf-8")
        return path

    def test_complete_source_only_pages_pass_without_ai_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pl = validate_page(self.write(directory, "pl.html", page("pl", self.pl)), "pl", now=self.now)
            en = validate_page(self.write(directory, "en.html", page("en", self.en)), "en", now=self.now)
        self.assertEqual(30, pl["articles"])
        self.assertEqual(48, en["articles"])

    def test_underfilled_section_is_blocked(self) -> None:
        counts = dict(self.pl)
        counts["zdrowie"] = 3
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, "pl.html", page("pl", counts))
            with self.assertRaisesRegex(SectionPublicationError, "zdrowie has 3 cards"):
                validate_page(path, "pl", now=self.now)

    def test_missing_source_image_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, "en.html", page("en", self.en, missing_image=True))
            with self.assertRaisesRegex(SectionPublicationError, "has no source image"):
                validate_page(path, "en", now=self.now)

    def test_stale_page_is_blocked(self) -> None:
        stale_now = datetime(2026, 8, 3, 7, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, "pl.html", page("pl", self.pl))
            with self.assertRaisesRegex(SectionPublicationError, "timestamp is stale"):
                validate_page(path, "pl", now=stale_now)

    def test_publication_marker_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, "pl.html", page("pl", self.pl))
            inject_marker(path, "run-first")
            inject_marker(path, "run-second")
            source = path.read_text(encoding="utf-8")
        self.assertNotIn("run-first", source)
        self.assertEqual(1, source.count("briefrooms-section-publication"))
        self.assertIn('content="run-second"', source)


if __name__ == "__main__":
    unittest.main()
