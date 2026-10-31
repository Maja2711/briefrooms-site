from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import validate_live_news_health as health


NOW = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
MARKER = "test-marker"


def story(index: int, lang: str = "en", section_id: str = "world-news") -> dict:
    published = NOW - timedelta(minutes=index)
    result = {
        "title": f"Story {index}",
        "link": f"https://example.com/{index}",
        "image": f"https://example.com/{index}.jpg",
        "source": "Example",
        "published_at": published.isoformat(),
        "news_first_seen_at": published.isoformat(),
    }
    result["news_expires_at"] = health.freshness._public_expiry(
        result, published, lang, section_id
    ).isoformat()
    return result


def write_fixture(root: Path, generated: datetime = NOW) -> None:
    status = {
        "schema_version": "news-live-status-v2",
        "marker": MARKER,
        "generated_at": generated.isoformat(),
    }
    (root / "data/news").mkdir(parents=True)
    (root / "data/news/status.json").write_text(json.dumps(status), encoding="utf-8")
    for lang, config in health.LANGUAGES.items():
        payload = {
            "schema_version": "news-live-v2",
            "language": lang,
            "marker": MARKER,
            "generated_at": generated.isoformat(),
            "sections": {
                section: [story(i, lang, section) for i in range(health.TARGET_SECTION)]
                for section in config["sections"]
            },
            "health": {"status": "ok", "source_errors": []},
        }
        path = root / config["feed"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        page = f'<meta name="briefrooms-live-news-marker" content="{MARKER}"><script src="{health.RUNTIME}" defer></script>'
        for key in ("news_page", "home_page"):
            target = root / config[key]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(page, encoding="utf-8")


class LiveNewsHealthTests(unittest.TestCase):
    def test_current_atomic_publication_is_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            report = health.assess(root, now=NOW, max_age_minutes=120)
        self.assertEqual("healthy", report["status"])
        self.assertFalse(report["reasons"])

    def test_underfilled_fresh_section_is_degraded_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            feed_path = root / health.LANGUAGES["pl"]["feed"]
            payload = json.loads(feed_path.read_text(encoding="utf-8"))
            first_section = next(iter(payload["sections"]))
            payload["sections"][first_section] = payload["sections"][first_section][:2]
            feed_path.write_text(json.dumps(payload), encoding="utf-8")
            report = health.assess(root, now=NOW, max_age_minutes=120)
        self.assertEqual("degraded", report["status"])
        self.assertFalse(report["reasons"])
        self.assertTrue(
            any("section_underfilled_freshness_first" in warning for warning in report["warnings"])
        )

    def test_seven_cards_is_healthy_and_sixteen_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            feed_path = root / health.LANGUAGES["en"]["feed"]
            payload = json.loads(feed_path.read_text(encoding="utf-8"))
            section = "sport"
            payload["sections"][section] = [story(i) for i in range(7)]
            feed_path.write_text(json.dumps(payload), encoding="utf-8")
            within_range = health.assess(root, now=NOW, max_age_minutes=120)
            self.assertEqual("healthy", within_range["status"])
            payload["sections"][section] = [story(i) for i in range(16)]
            feed_path.write_text(json.dumps(payload), encoding="utf-8")
            above_max = health.assess(root, now=NOW, max_age_minutes=120)
            self.assertEqual("failed", above_max["status"])
            self.assertIn("en:section_too_large:sport", above_max["reasons"])

    def test_over_24h_story_is_failed_even_when_feed_generation_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            feed_path = root / health.LANGUAGES["pl"]["feed"]
            payload = json.loads(feed_path.read_text(encoding="utf-8"))
            old = payload["sections"]["polityka"][0]
            old_time = NOW - timedelta(hours=24, seconds=1)
            old["published_at"] = old_time.isoformat()
            old["news_first_seen_at"] = old_time.isoformat()
            old["news_expires_at"] = (old_time + timedelta(hours=24)).isoformat()
            feed_path.write_text(json.dumps(payload), encoding="utf-8")
            report = health.assess(root, now=NOW, max_age_minutes=120)
        self.assertEqual("failed", report["status"])
        self.assertTrue(
            any("source_story_over_24h" in reason for reason in report["reasons"])
        )

    def test_editorial_exception_accepts_older_source_with_new_verified_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            feed_path = root / health.LANGUAGES["pl"]["feed"]
            payload = json.loads(feed_path.read_text(encoding="utf-8"))
            article = payload["sections"]["nauka"][0]
            article["published_at"] = (NOW - timedelta(days=5)).isoformat()
            article["news_first_seen_at"] = (NOW - timedelta(minutes=30)).isoformat()
            article["news_expires_at"] = (
                NOW + timedelta(hours=23, minutes=30)
            ).isoformat()
            feed_path.write_text(json.dumps(payload), encoding="utf-8")
            report = health.assess(root, now=NOW, max_age_minutes=120)
        self.assertEqual("healthy", report["status"])
        self.assertFalse(report["reasons"])

    def test_previous_day_pl_politics_source_within_24h_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            feed_path = root / health.LANGUAGES["pl"]["feed"]
            payload = json.loads(feed_path.read_text(encoding="utf-8"))
            article = payload["sections"]["polityka"][0]
            article["published_at"] = (NOW - timedelta(hours=12)).isoformat()
            article["news_first_seen_at"] = (NOW - timedelta(minutes=10)).isoformat()
            article["news_expires_at"] = (NOW + timedelta(hours=12)).isoformat()
            feed_path.write_text(json.dumps(payload), encoding="utf-8")
            report = health.assess(root, now=NOW, max_age_minutes=120)
        self.assertEqual("healthy", report["status"])
        self.assertFalse(report["reasons"])

    def test_previous_day_pl_politics_source_over_24h_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            feed_path = root / health.LANGUAGES["pl"]["feed"]
            payload = json.loads(feed_path.read_text(encoding="utf-8"))
            article = payload["sections"]["polityka"][0]
            article["published_at"] = (NOW - timedelta(hours=25)).isoformat()
            article["news_first_seen_at"] = (NOW - timedelta(minutes=10)).isoformat()
            article["news_expires_at"] = (NOW + timedelta(hours=12)).isoformat()
            feed_path.write_text(json.dumps(payload), encoding="utf-8")
            report = health.assess(root, now=NOW, max_age_minutes=120)
        self.assertEqual("failed", report["status"])
        self.assertIn("pl:source_story_over_24h:polityka", report["reasons"])

    def test_independent_watchdog_detects_statement_duplication_with_different_event_ids(self) -> None:
        from tests.test_public_statement_dedupe import reports

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            feed_path = root / health.LANGUAGES["pl"]["feed"]
            payload = json.loads(feed_path.read_text(encoding="utf-8"))
            statement_rows = reports()[:2]
            for index, row in enumerate(statement_rows):
                row["canonical_event_id"] = f"evt_distinct_{index}"
                row["published_at"] = (NOW - timedelta(minutes=index + 1)).isoformat()
                row["news_first_seen_at"] = NOW.isoformat()
                row["news_expires_at"] = health.freshness._public_expiry(
                    row, NOW, "pl", "polityka"
                ).isoformat()
            # Independently verify semantics, not the equal-ID check.
            payload["sections"]["polityka"][:2] = statement_rows
            payload["home"] = list(statement_rows)
            feed_path.write_text(json.dumps(payload), encoding="utf-8")
            report = health.assess(root, now=NOW, max_age_minutes=120)

        self.assertIn(
            "pl:duplicate_public_statement:polityka", report["reasons"]
        )
        self.assertIn(
            "pl:duplicate_public_statement:homepage", report["reasons"]
        )

    def test_stale_publication_fails_at_two_hours(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root, NOW - timedelta(minutes=121))
            report = health.assess(root, now=NOW, max_age_minutes=120)
        self.assertEqual("failed", report["status"])
        self.assertIn("status_stale", report["reasons"])

    def test_page_marker_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(root)
            page = root / health.LANGUAGES["pl"]["news_page"]
            page.write_text(f'<script src="{health.RUNTIME}" defer></script>', encoding="utf-8")
            report = health.assess(root, now=NOW, max_age_minutes=120)
        self.assertEqual("failed", report["status"])
        self.assertIn("pl:static_marker_mismatch:news_page", report["reasons"])


if __name__ == "__main__":
    unittest.main()
