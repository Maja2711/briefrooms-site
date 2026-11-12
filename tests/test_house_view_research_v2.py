"""House View v2: traceable bilingual synthesis and isolation regression tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scripts import house_view_research_v2 as v2
from scripts import publish_monthly_house_view as m
from tests.test_house_view_monthly import sources

TODAY = date(2026, 11, 8)


def write(root: Path, rel: str, doc: dict):
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return dest


def fake_models(root: Path, *, gse_probability: float = .68, stale_gse: bool = False):
    write(root, "data/gse/gse_v2_lab_public.json", {
        "generated_at": "2026-10-01T10:00:00+00:00" if stale_gse else "2026-11-08T10:00:00+00:00",
        "spx_long_view": {
            "asset": "SPX", "research_only": True, "decision_influence": False,
            "primary_horizon": {
                "asset": "SPX", "horizon_hours": 720, "freshness": "fresh",
                "forecast_at": "2026-11-08T09:00:00Z", "direction": "DOWN",
                "probability": gse_probability, "effective_cluster_n": 18,
            },
        },
    })
    write(root, "data/investments/fse_public.json", {
        "generated_at": "2026-11-08T11:00:00Z",
        "authority": {"trade_execution": False},
        "instruments": [{"instrument": "SPX", "regime": "TURBULENT",
                         "observed_at": "2026-11-07T21:00:00Z", "risk_score": .71,
                         "fractal_memory": {"available": False, "status": "ABSTAIN"}}],
    })
    write(root, "data/public/brace_spx_platform_public.json", {
        "generated_at": "2026-11-08T11:00:00Z",
        "frozen_track": {"single_champion_authorized": False,
                         "strict_gate_passed": False},
        "promotion_gate": {"status": "NOT_READY"},
    })
    write(root, "data/investments/wes_report.json", {"week_id": "2026-W45"})
    write(root, "data/investments/weekly/2026-W45.json", {
        "week_id": "2026-W45",
        "forecast_created_at": "2026-11-07T23:00:00+00:00",
        "instruments": [{"instrument_id": "sp500_futures", "data_quality": "passed",
                         "signals": {"ret20_pct": 4.8, "last_close": 7500}}],
    })


class HouseViewIntelligenceV2Tests(unittest.TestCase):
    def enrich(self, root: Path, **kw):
        fake_models(root, **kw)
        data = root / "data/investments"
        weekly = m.weekly_spx_market_observation(TODAY, data)
        self.assertIsNotNone(weekly)
        baseline = m.build_snapshot(sources(), TODAY, weekly)
        return v2.enrich_monthly_snapshot(baseline, TODAY, data, None)

    def test_full_bilingual_analysis_uses_engines_but_never_votes_or_trades(self):
        with tempfile.TemporaryDirectory() as td:
            doc = self.enrich(Path(td))
            self.assertEqual(doc["schema_version"], "2.0")
            self.assertEqual(doc["views"]["1M"]["bias"], "neutral")
            self.assertTrue(doc["governance"]["research_only"])
            self.assertFalse(doc["governance"]["trade_authority"])
            self.assertFalse(doc["governance"]["auto_promotion"])
            self.assertFalse(doc["governance"]["engine_writeback"])
            self.assertEqual({s["id"] for s in doc["research"]["sections"]}, {
                "thesis", "growth_and_inflation", "rates_and_energy",
                "earnings_and_valuation", "tactical_and_models", "disagreements",
            })
            for item in doc["research"]["sections"]:
                self.assertGreater(len(item["pl"]), 45)
                self.assertGreater(len(item["en"]), 45)
            engines = {x["id"]: x for x in doc["research"]["engine_crosscheck"]}
            self.assertEqual(engines["GSE"]["risk_flag"], "geopolitical_caution")
            self.assertEqual(engines["FSE"]["directional_memory"], "ABSTAIN")
            self.assertEqual(engines["FSE"]["risk_flag"], "structural_caution")
            self.assertFalse(engines["BRACE"]["champion_authorized"])
            self.assertEqual(engines["BELIEF"]["status"], "not_publicly_available")
            self.assertTrue(all(x["directional_vote"] is None for x in engines.values()))
            self.assertIn("gse_vs_positive_momentum", doc["research"]["engine_conflicts"])
            self.assertEqual(len(doc["research"]["scenarios"]), 3)
            self.assertTrue(all(x["probability"] is None for x in doc["research"]["scenarios"]))
            self.assertIsNone(doc["research"]["limitations"]["index_price_target"])
            self.assertEqual(doc["research"]["fundamentals"]["status"], "unavailable")

    def test_stale_gse_is_not_directional_or_a_risk_flag(self):
        with tempfile.TemporaryDirectory() as td:
            doc = self.enrich(Path(td), stale_gse=True)
            engines = {x["id"]: x for x in doc["research"]["engine_crosscheck"]}
            self.assertEqual(engines["GSE"]["status"], "unavailable")
            self.assertNotIn("gse_vs_positive_momentum", doc["research"]["engine_conflicts"])
            ev = {x["id"]: x for x in doc["research"]["evidence"]}
            self.assertEqual(ev["GSE"]["status"], "stale_or_future")
            self.assertEqual(len(ev["GSE"]["sha256"]), 64)

    def test_weak_gse_reading_not_promoted_to_direction(self):
        with tempfile.TemporaryDirectory() as td:
            doc = self.enrich(Path(td), gse_probability=.57)
            engines = {x["id"]: x for x in doc["research"]["engine_crosscheck"]}
            self.assertIsNone(engines["GSE"].get("risk_flag"))
            self.assertEqual(doc["research"]["engine_conflicts"], [])

    def test_optional_fundamentals_must_be_documented_and_fresh(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake_models(root)
            path = "data/investments/house_view_fundamentals.json"
            write(root, path, {"schema_version": "1.0", "generated_at": "2026-11-08T11:00:00Z",
                               "source_published_at": "2026-11-07T00:00:00Z",
                               "source_document_url": "https://insight.factset.com/real-report",
                               "forward_pe": 23.5, "forward_eps_growth_pct": 9.7})
            data = root / "data/investments"
            f, evidence = v2._fundamental_context(TODAY, data)
            self.assertEqual(f["forward_pe"], 23.5)
            self.assertEqual(evidence["status"], "fresh")
            write(root, path, {"schema_version": "1.0", "generated_at": "2026-11-08T11:00:00Z",
                               "source_published_at": "2026-11-07T00:00:00Z",
                               "source_document_url": "https://untrusted.example/report",
                               "forward_pe": 23.5, "forward_eps_growth_pct": 9.7})
            f, evidence = v2._fundamental_context(TODAY, data)
            self.assertEqual(f["status"], "unavailable")
            self.assertEqual(evidence["status"], "invalid_curated_source")

    def test_publication_commits_only_v2_snapshot_in_immutable_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake_models(root)
            data = root / "data/investments"
            untouched = write(root, "data/investments/trading_state_sentinel.json", {"keep": True})
            original = untouched.read_bytes()
            with patch.object(m, "fetch_series", side_effect=lambda key: sources()[key]):
                first = m.publish(TODAY, data)
            self.assertIn("published: 2026-11", first)
            current = data / "house_view_monthly_current.json"
            frozen = data / "house_view_monthly_archive/2026-11.json"
            self.assertEqual(current.read_bytes(), frozen.read_bytes())
            doc = json.loads(current.read_text(encoding="utf-8"))
            v2.validate_research(doc)
            with patch.object(m, "fetch_series", side_effect=AssertionError("should not fetch twice")):
                self.assertIn("skipped", m.publish(TODAY, data))
            self.assertEqual(current.read_bytes(), frozen.read_bytes())
            self.assertEqual(untouched.read_bytes(), original)

    def test_incomplete_macro_source_blocks_v2_before_writing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake_models(root)
            data = root / "data/investments"
            with patch.object(m, "fetch_series", side_effect=m.PublicationBlocked("missing macro")):
                with self.assertRaises(m.PublicationBlocked):
                    m.publish(TODAY, data)
            self.assertFalse((data / "house_view_monthly_current.json").exists())

    def test_previous_research_does_not_retroactively_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = self.enrich(root)
            previous = json.loads(json.dumps(first))
            previous["month"] = "2026-10"
            previous["inputs"]["brent_usd_bbl"] = 99
            next_doc = v2.enrich_monthly_snapshot(
                m.build_snapshot(sources(), TODAY, m.weekly_spx_market_observation(TODAY, root/"data/investments")),
                TODAY, root/"data/investments", previous
            )
            self.assertTrue(any(c["source_id"] == "DCOILBRENTEU" for c in
                                next_doc["research"]["change_in_observed_factors"]))
            self.assertEqual(previous["inputs"]["brent_usd_bbl"], 99)

    def test_all_four_views_offer_explicit_archive_and_v2_renderer(self):
        for path in ("pl/inwestycje/long-view.html", "en/investing/long-view.html",
                     "pl/inwestycje/spx-scenariusze-2026.html",
                     "en/investing/spx-scenarios-2026.html"):
            with self.subTest(path=path):
                html = (m.ROOT / path).read_text(encoding="utf-8")
                self.assertEqual(html.count("data-house-view-legacy"), 1)
                self.assertEqual(html.count("<details class=\"hvm-legacy\""), 1)
                self.assertIn("house-view-monthly.js?v=20261108-2", html)
        script = (m.ROOT / "scripts/house-view-monthly.js").read_text(encoding="utf-8")
        self.assertIn("renderV2", script)
        self.assertNotIn("innerHTML", script)


    def test_editorial_is_an_spx_market_thesis_not_engine_telemetry(self):
        with tempfile.TemporaryDirectory() as td:
            doc = self.enrich(Path(td))
            sections = {s["id"]: s for s in doc["research"]["sections"]}
            self.assertIn("S&P 500:", sections["thesis"]["pl"])
            self.assertIn("P/E S&P 500", sections["rates_and_energy"]["pl"])
            self.assertIn("Dla S&P 500", sections["growth_and_inflation"]["pl"])
            self.assertIn("nie zakładamy", sections["earnings_and_valuation"]["pl"])
            self.assertIn("S&P 500 futures wzrosły", sections["tactical_and_models"]["pl"])
            self.assertIn("S&P 500 futures gained", sections["tactical_and_models"]["en"])
            self.assertIn("geopolityczne", sections["tactical_and_models"]["pl"])
            self.assertNotIn("structural_caution", " ".join(s["pl"] for s in sections.values()))
            self.assertNotIn("ABSTAIN", " ".join(s["pl"] for s in sections.values()))
            self.assertNotIn("research_only", " ".join(s["en"] for s in sections.values()))
            self.assertTrue(all(s["source_ids"] for s in sections.values()))
            for value in doc["research"]["scenarios"]:
                self.assertIn("S&P 500", value["pl"])
                self.assertIn("S&P 500", value["en"])
                self.assertIsNone(value["probability"])

    def test_frozen_november_2026_facts_produce_defensive_human_readable_thesis(self):
        path = m.ROOT / "data/investments/house_view_monthly_archive/2026-11.json"
        frozen = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(frozen["inputs"]["spx_futures_20d_return_pct"], -5.82)
        old_raw = path.read_bytes()
        updated = v2._investment_editorial_sections(
            frozen["inputs"], frozen["views"],
            frozen["research"]["engine_crosscheck"],
            frozen["research"]["fundamentals"],
            frozen["research"]["engine_conflicts"])
        sections = {s["id"]: s for s in updated}
        self.assertIn("przeważa ryzyko korekty", sections["thesis"]["pl"])
        self.assertIn("straciły 5.82%", sections["tactical_and_models"]["pl"])
        self.assertIn("Rentowność US 10Y: 4.86%", sections["rates_and_energy"]["pl"])
        self.assertIn("nie podnosimy oceny 6M/12M", sections["earnings_and_valuation"]["pl"])
        self.assertIn("What would overturn the cautious 1M view?", sections["disagreements"]["en"])
        self.assertNotIn("structural_caution", " ".join(s["pl"] for s in updated))
        self.assertEqual(path.read_bytes(), old_raw, "never retroactively rewrite a frozen archive")

    def test_missing_fundamentals_has_explicit_investment_implication(self):
        with tempfile.TemporaryDirectory() as td:
            doc = self.enrich(Path(td))
            sections = {x["id"]: x for x in doc["research"]["sections"]}
            self.assertIn("wzrostu EPS", sections["earnings_and_valuation"]["pl"])
            self.assertIn("nie podnosimy oceny 6M/12M", sections["earnings_and_valuation"]["pl"])
            self.assertIsNone(doc["research"]["limitations"]["index_price_target"])


if __name__ == "__main__":
    unittest.main()
