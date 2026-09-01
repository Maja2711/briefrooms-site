from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import install_news_live_runtime as runtime


class HomepageStaticFreshnessGuardTests(unittest.TestCase):
    def test_exact_72_hours_is_fresh_but_older_is_stale(self) -> None:
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        exact = now - timedelta(days=3)
        older = exact - timedelta(microseconds=1)
        self.assertTrue(runtime._is_fresh(exact.isoformat(), now)[0])
        self.assertFalse(runtime._is_fresh(older.isoformat(), now)[0])
        self.assertFalse(runtime._is_fresh(None, now)[0])

    def test_static_homepage_hides_stale_and_unknown_cards(self) -> None:
        now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        fresh = (now - timedelta(hours=2)).isoformat()
        stale = (now - timedelta(days=3, seconds=1)).isoformat()
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

        self.assertIn('data-home-freshness-policy="max-72h-v1"', rendered)
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

    def test_installer_adds_exactly_one_floor_guard_after_news_runtime(self) -> None:
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

        self.assertEqual(rendered.count('/scripts/news-live.js?v=6'), 1)
        self.assertEqual(rendered.count('/scripts/home-card-floor.js?v=1'), 1)
        self.assertLess(rendered.index('/scripts/news-live.js?v=6'), rendered.index('/scripts/home-card-floor.js?v=1'))

    def test_floor_guard_is_valid_javascript_and_has_hard_minimum_of_ten(self) -> None:
        script = runtime.ROOT / 'scripts' / 'home-card-floor.js'
        completed = subprocess.run(
            ['node', '--check', str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        source = script.read_text(encoding='utf-8')
        self.assertIn('var MIN_CARDS = 10;', source)
        self.assertIn("card.dataset.brPhotoGuarded = '1';", source)
        self.assertIn("container.dataset.homeCardFloorStatus = current.length >= MIN_CARDS ? 'met' : 'underfilled';", source)


if __name__ == '__main__':
    unittest.main()
