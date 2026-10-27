from __future__ import annotations

import unittest
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path

from scripts import stock_trading_v2_position_experience as position_exp
from scripts import stock_trading_v2_contracts as contracts


class StockTradingV2PositionExperienceTests(unittest.TestCase):
    def test_historical_files_remain_byte_identical_across_later_reads(self):
        fixture_data = Path(os.environ.get("STOCK_TRADING_LEGACY_FIXTURE_ROOT", position_exp.DEFAULT_ROOT.parent))
        historical = list(position_exp.iter_files(fixture_data / position_exp.DEFAULT_ROOT.name))
        if not historical:
            self.skipTest("Legacy position snapshot unavailable; set STOCK_TRADING_LEGACY_FIXTURE_ROOT to the read-only research data/investments snapshot")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "positions"
            before = {}
            for source in historical:
                row = json.loads(source.read_text(encoding="utf-8"))
                path = position_exp.event_path(root, row)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(source.read_bytes())
                before[path] = source.read_bytes()
                later = deepcopy(row)
                later["portfolio_updated_at"] = "2026-10-24T11:00:00Z"
                later["recorded_at"] = "2026-10-24T11:00:01Z"
                later.pop("event_sha256")
                later["event_sha256"] = contracts.payload_sha256(later)
                self.assertFalse(position_exp.persist(root, later))
                self.assertFalse(position_exp.persist(root, later))
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            result = position_exp.verify(root)
            self.assertEqual(result["count"], len(historical))
            self.assertEqual(result["read_observations"], len(historical))

    def test_later_portfolio_metadata_is_separate_and_actual_changes_conflict(self):
        original = position_exp.make_event(market="US", position={"position_id": "p1", "symbol": "TST", "stop": 90},
            action="HOLD", reason="model_hold", decision_at="2026-10-23T15:00:00Z", portfolio_updated_at="2026-10-23T15:01:00Z", recorded_at="2026-10-23T15:01:01Z")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "positions"
            self.assertTrue(position_exp.persist(root, original))
            decision_path = position_exp.event_path(root, original)
            original_bytes = decision_path.read_bytes()
            observation_root = position_exp.observation_root_for(root)
            original_reads = {path: path.read_bytes() for path in observation_root.rglob("*.json")}
            later = deepcopy(original)
            later["portfolio_updated_at"] = "2026-10-24T10:00:00Z"
            later["recorded_at"] = "2026-10-24T10:00:01Z"
            later.pop("event_sha256")
            later["event_sha256"] = contracts.payload_sha256(later)
            self.assertFalse(position_exp.persist(root, later))
            self.assertFalse(position_exp.persist(root, later))
            self.assertEqual(decision_path.read_bytes(), original_bytes)
            self.assertEqual({path: path.read_bytes() for path in original_reads}, original_reads)
            self.assertEqual(position_exp.verify(root)["read_observations"], 2)
            for field, value in (("reason", "different_decision"), ("position_state", {"position_id": "p1", "symbol": "TST", "stop": 91})):
                changed = deepcopy(later)
                changed[field] = value
                changed.pop("event_sha256")
                changed["event_sha256"] = contracts.payload_sha256(changed)
                with self.assertRaises(position_exp.ImmutablePositionExperienceConflict):
                    position_exp.persist(root, changed)
                self.assertEqual(decision_path.read_bytes(), original_bytes)

    def test_companion_observation_hash_and_source_binding_are_checked(self):
        row = position_exp.make_event(market="GPW", position={"position_id": "p", "symbol": "TST.WA"}, action="EXIT",
            reason="stop", decision_at="2026-10-23T10:00:00Z", portfolio_updated_at="2026-10-23T11:00:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "positions"
            position_exp.persist(root, row)
            path = next(position_exp.observation_root_for(root).rglob("*.json"))
            observation = json.loads(path.read_text(encoding="utf-8"))
            observation["source_event_sha256"] = "forged"
            identity = {key: observation.get(key) for key in ("source_event_id", "source_event_sha256", "portfolio_updated_at")}
            observation["observation_id"] = "stposreadv2-" + contracts.payload_sha256(identity)[:24]
            observation.pop("observation_sha256")
            observation["observation_sha256"] = contracts.payload_sha256(observation)
            path.write_text(json.dumps(observation), encoding="utf-8")
            with self.assertRaisesRegex(contracts.ContractError, "source hash"):
                position_exp.verify(root)

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
