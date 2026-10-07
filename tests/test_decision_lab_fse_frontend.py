import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DecisionLabFSEFrontendTests(unittest.TestCase):
    def test_pl_and_en_have_fse_tab_and_panel(self):
        for rel in ("pl/inwestycje/decision-lab.html", "en/investing/decision-lab.html"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn('data-tab="fse"', text, rel)
            self.assertIn('id="tab-fse"', text, rel)
            self.assertIn('id="central-fse-lab"', text, rel)
            self.assertIn("Fractal Structure Engine", text, rel)

    def test_fse_frontend_reads_canonical_shadow_sources(self):
        for rel in ("scripts/central-lab.js", "scripts/central-lab-runtime-en.js"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("async function fse()", text, rel)
            self.assertIn("/data/investments/fse_public.json", text, rel)
            self.assertIn("/data/investments/fse_v2_public.json", text, rel)
            self.assertIn("https://briefrooms-fse-fast.szczerbinski0577.workers.dev/fse-fast", text, rel)\n            self.assertIn("/data/investments/fse_intraday_public.json", text, rel)\n            self.assertLess(text.index("briefrooms-fse-fast.szczerbinski0577.workers.dev/fse-fast"), text.index("/data/investments/fse_intraday_public.json"), rel)
            self.assertIn("/data/investments/hypothesis_shadow_engine_v2_public.json", text, rel)
            self.assertIn("FSE-PHASE", text, rel)
            self.assertIn("Cross-Scale Alignment", text, rel)
            self.assertIn("P Calibration Challenger", text, rel)
            self.assertIn("Fractal Trend", text, rel)
            self.assertIn("FSE_TREND_TFS", text, rel)
            self.assertIn("const FSE_TREND_TFS=['5m','15m','1h','4h','1d','1w']", text, rel)
            self.assertIn("fseTrendScore(map,['5m','15m'])", text, rel)
            self.assertIn("return ['5m','15m','1h','4h','1d','1w'].map(tf=>", text, rel)
            self.assertNotIn("const FSE_TREND_TFS=['1m'", text, rel)
            self.assertNotIn("fseTrendScore(map,['1m','5m','15m'])", text, rel)
            self.assertNotIn("return ['1m','5m','15m','1h','4h','1d','1w'].map(tf=>", text, rel)
            self.assertIn("fseFractalTrendCard", text, rel)
            self.assertIn("Lower TF", text, rel)
            self.assertIn("Higher TF", text, rel)
            self.assertIn("direction", text, rel)
            self.assertIn("fm.cadence_minutes||5", text, rel)
            self.assertNotIn("FAST 5M", text, rel)
            self.assertIn("function fseBarClock", text, rel)
            self.assertIn("row?.observed_at", text, rel)
            self.assertIn("stale_after_minutes", text, rel)
            self.assertIn("setInterval", text, rel)
            self.assertIn("x.source_engine==='FSE'", text, rel)
            self.assertIn("Brier", text, rel)
            self.assertIn("fse();shadows()", text, rel)

    def test_fse_ui_remains_read_only(self):
        for rel in ("scripts/central-lab.js", "scripts/central-lab-runtime-en.js"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            fse_block = text.split("async function fse(){", 1)[1].split("async function shadows(){", 1)[0]
            self.assertNotIn("fetch(", fse_block.replace("get(", ""), rel)
            self.assertNotIn("POST", fse_block, rel)
            self.assertNotIn("PUT", fse_block, rel)
            self.assertNotIn("PATCH", fse_block, rel)
            self.assertIn("PRODUCTION OFF", fse_block, rel)

    def test_fse_styles_are_present(self):
        css = (ROOT / "assets/decision-lab.css").read_text(encoding="utf-8")
        self.assertIn(".lab-glass-tabs .tab-fse", css)
        self.assertIn(".fse-memory-grid", css)
        self.assertIn(".fse-validation-table", css)
        self.assertIn(".fse-trend-grid", css)
        self.assertIn(".fse-trend-card", css)
        self.assertIn(".fse-tf-ribbon", css)
        self.assertIn(".fse-tf-direction", css)
        self.assertIn(".fse-fast-state", css)
        self.assertIn(".fse-fast-meta", css)
        self.assertIn(".fse-tf-direction em", css)


    def test_fse_trend_assets_are_cache_busted(self):
        pl = (ROOT / "pl/inwestycje/decision-lab.html").read_text(encoding="utf-8")
        en = (ROOT / "en/investing/decision-lab.html").read_text(encoding="utf-8")
        self.assertIn("/assets/decision-lab.css?v=20261007-1", pl)
        self.assertIn("/assets/decision-lab.css?v=20261007-1", en)
        self.assertIn("/scripts/central-lab.js?v=20261007-3", pl)
        self.assertIn("/scripts/central-lab-runtime-en.js?v=20261007-3", en)


if __name__ == "__main__":
    unittest.main()
