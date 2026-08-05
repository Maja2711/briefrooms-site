from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import install_investment_room_recovery as installer


class InvestmentRoomRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recovery = (SCRIPTS / "portfolio-10k-en-recovery.js").read_text(encoding="utf-8")
        self.nav_order = (SCRIPTS / "investment-room-nav-order.js").read_text(encoding="utf-8")

    def test_recovery_binds_tabs_before_network_data(self) -> None:
        self.assertIn("function bindTabs()", self.recovery)
        self.assertIn("document.addEventListener('click'", self.recovery)
        self.assertIn("bindTabs();\n\n    const portfolioPromise", self.recovery)
        self.assertIn("document.body.dataset.enInvestmentTabs = 'ready'", self.recovery)

    def test_recovery_uses_direct_usd_source_timeout_and_partial_failure(self) -> None:
        self.assertIn("/data/investments/portfolio_10k_usd.json", self.recovery)
        self.assertIn("AbortController", self.recovery)
        self.assertIn("Promise.allSettled", self.recovery)
        self.assertIn("Portfolio data remains active", self.recovery)
        self.assertNotIn("Promise.all([portfolioPromise", self.recovery)

    def test_navigation_order_places_investing_after_news(self) -> None:
        self.assertIn("['news', 'investing', 'health', 'science', 'geopolitics', 'about']", self.nav_order)
        self.assertIn("nav.appendChild(link)", self.nav_order)
        self.assertIn("data-section", self.nav_order)

    def test_installer_normalises_both_languages(self) -> None:
        source = (
            '<html><body>'
            '<script src="/scripts/portfolio-10k-dashboard-en.js?v=5" defer></script>'
            '<script src="/scripts/ai-tournament-public.js?v=3" defer></script>'
            '<script src="/scripts/ai-tournament-readiness.js?v=3" defer></script>'
            '</body></html>'
        )
        en = installer.patch_page(source, language="en")
        self.assertIn('portfolio-10k-dashboard-en.js?v=6', en)
        self.assertEqual(en.count('ai-tournament-public.js?v=5'), 1)
        self.assertEqual(en.count('ai-tournament-readiness.js?v=5'), 1)
        self.assertEqual(en.count('ai-tournament-company-profiles.js?v=1'), 1)
        self.assertEqual(en.count('ai-tournament-summary.js?v=1'), 1)
        self.assertEqual(en.count('investment-room-nav-order.js?v=1'), 1)
        self.assertEqual(en.count('portfolio-10k-en-recovery.js?v=1'), 1)

        pl = installer.patch_page(source, language="pl")
        self.assertEqual(pl.count('investment-room-nav-order.js?v=1'), 1)
        self.assertNotIn('portfolio-10k-en-recovery.js', pl)


if __name__ == "__main__":
    unittest.main()
