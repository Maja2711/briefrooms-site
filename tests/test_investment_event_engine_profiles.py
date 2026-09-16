import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import investment_event_engine_profiles as profiles


def evt(*, event_id="evt-1", pressure=1.0, strength=0.9, confidence=0.95, title="President ordered military strike"):
    return {
        "event_id": event_id,
        "title": title,
        "published_at": "2026-09-16T10:00:00Z",
        "source": "Reuters",
        "source_ref": "https://example.test/event",
        "confidence": confidence,
        "action_type": "executed_action",
        "event_type": "escalation" if pressure > 0 else "deescalation",
        "scenario_tags": ["general_geopolitics"],
        "event_strength": strength,
        "pressure": pressure,
    }


class EngineProfileTests(unittest.TestCase):
    def test_same_macro_event_has_weekly_daily_stock_sensitivity_order(self):
        target = {"target_id": "stock:US:MSFT", "symbol": "MSFT", "market": "US", "sector": "Software", "name": "Microsoft Corporation"}
        events = [evt()]
        weekly = profiles.score_target(target, events, engine_profile=profiles.WEEKLY)
        daily = profiles.score_target(target, events, engine_profile=profiles.DAILY)
        stock = profiles.score_target(target, events, engine_profile=profiles.STOCK_TRADING)
        self.assertGreater(abs(weekly["normalized_impact"]), abs(daily["normalized_impact"]))
        self.assertGreater(abs(daily["normalized_impact"]), abs(stock["normalized_impact"]))
        self.assertEqual(1.0, weekly["dominant_engine_weight"])
        self.assertEqual(0.75, daily["dominant_engine_weight"])
        self.assertEqual(0.30, stock["dominant_engine_weight"])

    def test_weekly_preserves_raw_legacy_event_impact(self):
        target = {"target_id": "sp500_futures", "symbol": "ES=F", "market": "MULTI_ASSET", "sector": "equity index"}
        score = profiles.score_target(target, [evt(strength=0.8)], engine_profile=profiles.WEEKLY)
        self.assertAlmostEqual(score["raw_normalized_impact"], score["normalized_impact"], places=4)

    def test_stock_company_fundamental_event_restores_full_weight(self):
        target = {
            "target_id": "stock:US:NVDA",
            "symbol": "NVDA",
            "market": "US",
            "sector": "Semiconductors",
            "name": "NVIDIA Corporation",
        }
        event = evt(title="NVIDIA faces export ban and regulator action", strength=0.95)
        event["scenario_tags"] = ["trade_sanctions", "china_taiwan"]
        score = profiles.score_target(target, [event], engine_profile=profiles.STOCK_TRADING)
        self.assertEqual("company_fundamental", score["dominant_event_scope"])
        self.assertEqual(1.0, score["dominant_engine_weight"])
        self.assertAlmostEqual(score["raw_normalized_impact"], score["normalized_impact"], places=4)

    def test_stock_sector_direct_event_uses_intermediate_weight(self):
        target = {"target_id": "stock:US:UAL", "symbol": "UAL", "market": "US", "sector": "Airlines", "name": "United Airlines Holdings"}
        event = evt(title="President ordered military strike in Middle East")
        event["scenario_tags"] = ["middle_east"]
        score = profiles.score_target(target, [event], engine_profile=profiles.STOCK_TRADING)
        self.assertEqual("sector_direct", score["dominant_event_scope"])
        self.assertEqual(0.60, score["dominant_engine_weight"])

    def test_short_position_treats_positive_asset_event_as_adverse(self):
        events = [
            evt(event_id="a", pressure=-1.0, strength=0.95, title="Prime minister signed ceasefire agreement"),
            evt(event_id="b", pressure=-1.0, strength=0.90, title="Government announced peace deal"),
        ]
        target = {"target_id": "sp500_futures", "symbol": "ES=F", "market": "MULTI_ASSET", "sector": "equity index"}
        score = profiles.score_target(target, events, engine_profile=profiles.WEEKLY)
        self.assertGreater(score["normalized_impact"], 0.0)
        self.assertEqual("CLOSE", profiles.directional_decision(score, "SHORT"))
        self.assertNotEqual("CLOSE", profiles.directional_decision(score, "LONG"))

    def test_short_entry_can_be_blocked_by_positive_event(self):
        events = [
            evt(event_id="a", pressure=-1.0, strength=0.98, title="Prime minister signed ceasefire agreement"),
            evt(event_id="b", pressure=-1.0, strength=0.95, title="Government announced peace agreement"),
            evt(event_id="c", pressure=-1.0, strength=0.90, title="President approved truce agreement"),
        ]
        target = {"target_id": "eurusd", "symbol": "EURUSD=X", "market": "DAILY_FX", "sector": "fx"}
        score = profiles.score_target(target, events, engine_profile=profiles.DAILY)
        self.assertTrue(profiles.entry_blocked_for_direction(score, "SHORT"))
        self.assertFalse(profiles.entry_blocked_for_direction(score, "LONG"))


if __name__ == "__main__":
    unittest.main()
