from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from scripts import briefrooms_market_relationship_outcomes as outcomes
    from scripts import stock_trading_v2_contracts as contracts
except ModuleNotFoundError:
    import briefrooms_market_relationship_outcomes as outcomes
    import stock_trading_v2_contracts as contracts


def observation(direction: str = "UP", trigger_type: str = "PEER_READTHROUGH") -> dict:
    payload = {
        "schema_version": "briefrooms-market-relationship-observation-v1",
        "observation_id": "rel-test-001",
        "market": "US",
        "symbol": "AMD",
        "observed_at": "2026-09-18T19:00:00Z",
        "session_date": "2026-09-18",
        "attention_score": 80.0,
        "attention_tier": "HOT",
        "attention_source": "trigger",
        "trigger_type": trigger_type,
        "direction": direction,
        "components": {
            "event_materiality": 70.0,
            "entity_reaction": 60.0,
            "peer_confirmation": 80.0,
            "persistence": 75.0,
            "frontier_prior": 85.0,
        },
        "event_context": [{
            "event_id": "corp-nvda-1",
            "relation": "theme",
            "event_kind": "guidance_raise",
            "event_domain": "technology",
        }],
        "peer_context": {
            "agreement_rate": 0.8,
            "leader_symbol": "NVDA",
            "lead_lag_watch": True,
        },
        "frontier_rank": 2,
        "source_frontier_sha256": "frontier",
        "source_event_snapshot_generated_at": "2026-09-18T18:55:00Z",
        "outcome_contract": {
            "status": "PENDING",
            "settlement_horizons_sessions": [1, 3, 5, 20],
            "outcomes_must_be_separate_immutable_records": True,
            "no_hindsight_mutation": True,
        },
        "governance": {
            "production_decision_influence": False,
            "automatic_policy_writeback": False,
        },
    }
    payload["observation_sha256"] = contracts.payload_sha256(payload)
    return payload


def bars():
    rows = [
        {"day": "2026-09-18", "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0},
        {"day": "2026-09-21", "open": 100.0, "high": 104.0, "low": 99.0, "close": 103.0},
        {"day": "2026-09-22", "open": 103.0, "high": 106.0, "low": 102.0, "close": 105.0},
        {"day": "2026-09-23", "open": 105.0, "high": 107.0, "low": 104.0, "close": 106.0},
        {"day": "2026-09-24", "open": 106.0, "high": 108.0, "low": 105.0, "close": 107.0},
        {"day": "2026-09-25", "open": 107.0, "high": 109.0, "low": 106.0, "close": 108.0},
    ]
    return rows


class RelationshipOutcomeTest(unittest.TestCase):
    def test_up_trigger_settles_directional_continuation(self) -> None:
        replay = outcomes.settle_horizon(observation("UP"), bars(), horizon_sessions=3)
        self.assertIsNotNone(replay)
        assert replay is not None
        self.assertEqual("SETTLED", replay["status"])
        self.assertTrue(replay["continuation"])
        self.assertAlmostEqual(0.06, replay["directional_return"], places=8)
        self.assertGreater(replay["mfe_directional"], 0.0)

    def test_down_trigger_inverts_return_direction(self) -> None:
        replay = outcomes.settle_horizon(observation("DOWN"), bars(), horizon_sessions=1)
        self.assertIsNotNone(replay)
        assert replay is not None
        self.assertFalse(replay["continuation"])
        self.assertAlmostEqual(-0.03, replay["directional_return"], places=8)

    def test_unresolved_horizon_is_not_persisted_early(self) -> None:
        replay = outcomes.settle_horizon(observation(), bars(), horizon_sessions=20)
        self.assertIsNone(replay)

    def test_immutable_outcome_detects_semantic_conflict(self) -> None:
        replay = outcomes.settle_horizon(observation(), bars(), horizon_sessions=1)
        assert replay is not None
        row = outcomes.build_outcome(observation(), replay, settled_at="2026-09-21T21:00:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(outcomes.persist_outcome(root, row))
            second = outcomes.build_outcome(observation(), replay, settled_at="2026-09-22T21:00:00Z")
            self.assertFalse(outcomes.persist_outcome(root, second))
            broken = dict(row)
            broken["replay"] = {**row["replay"], "directional_return": 0.99}
            broken.pop("outcome_sha256", None)
            broken["outcome_sha256"] = contracts.payload_sha256(broken)
            with self.assertRaises(contracts.ContractError):
                outcomes.persist_outcome(root, broken)

    def test_exploration_control_is_measured_but_cannot_promote(self) -> None:
        rows = []
        for index in range(30):
            obs = observation()
            obs["observation_id"] = f"control-{index}"
            obs["symbol"] = f"C{index % 10}"
            obs["attention_source"] = "exploration"
            replay = {
                "status": "SETTLED",
                "horizon_sessions": 5,
                "directional_return": 0.04,
                "continuation": True,
            }
            rows.append(outcomes.build_outcome(obs, replay, settled_at="2026-10-20T20:00:00Z"))
        report = outcomes.build_learning_report(rows)
        group = report["groups"][0]
        self.assertEqual("exploration", group["attention_source"])
        self.assertEqual("CONTROL_ARM_ONLY", group["promotion_state"])

    def test_learning_report_never_auto_promotes(self) -> None:
        rows = []
        for index in range(30):
            obs = observation()
            obs["observation_id"] = f"rel-{index}"
            obs["symbol"] = f"S{index % 10}"
            replay = {
                "status": "SETTLED",
                "horizon_sessions": 5,
                "directional_return": 0.03,
                "continuation": True,
            }
            rows.append(outcomes.build_outcome(obs, replay, settled_at="2026-10-20T20:00:00Z"))
        report = outcomes.build_learning_report(rows)
        group = report["groups"][0]
        self.assertEqual("ELIGIBLE_FOR_WEIGHT_CHALLENGER", group["promotion_state"])
        self.assertFalse(report["promotion_policy"]["automatic_promotion"])
        self.assertFalse(report["governance"]["automatic_policy_writeback"])

    def test_unplayable_missing_reference_is_explicit(self) -> None:
        replay = outcomes.settle_horizon(observation(), bars()[1:], horizon_sessions=1)
        self.assertIsNotNone(replay)
        assert replay is not None
        self.assertEqual("UNPLAYABLE", replay["status"])
        self.assertEqual("reference_session_missing", replay["reason"])


if __name__ == "__main__":
    unittest.main()
