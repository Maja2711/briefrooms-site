from __future__ import annotations

import unittest

from scripts import stock_trading_v2_position_experience as position_exp


class StockTradingV2PositionExperienceTests(unittest.TestCase):
    def test_open_and_closed_positions_become_hold_and_exit_events(self):
        state = {
            "updated_at": "2026-09-16T16:00:00+00:00",
            "markets": {
                "GPW": {
                    "open_positions": [
                        {
                            "position_id": "gpw:1:AAA.WA",
                            "market": "GPW",
                            "symbol": "AAA.WA",
                            "status": "OPEN",
                            "entry": 100,
                            "stop": 95,
                            "target": 115,
                            "last_mark": 104,
                            "last_reviewed_at": "2026-09-16T15:00:00+00:00",
                            "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED",
                            "scheduled_exit": None,
                            "valid_until": None,
                            "time_stop": None,
                            "risk_reviews": [],
                        }
                    ],
                    "closed_positions": [
                        {
                            "position_id": "gpw:2:BBB.WA",
                            "market": "GPW",
                            "symbol": "BBB.WA",
                            "status": "CLOSED",
                            "entry": 50,
                            "stop": 47,
                            "target": 60,
                            "closed_at": "2026-09-16T14:00:00+00:00",
                            "exit_price": 47,
                            "exit_reason": "stop_loss",
                            "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED",
                        }
                    ],
                },
                "US": {"open_positions": [], "closed_positions": []},
            },
        }
        events = position_exp.events_from_portfolio_state(state)
        self.assertEqual({event["action"] for event in events}, {"HOLD", "EXIT"})
        hold = next(event for event in events if event["action"] == "HOLD")
        exit_event = next(event for event in events if event["action"] == "EXIT")
        self.assertEqual(hold["symbol"], "AAA.WA")
        self.assertEqual(exit_event["reason"], "stop_loss")
        self.assertFalse(hold["governance"]["production_decision_influence"])
        position_exp.validate_event(hold)
        position_exp.validate_event(exit_event)

    def test_risk_review_reason_is_preserved_for_hold(self):
        state = {
            "updated_at": "2026-09-16T16:00:00+00:00",
            "markets": {
                "GPW": {
                    "open_positions": [
                        {
                            "position_id": "gpw:1:AAA.WA",
                            "market": "GPW",
                            "symbol": "AAA.WA",
                            "last_reviewed_at": "2026-09-16T15:00:00+00:00",
                            "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED",
                            "risk_reviews": [{"reviewed_at": "2026-09-16T15:00:00+00:00", "status": "updated"}],
                        }
                    ],
                    "closed_positions": [],
                },
                "US": {"open_positions": [], "closed_positions": []},
            },
        }
        event = position_exp.events_from_portfolio_state(state)[0]
        self.assertEqual(event["action"], "HOLD")
        self.assertEqual(event["reason"], "risk_reviewed")
        self.assertEqual(event["position_state"]["latest_risk_review"]["status"], "updated")

    def test_tamper_is_detected(self):
        event = position_exp.make_event(
            market="US",
            position={
                "position_id": "us:1:ABC",
                "symbol": "ABC",
                "market": "US",
                "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED",
            },
            action="HOLD",
            decision_at="2026-09-16T15:00:00-04:00",
            portfolio_updated_at="2026-09-16T19:00:00Z",
            reason="model_hold",
            recorded_at="2026-09-16T19:00:01Z",
        )
        event["reason"] = "tampered"
        with self.assertRaises(Exception):
            position_exp.validate_event(event)


if __name__ == "__main__":
    unittest.main()
