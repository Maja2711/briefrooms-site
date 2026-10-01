import json
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class StockTradingRMultipleTest(unittest.TestCase):
    def test_ntra_uses_initial_risk(self):
        data=json.loads((ROOT/"data/investments/stock_trading_portfolio.json").read_text(encoding="utf-8"))
        ntra=next(p for p in data["markets"]["US"]["closed_positions"] if (p.get("ticker") or p.get("symbol"))=="NTRA" and p.get("closed_at")=="2026-10-01T14:38:41-04:00")
        expected=(float(ntra["exit_price"])-float(ntra["entry"]))/float(ntra["initial_risk_amount"])
        self.assertTrue(math.isfinite(expected))
        self.assertAlmostEqual(float(ntra["r_multiple"]), expected, places=4)
        self.assertLess(abs(float(ntra["r_multiple"])), 100)

    def test_frontend_recomputes_realized_r(self):
        text=(ROOT/"scripts/stock-trading-public.js").read_text(encoding="utf-8")
        self.assertIn("function realizedR(p)", text)
        self.assertIn("initial_risk_amount", text)
        self.assertIn("rows.map(realizedR)", text)
        self.assertIn("Math.abs(stored) <= 100", text)

    def test_closure_uses_initial_risk_amount(self):
        text=(ROOT/"scripts/stock_trading_portfolio.py").read_text(encoding="utf-8")
        self.assertIn('position.get("initial_risk_amount")', text)
        self.assertNotIn('initial_risk = max(entry - float(position.get("stop") or entry), 1e-12)', text)

if __name__=="__main__":
    unittest.main()
