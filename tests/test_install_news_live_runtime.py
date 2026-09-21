from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import install_news_live_runtime as runtime


class HomepageStaticFreshnessGuardTests(unittest.TestCase):
    def test_exact_24_hours_is_fresh_but_older_is_stale(self) -> None:
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        exact = now - timedelta(hours=24)
        older = exact - timedelta(microseconds=1)
        self.assertTrue(runtime._is_fresh(exact.isoformat(), now)[0])
        self.assertFalse(runtime._is_fresh(older.isoformat(), now)[0])
        self.assertFalse(runtime._is_fresh(None, now)[0])

    def test_static_homepage_hides_stale_and_unknown_cards(self) -> None:
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        fresh = (now - timedelta(hours=2)).isoformat()
        stale = (now - timedelta(hours=24, seconds=1)).isoformat()
        source = (
            '<div id="latest-briefs" class="brief-grid">'
            '<!-- HOME_BRIEFS_START -->'
            '<a class="brief-card" href="/pl/briefy/fresh-aaaaaaaaaaaa.html">Fresh</a>'
            '<a class="brief-card" href="/pl/briefy/stale-bbbbbbbbbbbb.html">Stale</a>'
            '<a class="brief-card" href="/pl/briefy/unknown-cccccccccccc.html">Unknown</a>'
            '<!-- HOME_BRIEFS_END -->'
            '</div>'
        )
        publications = {
            '/pl/briefy/fresh-aaaaaaaaaaaa.html': fresh,
            '/pl/briefy/stale-bbbbbbbbbbbb.html': stale,
        }
        with patch.object(runtime, '_homepage_publication_map', return_value=publications):
            rendered = runtime.apply_homepage_freshness(source, 'pl', now)

        self.assertIn('data-home-freshness-policy="max-24h-public-news-display-v1"', rendered)
        self.assertIn('data-home-image-policy="https-image-required-v1"', rendered)
        self.assertRegex(
            rendered,
            r'<a class="brief-card" href="/pl/briefy/fresh-aaaaaaaaaaaa\.html" data-home-published-at="[^"]+">',
        )
        self.assertRegex(
            rendered,
            r'<a class="brief-card" href="/pl/briefy/stale-bbbbbbbbbbbb\.html" data-home-published-at="[^"]+" hidden aria-hidden="true" data-home-stale="true">',
        )
        self.assertIn(
            '<a class="brief-card" href="/pl/briefy/unknown-cccccccccccc.html" hidden aria-hidden="true" data-home-stale="true">',
            rendered,
        )

    def test_installer_adds_exactly_one_image_floor_guard_after_news_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'page.html'
            path.write_text(
                '<html><body><main></main>'
                '<script src="/scripts/news-live.js?v=5" defer></script>'
                '<script src="/scripts/home-card-floor.js?v=old" defer></script>'
                '</body></html>',
                encoding='utf-8',
            )
            runtime.install(path)
            rendered = path.read_text(encoding='utf-8')

        live_url = '/scripts/news-live.js?v=8&rev=global24h'
        floor_url = '/scripts/home-card-floor.js?v=4'
        self.assertEqual(rendered.count(live_url), 1)
        self.assertEqual(rendered.count(floor_url), 1)
        self.assertLess(rendered.index(live_url), rendered.index(floor_url))
        self.assertNotIn('/scripts/home-intelligence-layout.js', rendered)

    def test_homepage_installer_adds_lab_and_intelligence_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'pl' / 'index.html'
            path.parent.mkdir(parents=True)
            path.write_text(
                '<html><body><main></main>'
                '<script src="/scripts/home-lab.js?v=old" defer></script>'
                '<script src="/scripts/home-intelligence-layout.js?v=old" defer></script>'
                '</body></html>',
                encoding='utf-8',
            )
            with patch.object(runtime, 'apply_homepage_freshness', side_effect=lambda source, lang: source):
                runtime.install(path)
            rendered = path.read_text(encoding='utf-8')

        lab_url = '/scripts/home-lab.js?v=1'
        intelligence_url = '/scripts/home-intelligence-layout.js?v=1'
        live_url = '/scripts/news-live.js?v=8&rev=global24h'
        floor_url = '/scripts/home-card-floor.js?v=4'
        self.assertEqual(rendered.count(lab_url), 1)
        self.assertEqual(rendered.count(intelligence_url), 1)
        self.assertEqual(rendered.count(live_url), 1)
        self.assertEqual(rendered.count(floor_url), 1)
        self.assertLess(rendered.index(lab_url), rendered.index(intelligence_url))
        self.assertLess(rendered.index(intelligence_url), rendered.index(live_url))

    def test_homepage_intelligence_runtime_is_syntax_valid_and_non_destructive(self) -> None:
        script = runtime.ROOT / 'scripts' / 'home-intelligence-layout.js'
        completed = subprocess.run(
            ['node', '--check', str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = script.read_text(encoding='utf-8')
        self.assertIn("heading:'Co dziś naprawdę ma znaczenie'", source)
        self.assertIn("engineWeekly:'BriefRooms Trading Engine · WEEKLY'", source)
        self.assertIn("engineStock:'BriefRooms Stock Trading · OPEN'", source)
        self.assertIn('canonicalWeekly?copy.engineWeekly:copy.engineStock', source)
        self.assertIn("shareTitle:'BriefRooms Ci się przydał? Podaj dalej.'", source)
        self.assertIn("lab:'BriefRooms Lab — modele, testy i wyniki'", source)
        self.assertNotIn('paper trading', source.lower())
        self.assertNotIn('innerHTML=', source)

    def test_floor_guard_requires_twelve_cards_with_real_https_images(self) -> None:
        script = runtime.ROOT / 'scripts' / 'home-card-floor.js'
        completed = subprocess.run(
            ['node', '--check', str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = script.read_text(encoding='utf-8')
        self.assertIn('var MIN_CARDS = 12;', source)
        self.assertIn('function safeImageUrl(value)', source)
        self.assertIn("data.home_reserve", source)
        self.assertNotIn("Object.values(sections)", source)
        self.assertIn('function makeImageCard(document, story, lang, nowMs)', source)
        self.assertIn("image.addEventListener('error', onFailure", source)
        self.assertIn("context.failedStoryIds.add(cardIdentity(card));", source)
        self.assertNotIn('makeFallbackCard', source)
        self.assertNotIn('ensureFallback', source)
        self.assertNotIn('fallback-art', source)

    def test_news_runtime_home_cards_do_not_render_br_placeholders(self) -> None:
        script = runtime.ROOT / 'scripts' / 'news-live.js'
        source = script.read_text(encoding='utf-8')
        self.assertIn('const HOME_LIMIT = 12;', source)
        self.assertIn("const HOME_IMAGE_POLICY = 'https-image-required-v1';", source)
        self.assertIn('function safeImage(value)', source)
        self.assertNotIn('<div class="fallback-art" aria-hidden="true">BR</div>', source)


if __name__ == '__main__':
    unittest.main()
