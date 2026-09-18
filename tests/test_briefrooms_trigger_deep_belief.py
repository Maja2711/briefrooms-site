from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from scripts import briefrooms_market_relationship_trigger as relationship
from scripts import briefrooms_trigger_deep_belief as targeted
from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_deep_evidence as deep
from scripts import stock_trading_v2_discovery as discovery


def candidate(symbol: str, rank: int, score: float) -> dict:
    return {
        "symbol": symbol,
        "market_data_symbol": symbol,
        "name": f"{symbol} Corp",
        "exchange": "NASDAQ",
        "security_type": "COMMON_STOCK",
        "sector": "Technology",
        "industry": "Semiconductors",
        "relationship_rank": rank,
        "frontier_rank": rank if rank <= 1 else None,
        "opportunity_score": score,
        "research_utility_vs_cash": 0.4,
        "score_components": {"trend_quality": 90.0},
        "features": {
            "latest_session": "2026-09-18",
            "last_close": 100.0 + rank,
            "returns": {"1": 0.03, "5": 0.08, "20": 0.16, "60": 0.25},
            "atr_fraction": 0.02,
            "realized_volatility_20d": 0.025,
            "median_turnover_20d": 50000000.0,
            "volume_ratio_20d": 1.8,
        },
        "stage_zero": {"rank": rank, "lanes": ["movers"]},
        "liquidity": {
            "discovery_pass": True,
            "median_turnover": 50000000.0,
            "production_reference_pass": True,
            "production_reference": 25000000.0,
        },
        "freshness": {
            "latest_session": "2026-09-18",
            "observed_at": "2026-09-18T19:00:00Z",
            "recheck_after": "2026-09-18T20:15:00Z",
            "global_cutoff": None,
        },
        "admission": {
            "status": "PENDING_DEEP_EVIDENCE_AND_RISK_PLAN",
            "production_decision_influence": False,
        },
    }


def frontier() -> dict:
    first = candidate("AAA", 1, 92.0)
    second = candidate("BBB", 2, 88.0)
    payload = {
        "schema_version": discovery.SCHEMA_VERSION,
        "market": "US",
        "generated_at": "2026-09-18T19:00:00Z",
        "mode": "shadow_no_production_influence",
        "holding_horizon": "OPEN_ENDED_MODEL_CONTROLLED",
        "global_selection_cutoff": None,
        "universe_semantic_sha256": "test-universe",
        "universe_size": 1000,
        "features_available": 100,
        "eligible_after_discovery_liquidity": 100,
        "frontier_size": 1,
        "relationship_pool_size": 2,
        "relationship_pool": [
            {
                key: deepcopy(second[key])
                for key in (
                    "symbol", "market_data_symbol", "name", "exchange", "sector", "industry",
                    "relationship_rank", "frontier_rank", "opportunity_score",
                    "score_components", "features", "freshness",
                )
            },
            {
                key: deepcopy(first[key])
                for key in (
                    "symbol", "market_data_symbol", "name", "exchange", "sector", "industry",
                    "relationship_rank", "frontier_rank", "opportunity_score",
                    "score_components", "features", "freshness",
                )
            },
        ],
        "cash_alternative": {"status": "AVAILABLE", "research_utility": 0.0},
        "candidates": [first],
        "fetch_audit": {},
        "governance": {
            "production_decision_influence": False,
            "automatic_portfolio_admission": False,
            "rank_only_not_recommendation": True,
            "deep_evidence_pending": True,
            "no_forced_trade": True,
            "no_global_selection_cutoff": True,
        },
    }
    # Relationship pool must be in relationship-rank order.
    payload["relationship_pool"] = [
        {
            key: deepcopy(row[key])
            for key in (
                "symbol", "market_data_symbol", "name", "exchange", "sector", "industry",
                "relationship_rank", "frontier_rank", "opportunity_score",
                "score_components", "features", "freshness",
            )
        }
        for row in (first, second)
    ]
    payload["frontier_sha256"] = contracts.payload_sha256(payload)
    discovery.validate_frontier(payload)
    return payload


def trigger_snapshot(frontier_payload: dict) -> dict:
    rows = [
        {
            "symbol": "AAA",
            "attention_score": 91.0,
            "attention_tier": "HOT",
            "trigger_type": "DIRECT_EVENT_REACTION",
        },
        {
            "symbol": "BBB",
            "attention_score": 87.0,
            "attention_tier": "HOT",
            "trigger_type": "PEER_READTHROUGH",
        },
    ]
    payload = {
        "schema_version": relationship.SCHEMA_VERSION,
        "version": "market-relationship-trigger-v1.0.0",
        "market": "US",
        "generated_at": "2026-09-18T19:01:00Z",
        "mode": "shadow_attention_allocator",
        "central_object": "EVENT_x_ENTITY_x_PEERS_x_MARKET_REACTION_x_TIME",
        "source_frontier_sha256": frontier_payload["frontier_sha256"],
        "source_frontier_generated_at": frontier_payload["generated_at"],
        "source_event_snapshot_generated_at": "2026-09-18T18:55:00Z",
        "frontier_size": 1,
        "relationship_pool_size": 2,
        "event_count_seen": 1,
        "candidate_count": 2,
        "candidates": rows,
        "attention_queue": [
            {"symbol": "AAA", "attention_source": "trigger", "attention_score": 91.0},
            {"symbol": "BBB", "attention_source": "trigger", "attention_score": 87.0},
        ],
        "deep_belief_queue": [
            {
                "symbol": "AAA",
                "attention_score": 91.0,
                "trigger_type": "DIRECT_EVENT_REACTION",
                "strongest_event_id": "evt-1",
                "reason": "high_attention_event_reaction_peer_cluster",
            },
            {
                "symbol": "BBB",
                "attention_score": 87.0,
                "trigger_type": "PEER_READTHROUGH",
                "strongest_event_id": "evt-1",
                "reason": "high_attention_event_reaction_peer_cluster",
            },
        ],
        "attention_economics": {},
        "learning_contract": {},
        "governance": deepcopy(relationship.load_config()["governance"]),
    }
    payload["snapshot_sha256"] = contracts.payload_sha256(payload)
    relationship.validate_snapshot(payload, relationship.load_config())
    return payload


def evidence_rows(symbol: str) -> tuple[list[dict], dict]:
    rows = [{
        "evidence_id": f"ev-{symbol}",
        "provider": "SEC_EDGAR",
        "authority": "primary",
        "source_kind": "regulatory_filing",
        "title": f"{symbol} raises guidance after record revenue",
        "published_at": "2026-09-18T18:30:00Z",
        "age_hours": 0.5,
        "event_type": "guidance",
        "materiality": 5,
        "direction": 1,
    }]
    meta = {
        "primary": {"provider": "SEC_EDGAR", "ok": True},
        "secondary": {"provider": "GOOGLE_NEWS_RSS", "ok": True},
    }
    return rows, meta


class TriggerDeepBeliefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frontier = frontier()
        self.trigger = trigger_snapshot(self.frontier)
        self.trigger_config = relationship.load_config()
        self.evidence_config = deep.load_config()

    def test_max_two_trigger_targets_run_real_deep_evidence_proxy(self) -> None:
        payload = targeted.build_snapshot(
            self.trigger,
            self.frontier,
            {
                "AAA": evidence_rows("AAA"),
                "BBB": evidence_rows("BBB"),
            },
            trigger_config=self.trigger_config,
            evidence_config=self.evidence_config,
            generated_at="2026-09-18T19:02:00Z",
        )
        self.assertEqual(2, payload["target_count"])
        self.assertEqual(["AAA", "BBB"], [row["symbol"] for row in payload["targets"]])
        trigger_rows = {
            row["symbol"]: row
            for row in self.trigger["candidates"]
        }
        for row in payload["targets"]:
            self.assertEqual(
                relationship.build_observation_id(self.trigger, trigger_rows[row["symbol"]]),
                row["trigger_observation_id"],
            )
        self.assertTrue(all(row["evidence_metrics"]["primary_count"] == 1 for row in payload["targets"]))
        self.assertFalse(payload["full_belief_core_invocation"])
        self.assertFalse(payload["governance"]["production_decision_influence"])
        self.assertGreater(payload["research_economics"]["theoretical_slot_reduction_fraction"], 0.0)
        self.assertFalse(payload["research_economics"]["realized_champion_compute_reduction"])
        targeted.validate_snapshot(
            payload,
            trigger_config=self.trigger_config,
            evidence_config=self.evidence_config,
        )

    def test_relationship_pool_target_can_be_outside_top_frontier(self) -> None:
        payload = targeted.build_snapshot(
            self.trigger,
            self.frontier,
            {"AAA": evidence_rows("AAA"), "BBB": evidence_rows("BBB")},
            trigger_config=self.trigger_config,
            evidence_config=self.evidence_config,
        )
        bbb = next(row for row in payload["targets"] if row["symbol"] == "BBB")
        self.assertIsNone(bbb["frontier_rank"])
        self.assertEqual(2, bbb["relationship_rank"])

    def test_immutable_history_reuses_same_trigger_identity(self) -> None:
        payload = targeted.build_snapshot(
            self.trigger,
            self.frontier,
            {"AAA": evidence_rows("AAA"), "BBB": evidence_rows("BBB")},
            trigger_config=self.trigger_config,
            evidence_config=self.evidence_config,
            generated_at="2026-09-18T19:02:00Z",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(targeted.persist_snapshot(root, payload))
            self.assertFalse(targeted.persist_snapshot(root, payload))
            existing = targeted.find_existing(
                root,
                self.trigger["snapshot_sha256"],
                trigger_config_version=str(self.trigger_config.get("version") or "unknown"),
                evidence_config_version=str(self.evidence_config.get("version") or "unknown"),
            )
            self.assertIsNotNone(existing)
            assert existing is not None
            self.assertEqual(payload["snapshot_id"], existing["snapshot_id"])

    def test_deep_target_must_be_in_frozen_trigger_attention_arm(self) -> None:
        broken = deepcopy(self.trigger)
        broken["attention_queue"] = [
            row for row in broken["attention_queue"] if row["symbol"] != "BBB"
        ]
        broken.pop("snapshot_sha256", None)
        broken["snapshot_sha256"] = contracts.payload_sha256(broken)
        with self.assertRaises(contracts.ContractError):
            targeted.validate_inputs(broken, self.frontier, self.trigger_config)

    def test_full_belief_core_claim_is_rejected(self) -> None:
        payload = targeted.build_snapshot(
            self.trigger,
            self.frontier,
            {"AAA": evidence_rows("AAA"), "BBB": evidence_rows("BBB")},
            trigger_config=self.trigger_config,
            evidence_config=self.evidence_config,
        )
        broken = deepcopy(payload)
        broken["full_belief_core_invocation"] = True
        broken.pop("snapshot_sha256", None)
        broken["snapshot_sha256"] = contracts.payload_sha256(broken)
        with self.assertRaises(contracts.ContractError):
            targeted.validate_snapshot(
                broken,
                trigger_config=self.trigger_config,
                evidence_config=self.evidence_config,
            )


if __name__ == "__main__":
    unittest.main()
