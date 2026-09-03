from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts.belief_market_data_adapter import Bar
from scripts.daily_engine_contract import DailyEngineOutput
from scripts import daily_eurusd_spot_v16 as v16

UTC = timezone.utc
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def candidate(
    *,
    direction: str = "SHORT",
    score: float = 39.0,
    confidence: float = 0.22,
    source: str = "LOW_EDGE_LEARNING_EXPLORATION",
    components: dict[str, float] | None = None,
) -> DailyEngineOutput:
    components = components or {
        "trend": -0.10,
        "broad_usd_environment": -0.14,
        "us_rates_pressure_proxy": -0.18,
    }
    entry = 1.16000
    risk = 0.00300
    stop = entry - risk if direction == "LONG" else entry + risk
    target = entry + 1.8 * risk if direction == "LONG" else entry - 1.8 * risk
    return DailyEngineOutput(
        instrument="EUR/USD",
        timestamp=NOW.isoformat().replace("+00:00", "Z"),
        direction=direction,
        score=score,
        confidence=confidence,
        entry=entry,
        stop=stop,
        target=target,
        horizon="intraday_to_27h",
        engine_version="eurusd-daily-spot-v1.5.0",
        status="SIGNAL",
        decision_mode="WITHOUT",
        metadata={
            "decision_source": source,
            "learning_eligible": True,
            "learning_namespace": "TEST",
            "components": components,
            "weights": {
                "trend": 0.55,
                "broad_usd_environment": 0.25,
                "us_rates_pressure_proxy": 0.20,
            },
            "candidate": {
                "direction": direction,
                "score": score,
                "confidence": confidence,
                "accepted": True,
                "gate_reasons": [],
                "source": source,
            },
        },
    ).validate()


def loss_trade(
    *,
    source: str = "NATIVE",
    score: float = 40.0,
    confidence: float = 0.20,
    hours_ago: float = 2.0,
    components: dict[str, float] | None = None,
) -> dict[str, object]:
    return {
        "trade_id": "prior-loss",
        "instrument": "EUR/USD",
        "direction": "SHORT",
        "opened_at": (NOW - timedelta(hours=hours_ago + 2)).isoformat().replace("+00:00", "Z"),
        "closed_at": (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
        "entry": 1.1575,
        "stop": 1.1606,
        "target": 1.1519,
        "exit_price": 1.1606,
        "exit_reason": "STOP_LOSS",
        "r_multiple": -1.0,
        "outcome": "LOSS",
        "entry_score": score,
        "entry_confidence": confidence,
        "entry_components": components or {
            "trend": -0.09,
            "broad_usd_environment": -0.13,
            "us_rates_pressure_proxy": -0.17,
        },
        "entry_weights": {
            "trend": 0.55,
            "broad_usd_environment": 0.25,
            "us_rates_pressure_proxy": 0.20,
        },
        "decision_source": source,
        "learning_eligible": True,
        "mfe_r": 0.13,
        "mae_r": -1.03,
    }


class DailyEURUSDPostTradeIntelligenceTests(unittest.TestCase):
    def test_low_mfe_stop_loss_is_attributed_without_causal_overclaim(self):
        review = v16._post_trade_review(loss_trade())
        self.assertEqual(review["primary_pattern"], "DIRECTION_OR_ENTRY_TIMING_FAILURE")
        self.assertFalse(review["causal_claim"])
        self.assertEqual(review["classification_basis"], "observed_price_path_not_causal_proof")
        self.assertEqual(review["error_attribution"]["risk_geometry"], "unresolved_from_single_trade")
        self.assertEqual(
            review["learning_action"],
            "require_material_thesis_change_before_same_family_reentry",
        )

    def test_recent_same_native_thesis_loss_blocks_weak_reentry(self):
        output = v16._apply_same_thesis_guard(
            candidate(),
            {"trades": [loss_trade(source="NATIVE")]},
        )
        self.assertEqual(output.direction, "FLAT")
        self.assertEqual(output.status, "NO_TRADE")
        guard = output.metadata["same_thesis_reentry_guard"]
        self.assertTrue(guard["blocked"])
        self.assertEqual(guard["source_family"], "NATIVE_COMPONENTS")
        self.assertEqual(
            guard["reason"],
            "recent_same_thesis_loss_without_material_new_evidence",
        )
        self.assertIn("same_thesis_reentry_blocked", output.metadata["candidate"]["gate_reasons"])

    def test_materially_stronger_same_thesis_can_reenter(self):
        output = v16._apply_same_thesis_guard(
            candidate(score=35.0, confidence=0.30),
            {"trades": [loss_trade(source="NATIVE", score=40.0, confidence=0.20)]},
        )
        self.assertEqual(output.direction, "SHORT")
        guard = output.metadata["same_thesis_reentry_guard"]
        self.assertFalse(guard["blocked"])
        self.assertEqual(guard["reason"], "material_new_evidence_detected")
        self.assertIn("directional_score_strength_improved", guard["change"]["material_change_triggers"])

    def test_different_thesis_family_is_not_blocked(self):
        output = v16._apply_same_thesis_guard(
            candidate(source="LOW_EDGE_LEARNING_EXPLORATION"),
            {"trades": [loss_trade(source="A_TECHNICAL_FALLBACK")]},
        )
        self.assertEqual(output.direction, "SHORT")
        guard = output.metadata["same_thesis_reentry_guard"]
        self.assertFalse(guard["blocked"])
        self.assertEqual(guard["reason"], "recent_loss_belongs_to_different_thesis_family")

    def test_old_loss_outside_guard_window_is_not_blocked(self):
        output = v16._apply_same_thesis_guard(
            candidate(),
            {"trades": [loss_trade(hours_ago=13.0)]},
        )
        self.assertEqual(output.direction, "SHORT")
        self.assertFalse(output.metadata["same_thesis_reentry_guard"]["blocked"])
        self.assertEqual(
            output.metadata["same_thesis_reentry_guard"]["reason"],
            "no_recent_meaningful_same_direction_loss",
        )

    def test_position_persists_entry_thesis(self):
        c = candidate()
        position = v16.create_position(c.to_dict())
        self.assertIn("entry_thesis", position)
        self.assertEqual(position["entry_thesis"]["source_family"], "NATIVE_COMPONENTS")
        self.assertEqual(position["entry_thesis"]["direction"], "SHORT")

    def test_closed_trade_persists_review_and_thesis(self):
        c = candidate()
        position = v16.create_position(c.to_dict())
        bar = Bar(
            timestamp=NOW + timedelta(minutes=1),
            open=1.1601,
            high=1.1631,
            low=1.1597,
            close=1.1630,
            volume=1000,
        )
        trade = v16.evaluate_position(position, [bar], bar.timestamp)
        self.assertIsNotNone(trade)
        assert trade is not None
        self.assertEqual(trade["exit_reason"], "STOP_LOSS")
        self.assertIn("entry_thesis", trade)
        self.assertIn("post_trade_review", trade)
        self.assertEqual(
            trade["post_trade_review"]["schema_version"],
            "eurusd-post-trade-review-v1",
        )
        self.assertFalse(trade["post_trade_review"]["policy_mutation_allowed_from_single_trade"])


if __name__ == "__main__":
    unittest.main()
