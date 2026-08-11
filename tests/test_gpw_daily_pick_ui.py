from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GpwDailyPickUiTests(unittest.TestCase):
    def test_module_is_present_only_in_polish_portfolio_room(self):
        polish = (ROOT / "pl/inwestycje/portfel-10k.html").read_text(encoding="utf-8")
        english = (ROOT / "en/investing/portfolio-10k.html").read_text(encoding="utf-8")
        self.assertEqual(polish.count('id="gpw-daily-pick-root"'), 1)
        self.assertEqual(polish.count("/scripts/gpw-daily-pick-public.js?v=2"), 1)
        self.assertEqual(polish.count("/assets/gpw-daily-pick.css?v=2"), 1)
        self.assertIn("BriefRooms Research · daily trading", polish)
        self.assertIn("Pokaż więcej danych", polish)
        self.assertNotIn("gpw-daily-pick", english)
        self.assertNotIn("PORANNY WYBÓR GPW", english)

    def test_client_never_presents_stale_weekday_record_as_current(self):
        script = (ROOT / "scripts/gpw-daily-pick-public.js").read_text(encoding="utf-8")
        self.assertIn('payload.date !== today && weekday >= 1 && weekday <= 5', script)
        self.assertIn('"ANALIZA DANYCH — TRWA"', script)
        self.assertIn("Brak dzisiaj wyboru", script)
        self.assertNotIn("Publikacja zatrzymana przez zabezpieczenia", script)
        self.assertNotIn("AWARIA DANYCH", script)
        self.assertNotIn("paper trades", script)
        self.assertIn('cache: "no-store"', script)

    def test_more_data_section_only_opens_for_populated_values(self):
        script = (ROOT / "scripts/gpw-daily-pick-public.js").read_text(encoding="utf-8")
        self.assertIn('details.hidden = sections.length === 0', script)
        self.assertIn("Skład oceny", script)
        self.assertIn("Wyniki zakończonych transakcji", script)

    def test_workflow_has_dst_pairs_watchdog_gemini_and_scoped_commit(self):
        workflow = (ROOT / ".github/workflows/gpw-daily-pick-pl.yml").read_text(encoding="utf-8")
        self.assertIn('GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}', workflow)
        self.assertNotIn("OPENAI_API_KEY", workflow)
        for cron in ("35 5", "35 6", "17 6", "17 7"):
            self.assertIn(cron, workflow)
        self.assertIn("data/investments/gpw_daily_pick_history", workflow)
        self.assertNotIn("git add .", workflow)
        deploy = (ROOT / ".github/workflows/deploy-production.yml").read_text(encoding="utf-8")
        self.assertIn('"Publish PL GPW Daily Pick"', deploy)


if __name__ == "__main__":
    unittest.main()
