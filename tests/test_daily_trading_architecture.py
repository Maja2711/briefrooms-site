from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.belief_market_data_adapter import Bar, MarketSnapshot
from scripts.daily_engine_contract import DailyEngineOutput
from scripts.daily_engine_adapters import normalize_daily_stock_payload
from scripts.daily_eurusd_spot import EURUSD, TLT, UUP, build_output


def bars(start: float, drift: float, n: int = 90) -> list[Bar]:
    t0 = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)
    out = []
    price = start
    for i in range(n):
        price *= 1.0 + drift
        out.append(Bar(
            timestamp=t0 + timedelta(minutes=30 * i),
            open=price * 0.9998,
            high=price * 1.0007,
            low=price * 0.9993,
            close=price,
            volume=1000,
        ))
    return out


class DailyTradingArchitectureTests(unittest.TestCase):
    def test_contract_rejects_invalid_long_geometry(self):
        with self.assertRaises(ValueError):
            DailyEngineOutput(
                instrument="X", timestamp="2026-08-20T00:00:00Z", direction="LONG",
                score=70, confidence=.5, entry=10, stop=11, target=12,
                horizon="1d", engine_version="test"
            ).validate()

    def test_existing_daily_stock_payloads_normalize_without_changing_source_schema(self):
        gpw = {
            "schema_version": "gpw-daily-pick-v1",
            "policy_version": "gpw-test",
            "generated_at": "2026-08-20T09:37:13+02:00",
            "decision": "TRANSAKCJA",
            "methodology": {"horizon": "1-2 sesje GPW", "daily_stock_core": {"core": "daily-stock-core-v1"}},
            "selection": {"symbol": "PKO", "score": 79.0, "reference_price": 82.1, "stop": 80.0, "target": 85.88},
        }
        out = normalize_daily_stock_payload(gpw, "GPW").to_dict()
        self.assertEqual(out["schema_version"], "daily-engine-output-v1")
        self.assertEqual(out["instrument"], "PKO")
        self.assertEqual(out["direction"], "LONG")
        self.assertEqual(out["decision_mode"], "WITHOUT")
        self.assertTrue(out["metadata"]["source_hard_gates_preserved"])
        self.assertEqual(out["metadata"]["confidence_semantics"], "decision_strength_not_calibrated_probability")

    def test_eurusd_engine_emits_shared_without_contract(self):
        snapshot = MarketSnapshot({
            EURUSD: bars(1.16, 0.00016),
            UUP: bars(27.0, -0.00010),
            TLT: bars(90.0, 0.00012),
        })
        out = build_output(snapshot)
        payload = out.to_dict()
        self.assertEqual(payload["schema_version"], "daily-engine-output-v1")
        self.assertEqual(payload["instrument"], "EUR/USD")
        self.assertEqual(payload["decision_mode"], "WITHOUT")
        self.assertEqual(payload["status"], "SHADOW")
        self.assertFalse(payload["metadata"]["belief"]["decision_influence"])
        self.assertIn(payload["direction"], {"LONG", "SHORT", "FLAT"})

    def test_legacy_portfolio_daily_widget_is_removed_by_compatibility_shim(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts/gpw-daily-pick-public.js").read_text(encoding="utf-8")
        self.assertIn('/pl/inwestycje/portfel-10k.html', script)
        self.assertIn('legacyGrid || legacyRoot', script)
        self.assertIn('daily-stock-markets-public.js', script)

    def test_investments_information_architecture(self):
        root = Path(__file__).resolve().parents[1]
        landing = (root / "pl/inwestycje.html").read_text(encoding="utf-8")
        daily = (root / "pl/inwestycje/daily-trading.html").read_text(encoding="utf-8")
        weekly = (root / "pl/inwestycje/pozycje-tygodniowe.html").read_text(encoding="utf-8")
        long_view = (root / "pl/inwestycje/long-view.html").read_text(encoding="utf-8")
        self.assertIn("Daily Trading", landing)
        self.assertIn("Portfel 10K", landing)
        self.assertIn("Long View", landing)
        self.assertNotIn("<h2>Pozycje tygodniowe</h2>", landing)
        self.assertIn("Daily Trading</a>", daily)
        self.assertIn("Weekly Positions</a>", daily)
        self.assertIn('id="gpw-daily-pick-root"', daily)
        self.assertIn('id="eurusd-daily-root"', daily)
        self.assertIn("Daily Trading</a>", weekly)
        self.assertIn("Weekly Positions</a>", weekly)
        self.assertIn("S&amp;P 500", long_view)


if __name__ == "__main__":
    unittest.main()
