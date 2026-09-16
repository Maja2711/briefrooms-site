import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import investment_corporate_event_intelligence as corporate
import investment_event_engine_profiles as profiles


def corporate_event(
    *,
    entity="nvidia",
    symbols=None,
    themes=None,
    kind="guidance_cut",
    direct=-1.0,
    sector=-0.45,
    strength=0.95,
    confidence=0.95,
    domain="technology",
    index_impact=-0.28,
    market_index_relevant=True,
):
    return {
        "event_id": f"corp-{entity}",
        "event_domain": domain,
        "event_kind": kind,
        "entity_key": entity,
        "entity_name": entity.title(),
        "entity_symbols": ["NVDA"] if symbols is None else symbols,
        "entity_themes": ["semiconductor", "ai_infrastructure", "ai_market"] if themes is None else themes,
        "title": f"{entity.title()} cuts guidance after weak demand",
        "published_at": "2026-09-16T12:00:00Z",
        "source": "Reuters",
        "source_ref": "https://example.test/corporate",
        "confidence": confidence,
        "action_type": "corporate_material_update",
        "event_type": kind,
        "scenario_tags": [f"entity:{entity}", "theme:semiconductor", "ai_market"],
        "event_strength": strength,
        "pressure": 0.0,
        "direct_impact": direct,
        "sector_impact": sector,
        "index_impact": index_impact,
        "market_index_relevant": market_index_relevant,
    }


class CorporateEventRadarTests(unittest.TestCase):
    def test_tracked_executive_and_company_are_detected(self):
        entity = corporate.match_entity("Jensen Huang says NVIDIA sees strong data center demand")
        self.assertIsNotNone(entity)
        self.assertEqual("nvidia", entity["key"])
        authority, role, actor = corporate.actor_authority(
            "Jensen Huang says NVIDIA sees strong data center demand", entity
        )
        self.assertEqual(0.97, authority)
        self.assertEqual("tracked_executive_or_founder", role)
        self.assertEqual("Jensen Huang", actor)

    def test_guidance_raise_is_positive_direct_company_signal(self):
        signal = corporate.classify_signal("NVIDIA raises guidance after earnings beat")
        self.assertEqual("guidance_raise", signal["event_kind"])
        self.assertEqual(1.0, signal["direct_impact"])
        self.assertGreaterEqual(signal["severity"], 0.9)

    def test_guidance_cut_is_negative_direct_company_signal(self):
        signal = corporate.classify_signal("NVIDIA cuts guidance as demand weakens")
        self.assertEqual("guidance_cut", signal["event_kind"])
        self.assertEqual(-1.0, signal["direct_impact"])
        self.assertGreaterEqual(signal["severity"], 0.9)

    def test_product_statement_can_be_observed_without_forcing_direction(self):
        signal = corporate.classify_signal("NVIDIA unveils new GPU roadmap")
        self.assertEqual("product_roadmap", signal["event_kind"])
        self.assertEqual(0.0, signal["direct_impact"])

    def test_negative_nvidia_fundamental_hits_nvda_not_unrelated_stock(self):
        row = corporate_event()
        nvda = {"target_id": "stock:US:NVDA", "symbol": "NVDA", "market": "US", "sector": "Semiconductors", "name": "NVIDIA Corporation"}
        jpm = {"target_id": "stock:US:JPM", "symbol": "JPM", "market": "US", "sector": "Banks", "name": "JPMorgan Chase"}
        nvda_score = profiles.score_target(nvda, [row], engine_profile=profiles.STOCK_TRADING)
        jpm_score = profiles.score_target(jpm, [row], engine_profile=profiles.STOCK_TRADING)
        self.assertEqual("company_fundamental", nvda_score["dominant_event_scope"])
        self.assertEqual(1.0, nvda_score["dominant_engine_weight"])
        self.assertLess(nvda_score["normalized_impact"], 0.0)
        self.assertEqual(0.0, jpm_score["normalized_impact"])
        self.assertIsNone(jpm_score["dominant_event_scope"])

    def test_ai_sector_statement_has_bounded_readthrough_to_semiconductors(self):
        row = corporate_event(
            entity="openai",
            symbols=[],
            themes=["ai_market", "ai_infrastructure"],
            kind="strategic_statement",
            direct=0.0,
            sector=1.0,
            strength=0.80,
            domain="technology",
            index_impact=0.0,
            market_index_relevant=False,
        )
        row["title"] = "OpenAI executive says AI infrastructure demand is accelerating"
        row["scenario_tags"] = ["entity:openai", "theme:ai_market", "theme:ai_infrastructure", "ai_market"]
        nvda = {"target_id": "stock:US:NVDA", "symbol": "NVDA", "market": "US", "sector": "Semiconductors", "name": "NVIDIA Corporation"}
        score = profiles.score_target(nvda, [row], engine_profile=profiles.STOCK_TRADING)
        self.assertEqual("sector_direct", score["dominant_event_scope"])
        self.assertEqual(0.60, score["dominant_engine_weight"])
        self.assertGreater(score["normalized_impact"], 0.0)

    def test_crypto_ecosystem_signal_can_reach_btc_but_not_eurusd(self):
        row = corporate_event(
            entity="coinbase",
            symbols=["COIN"],
            themes=["crypto_market", "bitcoin"],
            kind="regulatory_approval",
            direct=1.0,
            sector=1.0,
            strength=0.85,
            domain="crypto",
            index_impact=0.0,
            market_index_relevant=False,
        )
        row["scenario_tags"] = ["entity:coinbase", "theme:crypto_market", "crypto_market", "bitcoin"]
        btc = {"target_id": "btcusd", "symbol": "BTC-USD", "market": "MULTI_ASSET", "sector": "crypto"}
        eur = {"target_id": "eurusd", "symbol": "EURUSD=X", "market": "MULTI_ASSET", "sector": "fx"}
        btc_score = profiles.score_target(btc, [row], engine_profile=profiles.WEEKLY)
        eur_score = profiles.score_target(eur, [row], engine_profile=profiles.WEEKLY)
        self.assertGreater(btc_score["normalized_impact"], 0.0)
        self.assertEqual(0.0, eur_score["normalized_impact"])

    def test_megacap_company_event_has_only_bounded_index_readthrough(self):
        row = corporate_event(strength=1.0, direct=-1.0, index_impact=-0.28)
        spx = {"target_id": "sp500_futures", "symbol": "ES=F", "market": "MULTI_ASSET", "sector": "equity index"}
        score = profiles.score_target(spx, [row], engine_profile=profiles.WEEKLY)
        self.assertAlmostEqual(-0.28, score["normalized_impact"], places=4)
        self.assertGreater(abs(row["direct_impact"]), abs(score["normalized_impact"]))


if __name__ == "__main__":
    unittest.main()
