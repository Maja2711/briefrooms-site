import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DecisionLabLiveRefreshTests(unittest.TestCase):
    def test_pl_and_en_auto_refresh_forecast_projection(self):
        for rel in ("scripts/decision-lab.js", "scripts/decision-lab-runtime-en.js"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn('fetch("/data/investments/decision_lab_public.json?v="+stamp,{cache:"no-store"})', text, rel)
            self.assertIn("const LIVE_REFRESH_MS=60000;", text, rel)
            self.assertIn('document.visibilityState==="visible"', text, rel)
            self.assertIn('document.addEventListener("visibilitychange"', text, rel)
            self.assertIn("let HAS_LOADED=false;", text, rel)

    def test_settlement_calendar_uses_frozen_dependencies(self):
        for rel in ("scripts/decision-lab.js", "scripts/decision-lab-runtime-en.js"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("function settlementDependencies(x)", text, rel)
            self.assertIn('"HYG","LQD","TLT","UUP"', text, rel)
            self.assertIn('deps.every(symbol=>symbol==="BTC-USD")', text, rel)
            self.assertIn('deps.every(symbol=>symbol==="EURUSD=X")', text, rel)

    def test_open_forecast_after_target_is_shown_as_waiting_for_t1(self):
        pl = (ROOT / "scripts/decision-lab.js").read_text(encoding="utf-8")
        en = (ROOT / "scripts/decision-lab-runtime-en.js").read_text(encoding="utf-8")
        self.assertIn("OCZEKUJE T1", pl)
        self.assertIn("AWAITING T1", en)
        self.assertIn("status(x)", pl)
        self.assertIn("status(x)", en)


if __name__ == "__main__":
    unittest.main()
