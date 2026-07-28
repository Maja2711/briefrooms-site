from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.modules.setdefault("requests", mock.Mock())

from comment_quality import QUALITY_STATUS, QUALITY_VERSION
from news_section_reserve import load_news_section_reserve, parse_news_page
from newsroom_articles import _bounded_section_candidates


PL_COMMENT = (
    "Rzad opublikowal szczegolowe zasady programu po zakonczeniu konsultacji publicznych. "
    "Nowe przepisy obejma wszystkie samorzady i zaczna obowiazywac od poczatku przyszlego roku. "
    "Zmiana okresla sposob finansowania projektu oraz obowiazki instytucji odpowiedzialnych za wykonanie. "
    "Kolejnym etapem bedzie wydanie przepisow technicznych przed uruchomieniem programu."
)
EN_COMMENT = (
    "The government published detailed programme rules after the public consultation ended. "
    "The new requirements will cover every local authority and take effect at the start of next year. "
    "The change defines the funding mechanism and the duties of the institutions responsible for delivery. "
    "The next step is the publication of technical regulations before the programme begins."
)


def page(lang: str, section_id: str, link: str, comment: str, image: str = "") -> str:
    source_label = "Zrodlo" if lang == "pl" else "Source"
    title = (
        "Rzad przedstawil szczegolowe zasady programu"
        if lang == "pl"
        else "Government publishes detailed programme rules"
    )
    image_html = f'<img src="{image}" alt="">' if image else ""
    return f"""<!doctype html><html lang="{lang}"><body>
<section class="card" id="{section_id}"><ul class="news"><li>
  <a class="news-main-link" href="{link}">
    <span class="news-thumb has-image">{image_html}</span>
    <span class="news-title-wrap"><span class="news-text">{title}</span>
    <span class="source-line">{source_label}: Example News</span></span>
  </a>
  <div class="ai-note"><div class="ai-head">Comment</div>
  <div class="sec"><strong>Key point:</strong> {comment}</div></div>
</li></ul></section></body></html>"""


def history(link: str, published_at: datetime) -> dict:
    return {
        "window_hours": 72,
        "stories": [
            {
                "title": "Stored title",
                "summary": "Stored summary",
                "link": link,
                "source": "Example News",
                "published_at": published_at.isoformat(),
            }
        ],
    }


class NewsSectionReserveTests(unittest.TestCase):
    def test_parser_maps_pl_and_en_sections_independently(self) -> None:
        pl = parse_news_page(
            page("pl", "nauka", "https://example.pl/story", PL_COMMENT),
            "pl",
        )
        en = parse_news_page(
            page("en", "science", "https://example.com/story", EN_COMMENT),
            "en",
        )
        self.assertEqual(1, len(pl["nauka"]))
        self.assertEqual(1, len(en["science"]))
        self.assertNotIn("science", pl)
        self.assertNotIn("nauka", en)

    def test_recent_approved_card_with_source_image_is_reused(self) -> None:
        now = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
        link = "https://example.com/story"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page_path = root / "news.html"
            history_path = root / "history.json"
            page_path.write_text(
                page(
                    "en",
                    "science",
                    link,
                    EN_COMMENT,
                    "https://example.com/photo.jpg",
                ),
                encoding="utf-8",
            )
            history_path.write_text(
                json.dumps(history(link, now - timedelta(hours=2))),
                encoding="utf-8",
            )
            result = load_news_section_reserve(
                "en",
                page_path=page_path,
                history_path=history_path,
                now=now,
            )

        item = result["science"][0]
        self.assertEqual("recent_approved_news_page", item["section_comment_source"])
        self.assertEqual("ai_review_approved", item["comment_generation_status"])
        self.assertEqual(QUALITY_STATUS, item["comment_quality_status"])
        self.assertEqual(QUALITY_VERSION, item["comment_quality_version"])
        self.assertTrue(item["_full_article_comment_approved"])

    def test_missing_page_image_can_be_recovered_from_the_source(self) -> None:
        now = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
        link = "https://example.com/story"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page_path = root / "news.html"
            history_path = root / "history.json"
            page_path.write_text(
                page("en", "asia-pacific", link, EN_COMMENT),
                encoding="utf-8",
            )
            history_path.write_text(
                json.dumps(history(link, now - timedelta(hours=2))),
                encoding="utf-8",
            )
            result = load_news_section_reserve(
                "en",
                page_path=page_path,
                history_path=history_path,
                now=now,
                image_lookup=lambda _link: "https://example.com/recovered.jpg",
            )

        self.assertEqual(
            "https://example.com/recovered.jpg",
            result["asia_pacific"][0]["thumbnail_url"],
        )

    def test_stale_or_unreviewed_card_is_not_a_reserve(self) -> None:
        now = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
        link = "https://example.com/story"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page_path = root / "news.html"
            history_path = root / "history.json"
            page_path.write_text(
                page(
                    "en",
                    "health",
                    link,
                    "This incomplete comment cannot pass.",
                    "https://example.com/photo.jpg",
                ),
                encoding="utf-8",
            )
            history_path.write_text(
                json.dumps(history(link, now - timedelta(hours=80))),
                encoding="utf-8",
            )
            result = load_news_section_reserve(
                "en",
                page_path=page_path,
                history_path=history_path,
                now=now,
            )

        self.assertEqual([], result["health"])

    def test_duplicate_url_cannot_be_reintroduced_in_another_section(self) -> None:
        now = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
        link = "https://example.com/story"
        source = (
            page(
                "en",
                "science",
                link,
                EN_COMMENT,
                "https://example.com/science.jpg",
            )
            + page(
                "en",
                "health",
                link,
                EN_COMMENT,
                "https://example.com/health.jpg",
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page_path = root / "news.html"
            history_path = root / "history.json"
            page_path.write_text(source, encoding="utf-8")
            history_path.write_text(
                json.dumps(history(link, now - timedelta(hours=2))),
                encoding="utf-8",
            )
            result = load_news_section_reserve(
                "en",
                page_path=page_path,
                history_path=history_path,
                now=now,
            )

        self.assertEqual(
            1,
            sum(len(items) for items in result.values()),
        )

    def test_ai_work_is_bounded_by_the_actual_section_shortage(self) -> None:
        reserve = [
            {
                "title": f"reserve-{index}",
                "link": f"https://example.com/reserve-{index}",
                "_section_reserve": True,
                "_full_article_comment_approved": True,
            }
            for index in range(6)
        ]
        fresh = [
            {
                "title": f"fresh-{index}",
                "link": f"https://example.com/fresh-{index}",
            }
            for index in range(20)
        ]
        selected = _bounded_section_candidates(fresh + reserve)
        self.assertEqual(4, len([item for item in selected if "fresh" in item["title"]]))
        self.assertEqual(6, len([item for item in selected if "reserve" in item["title"]]))

        sparse = _bounded_section_candidates(fresh + reserve[:2])
        self.assertEqual(7, len([item for item in sparse if "fresh" in item["title"]]))
        self.assertEqual(2, len([item for item in sparse if "reserve" in item["title"]]))

    def test_current_copy_of_a_reserve_url_does_not_consume_ai_budget(self) -> None:
        repeated = {
            "title": "Current copy",
            "link": "https://example.com/repeated?utm_source=rss",
        }
        reserve = {
            "title": "Approved reserve",
            "link": "https://example.com/repeated",
            "_section_reserve": True,
            "_full_article_comment_approved": True,
        }
        fresh = [
            {
                "title": f"Different fresh report {index}",
                "link": f"https://example.com/different-{index}",
            }
            for index in range(8)
        ]

        selected = _bounded_section_candidates([repeated, *fresh, reserve])

        self.assertNotIn(repeated, selected)
        self.assertIn(reserve, selected)
        self.assertEqual(
            8,
            len([item for item in selected if "Different fresh" in item["title"]]),
        )


if __name__ == "__main__":
    unittest.main()
