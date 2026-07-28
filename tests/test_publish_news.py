from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts import verify_news_production
from scripts.publish_news import (
    PRODUCTION_COMMANDS,
    PublicationContext,
    PublicationError,
    aggregate_source_report,
    build_manifest,
    canonical_url,
    copy_repository,
    failed_manifest,
    prepare,
    promote,
    refresh_plan,
    repository_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/news"


def context(run_id: str, hour: int) -> PublicationContext:
    return PublicationContext(
        run_id=run_id,
        generated_at=datetime(2026, 7, 27, hour, 0, tzinfo=timezone.utc),
        source_commit_sha="unknown",
    )


class AtomicNewsPublicationTests(unittest.TestCase):
    def test_failed_attempt_preserves_ai_cache_for_the_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            repository = sandbox / "repository"
            copy_repository(ROOT, repository)
            stage = sandbox / "stage"

            def fail_after_cache(site_root: Path, _diagnostics: Path) -> None:
                cache = site_root / ".cache/retry-proof.json"
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text('{"approved": true}\n', encoding="utf-8")
                raise PublicationError("transient provider failure")

            with mock.patch(
                "scripts.publish_news.run_production_build",
                side_effect=fail_after_cache,
            ):
                with self.assertRaisesRegex(
                    PublicationError,
                    "transient provider failure",
                ):
                    prepare(repository, stage, context("retry-first", 8))

            prepare(repository, stage, context("retry-second", 9), FIXTURES)
            self.assertTrue((stage / "site/.cache/retry-proof.json").is_file())

    def test_plan_refresh_allows_unrelated_main_data_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            repository = sandbox / "repository"
            copy_repository(ROOT, repository)
            stage = sandbox / "stage"
            prepare(repository, stage, context("refresh-plan", 9), FIXTURES)

            unrelated = repository / "data/investments/unrelated.json"
            unrelated.parent.mkdir(parents=True, exist_ok=True)
            unrelated.write_text('{"updated": true}\n', encoding="utf-8")
            refresh_plan(repository, stage)

            generator_change = repository / "scripts/new-news-input.py"
            generator_change.write_text("VALUE = 1\n", encoding="utf-8")
            with self.assertRaisesRegex(PublicationError, "generator inputs changed"):
                refresh_plan(repository, stage)

    def test_production_pipeline_enforces_external_homepage_photos(self) -> None:
        commands = {name: script for name, script, *_args in PRODUCTION_COMMANDS}
        self.assertEqual(
            "scripts/enforce_external_media_policy.py",
            commands["external_media_policy"],
        )
        self.assertEqual(
            "scripts/enforce_homepage_photo_only.py",
            commands["homepage_photo_policy"],
        )

    def test_permanent_briefs_are_rendered_before_homepage_normalization(self) -> None:
        labels = [command[0] for command in PRODUCTION_COMMANDS]
        generated = labels.index("generate_briefs")
        self.assertLess(generated, labels.index("normalize_pl"))
        self.assertLess(generated, labels.index("normalize_en"))
        self.assertLess(labels.index("quality_gate"), generated)
        self.assertLess(labels.index("dedupe_home"), generated)

    def test_publish_news_is_the_only_workflow_owner_of_shared_news_outputs(self) -> None:
        shared_markers = (
            "pl/index.html",
            "en/index.html",
            "pl/home_brief.json",
            "en/home_brief.json",
            "pl/aktualnosci.html",
            "en/news.html",
            "pl/briefy",
            "en/briefs",
        )
        owners = []
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            source = path.read_text(encoding="utf-8")
            if "git add" in source and any(marker in source for marker in shared_markers):
                owners.append(path.name)
        self.assertEqual(["publish-news.yml"], sorted(owners))

    def test_tracking_parameters_do_not_create_new_story_urls(self) -> None:
        first = canonical_url(
            "https://Example.com/news/item/?utm_source=rss&article=42#top"
        )
        second = canonical_url("https://example.com/news/item?article=42")
        self.assertEqual(first, second)

    def test_fixture_drives_complete_atomic_publication_and_failure_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            repository = sandbox / "repository"
            copy_repository(ROOT, repository)
            initial_fingerprint = repository_fingerprint(repository)

            incomplete_fixtures = sandbox / "incomplete-rss"
            incomplete_fixtures.mkdir()
            shutil.copy2(FIXTURES / "pl.xml", incomplete_fixtures / "pl.xml")
            failed_stage = sandbox / "failed-stage"
            with self.assertRaises(FileNotFoundError):
                prepare(
                    repository,
                    failed_stage,
                    context("fixture-failed", 10),
                    incomplete_fixtures,
                )
            self.assertEqual(initial_fingerprint, repository_fingerprint(repository))
            failed = json.loads(
                (failed_stage / "diagnostics/failed-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(failed["served_from_last_good"])
            self.assertEqual("failed", failed["pl"]["status"])
            self.assertNotIn("homepage_updated_at", failed["pl"])

            parallel_stage = sandbox / "parallel-stage"
            prepare(
                repository,
                parallel_stage,
                context("fixture-parallel", 10),
                FIXTURES,
            )
            manifest_path = repository / "data/news_publication_status.json"
            original_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text('{"parallel": true}\n', encoding="utf-8")
            with self.assertRaisesRegex(PublicationError, "changed after prepare"):
                promote(repository, parallel_stage)
            if original_manifest is None:
                manifest_path.unlink()
            else:
                manifest_path.write_bytes(original_manifest)

            first_stage = sandbox / "first-stage"
            first = prepare(
                repository,
                first_stage,
                context("fixture-first", 10),
                FIXTURES,
            )
            self.assertEqual("fresh", first["pl"]["status"])
            self.assertEqual("fresh", first["en"]["status"])
            self.assertEqual(10, first["pl"]["new_articles"])
            self.assertEqual(10, first["en"]["new_articles"])
            changed = promote(repository, first_stage)
            self.assertIn("pl/index.html", changed)
            self.assertIn("en/index.html", changed)

            permanent_links: dict[str, list[str]] = {}
            for lang, news_path, home_path, index_path, archive_path in (
                (
                    "pl",
                    "pl/aktualnosci.html",
                    "pl/home_brief.json",
                    "pl/index.html",
                    "data/permanent_briefs_pl.json",
                ),
                (
                    "en",
                    "en/news.html",
                    "en/home_brief.json",
                    "en/index.html",
                    "data/permanent_briefs_en.json",
                ),
            ):
                news = (repository / news_path).read_text(encoding="utf-8")
                home = json.loads((repository / home_path).read_text(encoding="utf-8"))
                index = (repository / index_path).read_text(encoding="utf-8")
                archive = json.loads(
                    (repository / archive_path).read_text(encoding="utf-8")
                )
                links = [item["link"] for item in home["latest"]]
                self.assertEqual(10, home["count"])
                self.assertEqual(10, len(home["latest"]))
                self.assertEqual(10, len(set(map(canonical_url, links))))
                self.assertGreaterEqual(news.count("<li>"), 10)
                self.assertGreaterEqual(index.count('class="brief-card"'), 10)
                self.assertIn('content="fixture-first"', news)
                self.assertIn('content="fixture-first"', index)
                self.assertTrue(all(f"/{lang}/" in link for link in links))
                fixture_records = [
                    item
                    for item in archive["items"]
                    if "fixture.example" in item.get("source_url", "")
                ]
                self.assertGreaterEqual(len(fixture_records), 10)
                permanent_links[lang] = [
                    item["permalink"] for item in fixture_records[:3]
                ]
                for permalink in permanent_links[lang]:
                    self.assertTrue((repository / permalink.lstrip("/")).is_file())
                    self.assertIn(permalink, (repository / "sitemap.xml").read_text(encoding="utf-8"))

            promoted_manifest = json.loads(
                (repository / "data/news_publication_status.json").read_text(
                    encoding="utf-8"
                )
            )
            for lang in ("pl", "en"):
                home = json.loads(
                    (repository / f"{lang}/home_brief.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    datetime.fromisoformat(home["updated_at"]),
                    datetime.fromisoformat(
                        promoted_manifest[lang]["homepage_updated_at"]
                    ),
                )

            with mock.patch.object(verify_news_production, "ROOT", repository):
                def stale_fetch(
                    _base: str,
                    path: str,
                    _cache_key: str,
                    _attempt: int,
                    _timeout: float,
                ) -> bytes:
                    body = (repository / path).read_bytes()
                    if path == "pl/index.html":
                        title = json.loads(
                            (repository / "pl/home_brief.json").read_text(encoding="utf-8")
                        )["latest"][0]["title"]
                        return body.replace(title.encode("utf-8"), b"OLD STORY", 1)
                    return body

                with mock.patch.object(
                    verify_news_production, "fetch", side_effect=stale_fetch
                ):
                    with self.assertRaisesRegex(
                        verify_news_production.VerificationError,
                        "does not match",
                    ):
                        verify_news_production.verify_attempt(
                            "https://briefrooms.test",
                            "fixture-first",
                            "a" * 40,
                            1,
                            1,
                        )

            first_home_times = {
                lang: promoted_manifest[lang]["homepage_updated_at"]
                for lang in ("pl", "en")
            }
            second_stage = sandbox / "second-stage"
            second = prepare(
                repository,
                second_stage,
                context("fixture-second", 14),
                FIXTURES,
            )
            for lang in ("pl", "en"):
                self.assertEqual("no-new-stories", second[lang]["status"])
                self.assertEqual(0, second[lang]["new_articles"])
                self.assertEqual(
                    first_home_times[lang],
                    second[lang]["homepage_updated_at"],
                )
                self.assertEqual(
                    promoted_manifest[lang]["last_new_publication_at"],
                    second[lang]["last_new_publication_at"],
                )
                self.assertEqual(
                    "2026-07-27T14:00:00+00:00",
                    second[lang]["last_successful_fetch_at"],
                )
                self.assertEqual(
                    "2026-07-27T14:00:00+00:00",
                    second[lang]["last_successful_publication_at"],
                )
            promote(repository, second_stage)
            for relative in (
                "pl/index.html",
                "pl/aktualnosci.html",
                "en/index.html",
                "en/news.html",
            ):
                self.assertIn(
                    'content="fixture-second"',
                    (repository / relative).read_text(encoding="utf-8"),
                )

            events = sandbox / "source-events.jsonl"
            events.write_text(
                "\n".join(
                    json.dumps(event)
                    for event in (
                        {
                            "kind": "feed",
                            "lang": "pl",
                            "pipeline": "news",
                            "url": "https://down.example/pl",
                            "fetched": False,
                            "parsed": 0,
                            "error": "timeout",
                        },
                        {
                            "kind": "feed",
                            "lang": "pl",
                            "pipeline": "news",
                            "url": "https://up.example/pl",
                            "fetched": True,
                            "parsed": 4,
                            "error": "",
                        },
                        {
                            "kind": "item",
                            "lang": "pl",
                            "pipeline": "news",
                            "url": "https://up.example/pl",
                            "result": "accepted",
                            "published_at": "2026-07-27T09:00:00+00:00",
                        },
                        {
                            "kind": "feed",
                            "lang": "en",
                            "pipeline": "news",
                            "url": "https://up.example/en",
                            "fetched": True,
                            "parsed": 3,
                            "error": "",
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            report = aggregate_source_report(events, context("report", 15))
            self.assertEqual(1, report["languages"]["pl"]["totals"]["errors"])
            self.assertEqual(1, report["languages"]["pl"]["totals"]["fetched"])
            self.assertEqual(1, report["languages"]["pl"]["totals"]["accepted"])

            all_down = {
                "languages": {
                    lang: {
                        "totals": {
                            "fetched": 0,
                            "parsed": 0,
                            "accepted": 0,
                        }
                    }
                    for lang in ("pl", "en")
                }
            }
            with self.assertRaisesRegex(PublicationError, "source acquisition failed"):
                build_manifest(
                    repository,
                    repository,
                    all_down,
                    context("all-down", 16),
                )

    def test_failed_manifest_never_fabricates_content_freshness(self) -> None:
        result = failed_manifest(context("failure", 17), RuntimeError("all feeds down"))
        self.assertTrue(result["served_from_last_good"])
        for lang in ("pl", "en"):
            self.assertEqual("failed", result[lang]["status"])
            self.assertNotIn("homepage_updated_at", result[lang])
            self.assertNotIn("news_page_updated_at", result[lang])


if __name__ == "__main__":
    unittest.main()
