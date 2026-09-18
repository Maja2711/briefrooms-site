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
            "last_close": 100.0,
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

    def test_deep_belief_is_strict_subset_of_trigger_attention_slots(self) -> None:
        scored = [
            {
                "symbol": f"TOP{rank}",
                "attention_score": 100.0 - rank,
                "attention_tier": "HOT",
                "trigger_type": "BACKGROUND",
                "deep_belief_eligible": False,
                "frontier_rank": rank,
                "relationship_rank": rank,
                "event_context": [],
            }
            for rank in range(1, 5)
        ]
        scored.append({
            "symbol": "DEEP5",
            "attention_score": 95.0,
            "attention_tier": "HOT",
            "trigger_type": "DIRECT_EVENT_REACTION",
            "deep_belief_eligible": True,
            "frontier_rank": 5,
            "relationship_rank": 5,
            "event_context": [{"event_id": "evt-deep5"}],
        })
        queue, deep_queue = trigger.allocate_attention(scored, config=self.config)
        sources = {row["symbol"]: row["attention_source"] for row in queue}
        self.assertEqual("exploration", sources["DEEP5"])
        self.assertEqual([], deep_queue)

        scored[0]["deep_belief_eligible"] = True
        scored[0]["trigger_type"] = "DIRECT_EVENT_REACTION"
        scored[0]["event_context"] = [{"event_id": "evt-top1"}]
        queue, deep_queue = trigger.allocate_attention(scored, config=self.config)
        trigger_symbols = {
            row["symbol"] for row in queue if row["attention_source"] == "trigger"
        }
        self.assertEqual(["TOP1"], [row["symbol"] for row in deep_queue])
        self.assertTrue(all(row["symbol"] in trigger_symbols for row in deep_queue))

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
            frozen = [__import__("json").loads(path.read_text(encoding="utf-8")) for path in files]
            sources = {row["attention_source"] for row in frozen}
            self.assertIn("trigger", sources)
            trigger_rows = [row for row in frozen if row["attention_source"] == "trigger"]
            self.assertTrue(trigger_rows)
            self.assertEqual(100.0, trigger_rows[0]["reference_market_state"]["price"])
            self.assertTrue(trigger_rows[0]["reference_market_state"]["point_in_time_frozen"])

    def test_trigger_and_exploration_queue_sources_are_explicit(self) -> None:
        quiet = [
            candidate("AAA", 1, 80.0, r1=0.001, r5=0.005, r20=0.01, r60=0.02, volume=1.0, sector="Industrials", industry="Machinery"),
            candidate("BBB", 2, 78.0, r1=0.001, r5=0.004, r20=0.009, r60=0.018, volume=1.0, sector="Health Care", industry="Medical"),
        ]
        quiet_snapshot = trigger.build_snapshot(
            frontier(quiet),
            {"generated_at": "2026-09-18T18:55:00Z", "events": []},
            self.config,
            generated_at=self.now,
        )
        self.assertEqual({"exploration"}, {row["attention_source"] for row in quiet_snapshot["attention_queue"]})

        hot_snapshot = trigger.build_snapshot(
            frontier(self.rows),
            {"generated_at": "2026-09-18T18:55:00Z", "events": [direct_event()]},
            self.config,
            generated_at=self.now,
        )
        self.assertIn("trigger", {row["attention_source"] for row in hot_snapshot["attention_queue"]})

    def test_relationship_pool_can_surface_non_frontier_lagger(self) -> None:
        lagger = candidate("LAG", 99, 70.0, r1=0.04, r5=0.08, r20=0.12, r60=0.18, volume=2.2)
        lagger["frontier_rank"] = None
        lagger["relationship_rank"] = 3
        base = frontier(self.rows[:2])
        base["relationship_pool"] = [dict(self.rows[0]), dict(self.rows[1]), lagger]
        base["relationship_pool"][0]["relationship_rank"] = 1
        base["relationship_pool"][1]["relationship_rank"] = 2
        base["relationship_pool_size"] = 3
        base.pop("frontier_sha256", None)
        base["frontier_sha256"] = contracts.payload_sha256(base)
        event = direct_event()
        event["event_id"] = "corp-lag-1"
        event["entity_symbols"] = ["LAG"]
        snapshot = trigger.build_snapshot(
            base,
            {"generated_at": "2026-09-18T18:55:00Z", "events": [event]},
            self.config,
            generated_at=self.now,
        )
        self.assertIn("LAG", {row["symbol"] for row in snapshot["candidates"]})
        self.assertGreater(snapshot["relationship_pool_size"], snapshot["frontier_size"])

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
