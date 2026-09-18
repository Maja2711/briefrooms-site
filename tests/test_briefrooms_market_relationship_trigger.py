from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts import briefrooms_market_relationship_trigger as trigger
    from scripts import stock_trading_v2_contracts as contracts
except ModuleNotFoundError:
    import briefrooms_market_relationship_trigger as trigger
    import stock_trading_v2_contracts as contracts


UTC = timezone.utc


def candidate(
    symbol: str,
    rank: int,
    score: float,
    *,
    r1: float,
    r5: float,
    r20: float,
    r60: float,
    volume: float = 1.5,
    sector: str = "Technology",
    industry: str = "Semiconductors",
) -> dict:
    return {
        "symbol": symbol,
        "name": f"{symbol} Corp",
        "sector": sector,
        "industry": industry,
        "frontier_rank": rank,
        "opportunity_score": score,
        "score_components": {"trend_quality": 95.0},
        "features": {
            "latest_session": "2026-09-18",
            "returns": {"1": r1, "5": r5, "20": r20, "60": r60},
            "atr_fraction": 0.03,
            "realized_volatility_20d": 0.025,
            "volume_ratio_20d": volume,
        },
        "freshness": {
            "observed_at": "2026-09-18T19:00:00Z",
            "latest_session": "2026-09-18",
        },
        "admission": {"production_decision_influence": False},
    }


def frontier(rows: list[dict]) -> dict:
    payload = {
        "schema_version": "stock-trading-v2-opportunity-frontier-v1",
        "market": "US",
        "generated_at": "2026-09-18T19:00:00Z",
        "global_selection_cutoff": None,
        "frontier_size": len(rows),
        "candidates": rows,
        "governance": {"production_decision_influence": False},
    }
    payload["frontier_sha256"] = contracts.payload_sha256(payload)
    return payload


def direct_event() -> dict:
    return {
        "event_id": "corp-nvda-1",
        "event_domain": "technology",
        "event_kind": "guidance_raise",
        "title": "NVIDIA raises guidance on strong AI demand",
        "published_at": "2026-09-18T18:30:00Z",
        "event_strength": 0.92,
        "confidence": 0.92,
        "severity": 0.95,
        "source_reliability": 0.98,
        "direct_impact": 0.9,
        "sector_impact": 0.7,
        "entity_symbols": ["NVDA"],
        "entity_themes": ["semiconductor", "ai_market"],
        "scenario_tags": ["theme:semiconductor"],
    }


class MarketRelationshipTriggerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = trigger.load_config()
        self.now = datetime(2026, 9, 18, 19, 0, tzinfo=UTC)
        self.rows = [
            candidate("NVDA", 1, 92.0, r1=0.08, r5=0.15, r20=0.28, r60=0.42, volume=2.8),
            candidate("AMD", 2, 88.0, r1=0.045, r5=0.11, r20=0.22, r60=0.31, volume=2.1),
            candidate("AVGO", 3, 85.0, r1=0.035, r5=0.09, r20=0.18, r60=0.27, volume=1.9),
            candidate("MSFT", 4, 82.0, r1=0.012, r5=0.04, r20=0.08, r60=0.14, volume=1.3, industry="Software"),
            candidate("JPM", 5, 78.0, r1=0.006, r5=0.02, r20=0.05, r60=0.08, volume=1.1, sector="Financials", industry="Banks"),
        ]

    def test_direct_event_reaction_gets_hot_attention_and_lazy_belief(self) -> None:
        row = trigger.score_candidate(
            self.rows[0],
            self.rows,
            [direct_event()],
            now=self.now,
            config=self.config,
        )
        self.assertEqual("DIRECT_EVENT_REACTION", row["trigger_type"])
        self.assertEqual("HOT", row["attention_tier"])
        self.assertTrue(row["deep_belief_eligible"])
        self.assertEqual("direct", row["event_context"][0]["relation"])
        self.assertGreater(row["components"]["peer_confirmation"], 60.0)

    def test_peer_gets_theme_readthrough_not_fake_direct_event(self) -> None:
        row = trigger.score_candidate(
            self.rows[1],
            self.rows,
            [direct_event()],
            now=self.now,
            config=self.config,
        )
        self.assertNotEqual("direct", row["event_context"][0]["relation"])
        self.assertEqual("theme", row["event_context"][0]["relation"])
        self.assertIn(row["trigger_type"], {"PEER_READTHROUGH", "CLUSTER_MOMENTUM"})

    def test_lead_lag_is_hypothesis_not_correlation_claim(self) -> None:
        lagger = candidate("AMD", 1, 88.0, r1=0.005, r5=0.07, r20=0.18, r60=0.28, volume=1.1)
        leader1 = candidate("NVDA", 2, 86.0, r1=0.04, r5=0.10, r20=0.20, r60=0.30, volume=2.5)
        leader2 = candidate("AVGO", 3, 84.0, r1=0.03, r5=0.08, r20=0.17, r60=0.25, volume=2.0)
        row = trigger.score_candidate(lagger, [lagger, leader1, leader2], [], now=self.now, config=self.config)
        self.assertTrue(row["peer_context"]["lead_lag_watch"])
        self.assertEqual("LEAD_LAG_WATCH", row["trigger_type"])

    def test_attention_budget_preserves_exploration_when_no_trigger_exists(self) -> None:
        quiet = [
            candidate("AAA", 1, 80.0, r1=0.001, r5=0.005, r20=0.01, r60=0.02, volume=1.0, sector="Industrials", industry="Machinery"),
            candidate("BBB", 2, 78.0, r1=0.001, r5=0.004, r20=0.009, r60=0.018, volume=1.0, sector="Health Care", industry="Medical"),
            candidate("CCC", 3, 76.0, r1=-0.001, r5=0.003, r20=0.008, r60=0.015, volume=1.0, sector="Utilities", industry="Utilities"),
        ]
        snapshot = trigger.build_snapshot(
            frontier(quiet),
            {"generated_at": "2026-09-18T18:55:00Z", "events": []},
            self.config,
            generated_at=self.now,
        )
        self.assertEqual(2, len(snapshot["attention_queue"]))
        self.assertTrue(all(row["attention_source"] == "exploration" for row in snapshot["attention_queue"]))
        self.assertEqual([], snapshot["deep_belief_queue"])
        self.assertGreaterEqual(snapshot["attention_economics"]["counterfactual_slot_reduction_fraction"], 0.8)

    def test_history_is_append_only_and_deduplicates_same_semantic_observation(self) -> None:
        snapshot = trigger.build_snapshot(
            frontier(self.rows),
            {"generated_at": "2026-09-18T18:55:00Z", "events": [direct_event()]},
            self.config,
            generated_at=self.now,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = trigger.freeze_observations(snapshot, root=root, config=self.config)
            second = trigger.freeze_observations(snapshot, root=root, config=self.config)
            self.assertGreaterEqual(first["written"], 1)
            self.assertEqual(0, second["written"])
            self.assertGreaterEqual(second["existing"], 1)
            files = list(root.rglob("*.json"))
            self.assertGreaterEqual(len(files), 1)

    def test_snapshot_contract_stays_shadow_only(self) -> None:
        snapshot = trigger.build_snapshot(
            frontier(self.rows),
            {"generated_at": "2026-09-18T18:55:00Z", "events": [direct_event()]},
            self.config,
            generated_at=self.now,
        )
        trigger.validate_snapshot(snapshot, self.config)
        self.assertFalse(snapshot["governance"]["production_decision_influence"])
        self.assertFalse(snapshot["governance"]["deep_belief_execution"])
        self.assertTrue(snapshot["learning_contract"]["lead_lag_is_hypothesis_until_settled"])


if __name__ == "__main__":
    unittest.main()
