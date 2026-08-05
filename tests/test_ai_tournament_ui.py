from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import install_ai_tournament_ui as installer


class AiTournamentUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (ROOT / "scripts" / "ai-tournament-public.js").read_text(encoding="utf-8")
        self.profile_script = (ROOT / "scripts" / "ai-tournament-company-profiles.js").read_text(encoding="utf-8")
        self.runtime_script = (ROOT / "scripts" / "portfolio-10k-execution-finalizer.js").read_text(encoding="utf-8")
        self.config = json.loads((ROOT / "data" / "ai_tournament" / "config.json").read_text(encoding="utf-8"))
        self.profiles = json.loads((ROOT / "data" / "ai_tournament" / "company_profiles.json").read_text(encoding="utf-8"))

    def test_redesign_removes_placeholder_layout_and_uses_real_content_modules(self) -> None:
        self.assertIn("#agents-preview,#agent-cards,#agent-log{display:block!important", self.script)
        self.assertIn('class="aitx-overview"', self.script)
        self.assertIn('class="aitx-left-grid"', self.script)
        self.assertIn('class="aitx-ranking-panel"', self.script)
        self.assertIn("performanceVisual(data, rows)", self.script)
        self.assertIn("currentBars(rows)", self.script)

    def test_locked_thesis_is_collapsed_with_native_details(self) -> None:
        self.assertIn('<details class="aitx-thesis">', self.script)
        self.assertNotIn('<details class="aitx-thesis" open', self.script)
        self.assertIn('<summary><span>${esc(T.thesis)}</span>', self.script)
        self.assertIn("expand: 'Rozwiń'", self.script)
        self.assertIn("collapse: 'Zwiń'", self.script)
        self.assertNotIn('class="ait-decision"', self.script)

    def test_only_important_agent_metrics_are_visible(self) -> None:
        self.assertIn("T.result", self.script)
        self.assertIn("T.value", self.script)
        self.assertIn("T.cash", self.script)
        self.assertIn("T.alpha", self.script)
        self.assertNotIn("T.drawdown", self.script)
        self.assertNotIn("T.positions", self.script)

    def test_company_profiles_cover_the_full_locked_universe(self) -> None:
        self.assertEqual(self.profiles["schema_version"], "ai-tournament-company-profiles-v1")
        universe = set(self.config["universe"])
        profile_tickers = set(self.profiles["profiles"])
        self.assertEqual(profile_tickers, universe)
        self.assertEqual(len(profile_tickers), 55)
        for ticker, profile in self.profiles["profiles"].items():
            self.assertTrue(profile["name"], ticker)
            self.assertTrue(profile["sector_pl"], ticker)
            self.assertTrue(profile["sector_en"], ticker)
            self.assertTrue(profile["description_pl"], ticker)
            self.assertTrue(profile["description_en"], ticker)

    def test_company_profile_layer_is_accessible_and_optional(self) -> None:
        self.assertIn("role=\"dialog\"", self.profile_script)
        self.assertIn("aria-modal=\"true\"", self.profile_script)
        self.assertIn("aria-haspopup", self.profile_script)
        self.assertIn("event.key === 'Escape'", self.profile_script)
        self.assertIn("MutationObserver", self.profile_script)
        self.assertIn(".aitx-holdings span:not([data-company-profile-ready])", self.profile_script)
        self.assertIn("T.open", self.profile_script)
        self.assertIn("description_pl", self.profile_script)
        self.assertIn("description_en", self.profile_script)

    def test_existing_portfolio_runtime_bootstraps_profiles_without_html_dependency(self) -> None:
        self.assertIn("function loadTournamentCompanyProfiles()", self.runtime_script)
        self.assertIn("/scripts/ai-tournament-company-profiles.js?v=1", self.runtime_script)
        self.assertIn("loadTournamentCompanyProfiles();", self.runtime_script)
        self.assertIn("script[src*=\"/scripts/ai-tournament-company-profiles.js\"]", self.runtime_script)

    def test_installer_deploys_v5_and_company_profiles_once(self) -> None:
        self.assertEqual(installer.SCRIPT_VERSION, "5")
        self.assertEqual(installer.PROFILE_VERSION, "1")
        source = (
            '<html><body>'
            '<script src="/scripts/ai-tournament-public.js?v=4" defer></script>'
            '<script src="/scripts/ai-tournament-readiness.js?v=4" defer></script>'
            '<script src="/scripts/ai-tournament-company-profiles.js?v=old" defer></script>'
            '</body></html>'
        )
        patched = installer.patch_text(source)
        self.assertEqual(patched.count('ai-tournament-public.js?v=5'), 1)
        self.assertEqual(patched.count('ai-tournament-readiness.js?v=5'), 1)
        self.assertEqual(patched.count('ai-tournament-company-profiles.js?v=1'), 1)
        self.assertNotIn('ai-tournament-public.js?v=4', patched)
        self.assertNotIn('ai-tournament-readiness.js?v=4', patched)
        self.assertNotIn('ai-tournament-company-profiles.js?v=old', patched)


if __name__ == "__main__":
    unittest.main()
