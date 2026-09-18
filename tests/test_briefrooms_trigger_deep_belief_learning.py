from __future__ import annotations

import unittest
from copy import deepcopy

from scripts import briefrooms_trigger_deep_belief as targeted
from scripts import briefrooms_trigger_deep_belief_learning as learning
from scripts import stock_trading_v2_contracts as contracts


def research_row(
    *,
    observation_id: str = "rel-001",
    symbol: str = "AAA",
    direction: str = "UP",
    base: float = 80.0,
    deep_score: float = 83.0,
) -> dict:
    return {
        "trigger_observation_id": observation_id,
        "symbol": symbol,
        "trigger_type": "DIRECT_EVENT_REACTION",
        "trigger_direction": direction,
        "opportunity_score": base,
        "deep_opportunity_score": deep_score,
        "evidence_status": "COMPLETE",
        "evidence_metrics": {
            "primary_count": 1,
            "secondary_count": 2,
            "material_event_present": True,
        },
        "admission": {"production_decision_influence": False},
        "research_snapshot_id": "tdb-test",
        "researched_at": "2026-09-18T19:02:00Z",
    }


def outcome_row(
    *,
    observation_id: str = "rel-001",
    horizon: int = 5,
    continuation: bool = True,
    directional_return: float = 0.04,
) -> dict:
    return {
        "observation_id": observation_id,
        "horizon_sessions": horizon,
        "replay": {
            "status": "SETTLED",
            "directional_return": directional_return,
            "continuation": continuation,
            "mfe_directional": 0.06,
            "mae_directional": -0.015,
            "terminal_session": "2026-09-25",
        },
    }


class TriggerDeepBeliefLearningTests(unittest.TestCase):
    def test_exact_observation_id_join_and_support_agreement(self) -> None:
        linked = learning.link_research_to_outcomes(
            [research_row()],
            [outcome_row()],
        )
        self.assertEqual(1, len(linked))
        row = linked[0]
        self.assertEqual("rel-001", row["trigger_observation_id"])
        self.assertEqual("SUPPORTS_TRIGGER", row["research_update_class"])
        self.assertTrue(row["directional_update_outcome_agreement"])
        self.assertAlmostEqual(3.0, row["score_delta"], places=8)
        self.assertAlmostEqual(3.0, row["directional_research_update"], places=8)

    def test_same_ticker_different_observation_id_never_heuristically_joins(self) -> None:
        linked = learning.link_research_to_outcomes(
            [research_row(observation_id="rel-001", symbol="AAA")],
            [outcome_row(observation_id="rel-other")],
        )
        self.assertEqual([], linked)

    def test_opposing_research_update_agrees_when_trigger_does_not_continue(self) -> None:
        linked = learning.link_research_to_outcomes(
            [research_row(base=80.0, deep_score=77.0, direction="UP")],
            [outcome_row(continuation=False, directional_return=-0.03)],
        )
        self.assertEqual(1, len(linked))
        row = linked[0]
        self.assertEqual("OPPOSES_TRIGGER", row["research_update_class"])
        self.assertTrue(row["directional_update_outcome_agreement"])
        self.assertAlmostEqual(-3.0, row["directional_research_update"], places=8)

    def test_report_stays_research_only_and_tracks_pending_targets(self) -> None:
        research = [
            research_row(observation_id="rel-001", symbol="AAA"),
            research_row(observation_id="rel-002", symbol="BBB"),
        ]
        report = learning.build_report(
            research,
            [outcome_row(observation_id="rel-001", horizon=1)],
        )
        self.assertEqual(2, report["research_targets"])
        self.assertEqual(1, report["research_targets_with_any_settled_outcome"])
        self.assertEqual(1, report["pending_research_targets"])
        self.assertEqual(1, report["linked_outcome_rows"])
        self.assertTrue(report["learning_policy"]["exact_observation_id_join_required"])
        self.assertTrue(report["learning_policy"]["ticker_time_heuristic_join_forbidden"])
        self.assertFalse(report["governance"]["production_decision_influence"])
        self.assertFalse(report["governance"]["automatic_promotion"])
        learning.validate_report(report)

    def test_historical_research_snapshot_hash_and_lineage_are_verified(self) -> None:
        payload = {
            "schema_version": targeted.SCHEMA_VERSION,
            "snapshot_id": "tdb-test",
            "market": "US",
            "generated_at": "2026-09-18T19:02:00Z",
            "research_backend": targeted.BACKEND,
            "belief_mode": targeted.BELIEF_MODE,
            "full_belief_core_invocation": False,
            "source_trigger_snapshot_sha256": "trigger-sha",
            "source_frontier_sha256": "frontier-sha",
            "target_count": 1,
            "targets": [research_row()],
            "research_economics": {},
            "research_yield": {},
            "learning_contract": {},
            "governance": {
                "production_decision_influence": False,
                "trade_execution": False,
                "automatic_portfolio_admission": False,
                "automatic_policy_writeback": False,
                "automatic_promotion": False,
            },
        }
        payload["snapshot_sha256"] = contracts.payload_sha256(payload)
        learning.validate_historical_research_snapshot(payload)

        broken = deepcopy(payload)
        broken["targets"][0]["trigger_observation_id"] = "bad"
        broken.pop("snapshot_sha256", None)
        broken["snapshot_sha256"] = contracts.payload_sha256(broken)
        with self.assertRaises(contracts.ContractError):
            learning.validate_historical_research_snapshot(broken)


if __name__ == "__main__":
    unittest.main()
