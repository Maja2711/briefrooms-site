"""Monthly House View: deterministic signal, lineage, stale-data rejection and idempotency."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scripts import publish_monthly_house_view as m


TODAY = date(2026, 11, 8)


def sources():
    return {
        "GDPC1": [(date(2026, 4, 1), 100.0), (date(2026, 7, 1), 100.6)],
        "PCEPI": [(date(2025, 9, 1), 100.0), (date(2026, 9, 1), 103.4)],
        "PCEPILFE": [(date(2025, 9, 1), 100.0), (date(2026, 9, 1), 103.1)],
        "DGS10": [(date(2026, 11, 6), 4.8)],
        "DCOILBRENTEU": [(date(2026, 11, 6), 104.2)],
    }


class MonthlyHouseViewTests(unittest.TestCase):
    def test_snapshot_is_non_trading_and_bilingual_numeric_facts_are_frozen(self):
        out = m.build_snapshot(sources(), TODAY, {"week_id": "2026-W45", "ret20_pct": -3.2})
        self.assertEqual(out["month"], "2026-11")
        self.assertEqual(out["views"]["1M"]["bias"], "defensive")
        self.assertEqual(out["views"]["6M"]["bias"], "neutral")
        self.assertEqual(out["views"]["12M"]["bias"], "neutral")
        self.assertEqual(out["views"]["1M"]["conviction"], 3)
        self.assertEqual(out["inputs"]["core_pce_yoy_pct"], 3.1)
        self.assertFalse(out["governance"]["trade_authority"])
        self.assertFalse(out["governance"]["auto_promotion"])
        self.assertIsNone(out["governance"]["forecast_probability"])
        self.assertEqual(len(out["indicators"]), 5)

    def test_unavailable_spx_market_observation_is_not_neutral_signal(self):
        out = m.build_snapshot(sources(), TODAY, None)
        self.assertEqual(out["views"]["1M"]["bias"], "unassessed")
        self.assertIsNone(out["views"]["1M"]["risk_balance_score"])

    def test_outdated_critical_source_blocks_publication(self):
        s = sources()
        s["PCEPI"] = [(date(2025, 6, 1), 100.0), (date(2026, 6, 1), 103.4)]
        with self.assertRaises(m.PublicationBlocked):
            m.build_snapshot(s, TODAY, None)

    def test_missing_12_month_baseline_is_not_fabricated(self):
        s = sources()
        s["PCEPILFE"] = [(date(2026, 9, 1), 103.1)]
        with self.assertRaises(m.PublicationBlocked):
            m.build_snapshot(s, TODAY, None)

    def test_missing_source_leaves_no_new_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(m, "fetch_series", side_effect=m.PublicationBlocked("offline")):
                with self.assertRaises(m.PublicationBlocked):
                    m.publish(TODAY, root)
            self.assertFalse((root / "house_view_monthly_current.json").exists())
            self.assertFalse((root / "house_view_monthly_archive" / "2026-11.json").exists())

    def test_second_run_is_immutable_and_heals_current_pointer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "house_view_monthly_archive").mkdir()
            archived = m.build_snapshot(sources(), TODAY, None)
            frozen = json.dumps(archived) + "\n"
            (root / "house_view_monthly_archive" / "2026-11.json").write_text(frozen, encoding="utf-8")
            with patch.object(m, "fetch_series", side_effect=AssertionError("no network needed")):
                self.assertIn("skipped", m.publish(TODAY, root))
            self.assertEqual((root / "house_view_monthly_current.json").read_text(encoding="utf-8"), frozen)

    def test_mismatched_pce_reporting_months_block_publication(self):
        s = sources()
        s["PCEPI"] = [(date(2025, 8, 1), 100.0), (date(2026, 8, 1), 103.4)]
        with self.assertRaises(m.PublicationBlocked):
            m.build_snapshot(s, TODAY, None)

    def test_weekly_freeze_is_context_even_before_forecast_week_ends(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "weekly").mkdir()
            (base / "wes_report.json").write_text(json.dumps({"week_id": "2026-W45"}))
            payload = {"week_id":"2026-W45", "forecast_created_at":"2026-11-07T01:00:00+00:00",
                "forecast_for_week_end":"2026-11-13",
                "instruments":[{"instrument_id":"sp500_futures","data_quality":"passed",
                                "signals":{"ret20_pct":-3.5,"last_close":7600.0}}]}
            (base / "weekly" / "2026-W45.json").write_text(json.dumps(payload))
            observed = m.weekly_spx_market_observation(TODAY, base)
            self.assertEqual(observed["ret20_pct"], -3.5)
            self.assertIsNone(observed["price_tick_at"])
            self.assertIn("forecast_frozen_at", observed)

    def test_all_four_pages_have_bilingual_monthly_reader(self):
        for path in ("pl/inwestycje/long-view.html", "en/investing/long-view.html",
                     "pl/inwestycje/spx-scenariusze-2026.html", "en/investing/spx-scenarios-2026.html"):
            with self.subTest(path=path):
                text = (m.ROOT / path).read_text(encoding="utf-8")
                self.assertIn("data-house-view-monthly", text)
                self.assertIn("/scripts/house-view-monthly.js", text)
                self.assertIn("/assets/house-view-monthly.css", text)

    def test_future_series_observation_is_blocked(self):
        s = sources()
        s["DGS10"] = [(date(2026, 11, 9), 4.9)]
        with self.assertRaises(m.PublicationBlocked):
            m.build_snapshot(s, TODAY, None)


if __name__ == "__main__":
    unittest.main()
