from __future__ import annotations

import unittest

from scripts.finalize_portfolio_10k_html import CANONICAL_VERSIONS, finalize_text


class Portfolio10kHtmlFinalizerTests(unittest.TestCase):
    def test_normalizes_stale_versions_and_duplicates(self) -> None:
        source = (
            '<html><body>'
            '<script src="/scripts/portfolio-10k-dashboard.js?v=8" defer></script>'
            '<script src="/scripts/portfolio-10k-experience-store.js?v=1" defer></script>'
            '<script src="/scripts/portfolio-10k-lab-health.js?v=2" defer></script>'
            '<script src="/scripts/ai-tournament-public.js?v=6" defer></script>'
            '<script src="/scripts/ai-tournament-readiness.js?v=6" defer></script>'
            '<script src="/scripts/ai-tournament-company-profiles.js?v=2" defer></script>'
            '<script src="/scripts/ai-tournament-summary.js?v=2" defer></script>'
            '<script src="/scripts/portfolio-10k-navigation-guard.js?v=3" defer></script>'
            '<script src="/scripts/portfolio-10k-navigation-guard.js?v=5" defer></script>'
            '</body></html>'
        )
        out = finalize_text(source)
        for name, version in CANONICAL_VERSIONS.items():
            if name.endswith('-en.js'):
                continue
            if name == 'portfolio-10k-dashboard.js' or name in (
                'portfolio-10k-experience-store.js',
                'portfolio-10k-lab-health.js',
                'ai-tournament-public.js',
                'ai-tournament-readiness.js',
                'ai-tournament-company-profiles.js',
                'ai-tournament-summary.js',
                'portfolio-10k-navigation-guard.js',
            ):
                self.assertIn(f'/scripts/{name}?v={version}', out)
        self.assertEqual(out.count('portfolio-10k-navigation-guard.js'), 1)
        self.assertNotIn('portfolio-10k-navigation-guard.js?v=3', out)
        self.assertNotIn('portfolio-10k-lab-health.js?v=2', out)

    def test_restores_missing_protected_assets(self) -> None:
        out = finalize_text('<html><body><script src="/scripts/portfolio-10k-dashboard.js?v=1" defer></script></body></html>')
        for name in (
            'portfolio-10k-experience-store.js',
            'portfolio-10k-lab-health.js',
            'ai-tournament-public.js',
            'ai-tournament-readiness.js',
            'ai-tournament-company-profiles.js',
            'ai-tournament-summary.js',
            'portfolio-10k-navigation-guard.js',
        ):
            self.assertEqual(out.count(name), 1)

    def test_navigation_guard_is_final_protected_script(self) -> None:
        out = finalize_text('<html><body></body></html>')
        nav = out.rfind('portfolio-10k-navigation-guard.js')
        health = out.rfind('portfolio-10k-lab-health.js')
        tournament = out.rfind('ai-tournament-summary.js')
        self.assertGreater(nav, health)
        self.assertGreater(nav, tournament)


if __name__ == '__main__':
    unittest.main()
