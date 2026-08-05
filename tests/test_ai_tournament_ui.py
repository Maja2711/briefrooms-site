from __future__ import annotations

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

    def test_installer_deploys_v4_once_for_both_languages(self) -> None:
        self.assertEqual(installer.SCRIPT_VERSION, "4")
        source = (
            '<html><body>'
            '<script src="/scripts/ai-tournament-public.js?v=3" defer></script>'
            '<script src="/scripts/ai-tournament-readiness.js?v=3" defer></script>'
            '</body></html>'
        )
        patched = installer.patch_text(source)
        self.assertEqual(patched.count('ai-tournament-public.js?v=4'), 1)
        self.assertEqual(patched.count('ai-tournament-readiness.js?v=4'), 1)
        self.assertNotIn('ai-tournament-public.js?v=3', patched)
        self.assertNotIn('ai-tournament-readiness.js?v=3', patched)


if __name__ == "__main__":
    unittest.main()
