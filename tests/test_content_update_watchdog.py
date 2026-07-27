from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts import content_update_watchdog as watchdog


NOW = datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def build_repository(root: Path) -> dict:
    write_json(
        root / "data/content_update_contract.json",
        {
            "contract_version": "2.0.0",
            "watchdog": {
                "successful_fetch_stale_after_hours": 8,
                "new_publication_stale_after_hours": 24,
            },
        },
    )
    generated = (NOW - timedelta(hours=2)).isoformat()
    manifest = {
        "publication_id": "pub-1",
        "pl": {
            "status": "fresh",
            "last_successful_fetch_at": generated,
            "last_successful_publication_at": generated,
            "last_new_publication_at": generated,
            "homepage_updated_at": generated,
            "news_page_updated_at": generated,
            "served_from_last_good": False,
        },
        "en": {
            "status": "fresh",
            "last_successful_fetch_at": generated,
            "last_successful_publication_at": generated,
            "last_new_publication_at": generated,
            "homepage_updated_at": generated,
            "news_page_updated_at": generated,
            "served_from_last_good": False,
        },
    }
    write_json(root / "data/news_publication_status.json", manifest)
    for lang, news_file in (("pl", "aktualnosci.html"), ("en", "news.html")):
        write_json(root / f"{lang}/home_brief.json", {"updated_at": generated})
        path = root / lang / news_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '<meta name="briefrooms-news-updated-at" '
            f'content="{generated}">',
            encoding="utf-8",
        )
    for relative in watchdog.CORE_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("current", encoding="utf-8")
    return manifest


class ContentUpdateWatchdogTests(unittest.TestCase):
    def test_stale_polish_fetch_is_reported_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_repository(root)
            manifest["pl"]["last_successful_fetch_at"] = (
                NOW - timedelta(hours=9)
            ).isoformat()
            write_json(root / "data/news_publication_status.json", manifest)
            report = watchdog.assess_health(root, now=NOW)
        self.assertTrue(report["languages"]["pl"]["stale"])
        self.assertIn("successful_fetch_stale", report["languages"]["pl"]["reasons"])
        self.assertFalse(report["languages"]["en"]["stale"])

    def test_stale_english_new_publication_is_reported_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_repository(root)
            manifest["en"]["last_new_publication_at"] = (
                NOW - timedelta(hours=25)
            ).isoformat()
            write_json(root / "data/news_publication_status.json", manifest)
            report = watchdog.assess_health(root, now=NOW)
        self.assertFalse(report["languages"]["pl"]["stale"])
        self.assertTrue(report["languages"]["en"]["stale"])
        self.assertIn("new_publication_stale", report["languages"]["en"]["reasons"])

    def test_no_new_stories_is_healthy_without_touching_content_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = build_repository(root)
            original_home_time = manifest["pl"]["homepage_updated_at"]
            manifest["pl"]["status"] = "no-new-stories"
            manifest["pl"]["last_successful_fetch_at"] = (
                NOW - timedelta(minutes=10)
            ).isoformat()
            write_json(root / "data/news_publication_status.json", manifest)
            report = watchdog.assess_health(root, now=NOW)
        self.assertFalse(report["languages"]["pl"]["stale"])
        self.assertEqual(original_home_time, manifest["pl"]["homepage_updated_at"])

    def test_production_mismatch_requests_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_repository(root)
            with mock.patch.object(
                watchdog,
                "production_matches_main",
                return_value=(False, "pl/index.html differs"),
            ):
                report = watchdog.assess_health(
                    root,
                    now=NOW,
                    base_url="https://briefrooms.test",
                )
        self.assertTrue(report["production_mismatch"])
        self.assertTrue(report["recovery_needed"])
        self.assertIn("differs", report["production_error"])

    def test_matching_current_production_is_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_repository(root)
            with mock.patch.object(
                watchdog,
                "production_matches_main",
                return_value=(True, ""),
            ):
                report = watchdog.assess_health(
                    root,
                    now=NOW,
                    base_url="https://briefrooms.test",
                )
        self.assertEqual("healthy", report["status"])
        self.assertFalse(report["recovery_needed"])


if __name__ == "__main__":
    unittest.main()
