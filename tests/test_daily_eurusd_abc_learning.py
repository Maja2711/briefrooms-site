from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts import daily_eurusd_abc_learning as learning

UTC = timezone.utc
T0 = datetime(2026, 9, 3, 8, 0, tzinfo=UTC)


def capture(at: datetime, *, realized: dict[str, float] | None = None, suffix: str = "x") -> dict:
    realized = realized or {"A": -27.0, "B": 48.6, "C": -13.5}
    arms = {
        "A": {"available": True, "model_type": "TECHNICAL_ONLY", "direction": "SHORT", "score": 38.0, "confidence": 0.24,
              "technical": {"components": {"trend": -0.4, "momentum": -0.2}}},
        "B": {"available": True, "model_type": "BELIEF_ONLY", "direction": "LONG", "score": 63.0, "confidence": 0.26,
              "belief": {"raw_signed_score": 0.026}},
        "C": {"available": True, "model_type": "HYBRID", "direction": "SHORT", "score": 39.0, "confidence": 0.22,
              "technical": {"components": {"trend": -0.3}}, "belief_context": {"macro": 0.1}},
    }
    entry = 1.1600
    risk = 0.003132
    plans = {}
    paths = {}
    for arm in learning.ARMS:
        direction = arms[arm]["direction"]
        stop = entry + risk if direction == "SHORT" else entry - risk
        target = entry - 1.8 * risk if direction == "SHORT" else entry + 1.8 * risk
        plans[arm] = {
            "available": True,
            "direction": direction,
            "status": "TRACKED",
            "entry_price": entry,
            "stop_price": stop,
            "target_price": target,
        }
        rbps = realized[arm]
        paths[arm] = {
            "status": "CLOSED",
            "mfe_bps": 2.0 if rbps < 0 else 50.0,
            "mae_bps": -28.0 if rbps < 0 else -5.0,
            "exit_reason": "STOP_LOSS" if rbps < 0 else "TAKE_PROFIT",
            "exit_at": (at + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "exit_price": 1.163132 if rbps < 0 else 1.154362,
            "realized_bps": rbps,
        }
    return {
        "capture_id": f"cap-{suffix}",
        "engine_version": "eurusd-daily-abc-v1.3.0",
        "captured_at": at.isoformat().replace("+00:00", "Z"),
        "market_observed_at": at.isoformat().replace("+00:00", "Z"),
        "decision_sha256": f"sha-{suffix}",
        "arms": arms,
        "trade_plan": {"arms": plans},
        "trade_path": {"arms": paths},
    }


class DailyEURUSDABCLearningTests(unittest.TestCase):
    def test_activation_is_prospective_and_does_not_backfill_existing_capture(self):
        old = capture(T0, suffix="old")
        experiment = {"captures": [old]}
        state = learning.initialize_learning_state(experiment, now=T0 + timedelta(minutes=1))
        synced, appended = learning.sync_learning(experiment, state)
        self.assertEqual(appended, 0)
        self.assertEqual(synced["episodes"], [])
        self.assertFalse(synced["anti_hindsight"]["historical_backfill"])

    def test_one_shared_contract_creates_isolated_arm_memories(self):
        old = capture(T0, suffix="old")
        future = capture(T0 + timedelta(hours=2), suffix="new")
        state = learning.initialize_learning_state({"captures": [old]}, now=T0 + timedelta(minutes=1))
        synced, appended = learning.sync_learning({"captures": [old, future]}, state)
        self.assertEqual(appended, 3)
        self.assertEqual(len(synced["episodes"]), 3)
        ids = {row["arm_id"]: row["episode_id"] for row in synced["episodes"]}
        for arm in learning.ARMS:
            self.assertEqual(synced["arm_memory"][arm]["episode_ids"], [ids[arm]])
            self.assertEqual(next(row for row in synced["episodes"] if row["arm_id"] == arm)["schema_version"], learning.LEARNING_EPISODE_SCHEMA)
        self.assertEqual(len(set(ids.values())), 3)
        learning.validate_learning(synced, learning.build_report(synced, now=T0 + timedelta(hours=3)))

    def test_sync_is_idempotent(self):
        old = capture(T0, suffix="old")
        future = capture(T0 + timedelta(hours=2), suffix="new")
        state = learning.initialize_learning_state({"captures": [old]}, now=T0 + timedelta(minutes=1))
        once, appended = learning.sync_learning({"captures": [old, future]}, state)
        twice, second = learning.sync_learning({"captures": [old, future]}, once)
        self.assertEqual(appended, 3)
        self.assertEqual(second, 0)
        self.assertEqual(once["episodes"], twice["episodes"])

    def test_loss_attribution_separates_observation_from_causal_claim(self):
        old = capture(T0, suffix="old")
        future = capture(T0 + timedelta(hours=2), suffix="new")
        state = learning.initialize_learning_state({"captures": [old]}, now=T0 + timedelta(minutes=1))
        synced, _ = learning.sync_learning({"captures": [old, future]}, state)
        row = next(item for item in synced["episodes"] if item["arm_id"] == "A")
        self.assertLess(row["outcome"]["outcome_r"], 0)
        self.assertEqual(row["error_attribution"]["primary_pattern"], "DIRECTION_OR_TIMING_FAILURE")
        self.assertFalse(row["error_attribution"]["causal_claim"])
        self.assertFalse(row["policy_change_applied"])

    def test_repeated_error_can_propose_but_never_apply_policy(self):
        state = learning.initialize_learning_state({"captures": []}, now=T0)
        state["activation_capture_cutoff"] = None
        captures = []
        for i in range(8):
            c = capture(T0 + timedelta(hours=i + 1), suffix=str(i), realized={"A": -27.0, "B": -27.0, "C": -27.0})
            captures.append(c)
        synced, appended = learning.sync_learning({"captures": captures}, state)
        self.assertEqual(appended, 24)
        report = learning.build_report(synced, now=T0 + timedelta(hours=10))
        for arm in learning.ARMS:
            lesson = report["arms"][arm]["lesson_candidate"]
            self.assertTrue(lesson["eligible"])
            self.assertTrue(lesson["policy_change_proposed"])
            self.assertFalse(lesson["policy_change_applied"])
            self.assertEqual(report["arms"][arm]["policy_stability"], 1.0)
        learning.validate_learning(synced, report)


if __name__ == "__main__":
    unittest.main()
