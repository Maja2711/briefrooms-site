from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import validate_live_news_health as health


NOW = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
MARKER = "test-marker"


def story(index: int) -> dict:
    return {
        "title": f"Story {index}",
        "link": f"https://example.com/{index}",
        "image": f"https://example.com/{index}.jpg",
        "source": "Example",
        "published_at": (NOW - timedelta(minutes=index)).isoformat(),
    }


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
                section: [story(i) for i in range(health.MIN_SECTION)]
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
