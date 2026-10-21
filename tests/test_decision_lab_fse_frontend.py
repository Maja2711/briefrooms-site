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
            self.assertIn("https://briefrooms-fse-fast.szczerbinski0577.workers.dev/fse-fast", text, rel)
            self.assertIn("/data/investments/fse_intraday_public.json", text, rel)
            self.assertLess(text.index("briefrooms-fse-fast.szczerbinski0577.workers.dev/fse-fast"), text.index("/data/investments/fse_intraday_public.json"), rel)
            self.assertIn("/data/investments/hypothesis_shadow_engine_v2_public.json", text, rel)
            self.assertIn("FSE-PHASE", text, rel)
            self.assertIn("Cross-Scale Alignment", text, rel)
            self.assertIn("P Calibration Challenger", text, rel)
            self.assertIn("Deep Fractal Memory v2", text, rel)
            self.assertIn("v2 Calibration Challenger", text, rel)
            self.assertIn("directional_performance", text, rel)
            self.assertIn("fseModelCell(", text, rel)
            self.assertIn("fseSignal(", text, rel)
            self.assertIn("n>=.55?'long':n<=.45?'short':'neutral'", text, rel)
            self.assertIn("delta_brier_vs_base", text, rel)
            self.assertIn("comparisonRows", text, rel)
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
            self.assertIn("fseFastRowFresh", text, rel)
            self.assertIn("FSE-FAST-2.0", text, rel)
            self.assertIn("setInterval", text, rel)
            self.assertIn("experiment?.source_engine==='FSE'", text, rel)
            self.assertIn("fseCurrentExperiments(hse.experiments,allMeasurements)", text, rel)
            self.assertIn("Brier", text, rel)
            self.assertIn("fse();shadows()", text, rel)

    def test_autonomous_discovery_is_visible_and_read_only(self):
        for rel in ("scripts/central-lab.js", "scripts/central-lab-runtime-en.js"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("function fseDiscoveryPanel(", text, rel)
            self.assertIn("/data/investments/fse_discovery_public.json", text, rel)
            self.assertIn("fseDiscoveryPanel(discovery)", text, rel)
        # Both locales expose the same evidence and complete expandable catalogue.
        pl = (ROOT / "scripts/central-lab.js").read_text(encoding="utf-8")
        self.assertIn("new Map((Array.isArray(d.families)", pl)
        self.assertIn("FSE analizuje obecnie", pl)
        self.assertIn("fse-discovery-cardtop", pl)
        self.assertIn("fse-discovery-catalogue", pl)
        self.assertNotIn("families.slice(0,12)", pl)
        # English LAB is unchanged and continues to show its detailed research cards.
        en = (ROOT / "scripts/central-lab-runtime-en.js").read_text(encoding="utf-8")
        for field in ("f.evidence_status", "f.applications", "f.name", "f.description"):
            self.assertIn(field, en)

    def test_english_discovery_renders_localized_fields(self):
        js = (ROOT / "scripts/central-lab-runtime-en.js").read_text(encoding="utf-8")
        self.assertIn("function fseDiscoveryEnglishCopy(f)", js)
        self.assertIn("f.name_en", js)
        self.assertIn("f.description_en", js)
        self.assertIn("f.applications_en", js)
        self.assertIn("esc(english.name)", js)
        self.assertIn("esc(english.description)", js)
        self.assertIn("const apps=f.application_status", js)
        self.assertIn("apps.map", js)
        self.assertIn("Application undetermined.", js)
        self.assertIn("fseDiscoveryShape(f)", js)

    def test_unmatched_discovery_observations_remain_in_shadow(self):
        # Both readers exclude novel or retired matches from the visible banner.
        pl = (ROOT / "scripts/central-lab.js").read_text(encoding="utf-8")
        self.assertIn("fse-discovery-matches", pl)
        self.assertIn("x.status==='FROZEN_MATCH'", pl)
        self.assertIn("activeIds.has(x.family_id)", pl)
        en = (ROOT / "scripts/central-lab-runtime-en.js").read_text(encoding="utf-8")
        self.assertIn("x.status==='FROZEN_MATCH'", en)
        self.assertIn("activeIds.has(x.family_id)", en)
        self.assertNotIn("x.family_id||'New/unmatched shape'", en)
        engine = (ROOT / "scripts/fse_autonomous_discovery.py").read_text(encoding="utf-8")
        self.assertIn('"FROZEN_NOVEL_UNMATCHED"', engine)
        self.assertIn('append_evidence(state, "snapshots", payload)', engine)

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
        self.assertIn(".fse-comparison-table", css)
        self.assertIn(".fse-signal", css)
        self.assertIn(".fse-validation-table", css)
        self.assertIn(".fse-trend-grid", css)
        self.assertIn(".fse-trend-card", css)
        self.assertIn(".fse-tf-ribbon", css)
        self.assertIn(".fse-tf-direction", css)
        self.assertIn(".fse-fast-state", css)
        self.assertIn(".fse-fast-meta", css)
        self.assertIn(".fse-discovery-grid", css)
        self.assertIn(".fse-discovery-card", css)
        self.assertIn(".fse-discovery-shape", css)
        self.assertIn(".fse-discovery-no-use", css)
        # Dark LAB panels must not inherit a white surface with light text.
        self.assertIn("background:linear-gradient(180deg,#12273b,#0e1d2e)", css)
        self.assertIn("color:#eaf3ff", css)
        self.assertIn(".fse-discovery .fse-discovery-card h4", css)
        self.assertIn("color:#dfecfa", css)
        self.assertIn(".fse-discovery .fse-discovery-card summary", css)
        self.assertIn("summary:focus-visible", css)
        self.assertNotIn(".fse-discovery-card{padding:15px", css)

        self.assertIn(".fse-tf-direction em", css)


    def test_asd_critical_contrast_is_inline_in_both_locales(self):
        # External CSS can be cached at the CDN; HTML critical rules protect
        # dynamically rendered dark-LAB cards without modifying other panels.
        for rel in ("pl/inwestycje/decision-lab.html", "en/investing/decision-lab.html"):
            html = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn('id="fse-asd-critical-contrast"', html, rel)
            self.assertEqual(html.count('id="fse-asd-critical-contrast"'), 1, rel)
            self.assertIn('#central-fse-lab .fse-discovery .fse-discovery-card{', html, rel)
            self.assertIn('background:#102237 !important', html, rel)
            self.assertIn('background-image:linear-gradient(180deg,#142d43 0%,#0d1c2e 100%) !important', html, rel)
            self.assertIn('color:#ffffff !important', html, rel)
            self.assertIn('color:#dfecfa !important', html, rel)
            self.assertIn('.fse-discovery-metrics span{', html, rel)
            self.assertIn('color:#9fdbff !important', html, rel)
            self.assertLess(html.index('id="fse-asd-critical-contrast"'), html.index('</head>'), rel)
            self.assertNotIn('style="', html, rel)

    def test_fse_trend_assets_are_cache_busted(self):
        pl = (ROOT / "pl/inwestycje/decision-lab.html").read_text(encoding="utf-8")
        en = (ROOT / "en/investing/decision-lab.html").read_text(encoding="utf-8")
        self.assertIn("/assets/decision-lab.css?v=20261011-asd4", pl)
        self.assertIn("/assets/decision-lab.css?v=20261011-asd4", en)
        self.assertIn("/scripts/central-lab.js?v=20261021-fse-search3", pl)
        self.assertIn("/scripts/central-lab-runtime-en.js?v=20261021-fse-search3", en)


if __name__ == "__main__":
    unittest.main()
