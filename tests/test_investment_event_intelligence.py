import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import investment_event_intelligence as event

UTC = timezone.utc


def synthetic_event(*, pressure=1.0, strength=0.9, confidence=0.95, event_id="evt-test"):
    return {
        "event_id": event_id,
        "title": "President ordered military strike after escalation",
        "published_at": "2026-09-16T10:00:00Z",
        "source": "Reuters",
        "source_ref": "https://example.test/event",
        "confidence": confidence,
        "action_type": "executed_action",
        "event_type": "escalation" if pressure > 0 else "deescalation",
        "scenario_tags": ["middle_east"],
        "event_strength": strength,
        "pressure": pressure,
    }


class InvestmentEventIntelligenceTests(unittest.TestCase):
    def test_executed_leader_escalation_is_classified_stronger_than_rhetoric(self):
        executed, executed_type = event.action_strength("President ordered and launched strikes")
        rhetoric, rhetoric_type = event.action_strength("President warned strikes could follow")
        self.assertEqual("executed_action", executed_type)
        self.assertEqual("rhetoric_or_warning", rhetoric_type)
        self.assertGreater(executed, rhetoric)

    def test_ceasefire_is_deescalation(self):
        pressure, severity, event_type = event.event_pressure("Prime minister announced ceasefire agreement")
        self.assertEqual(-1.0, pressure)
        self.assertEqual("deescalation", event_type)
        self.assertGreaterEqual(severity, 0.9)

    def test_escalation_is_negative_for_sp500_eurusd_and_btc(self):
        events = [synthetic_event()]
        for target_id in ("sp500_futures", "eurusd", "btcusd"):
            score = event.score_target({"target_id": target_id, "symbol": "X", "market": "MULTI_ASSET"}, events)
            self.assertLess(score["normalized_impact"], 0.0, target_id)

    def test_deescalation_reverses_risk_asset_direction(self):
        score = event.score_target(
            {"target_id": "sp500_futures", "symbol": "ES=F", "market": "MULTI_ASSET"},
            [synthetic_event(pressure=-1.0)],
        )
        self.assertGreater(score["normalized_impact"], 0.0)
        self.assertEqual("SUPPORTIVE", score["decision_overlay"])

    def test_sector_mapping_is_not_one_size_fits_all(self):
        events = [synthetic_event()]
        defense = event.score_target(
            {"target_id": "stock:US:LMT", "symbol": "LMT", "market": "US", "sector": "Aerospace & Defense"},
            events,
        )
        airline = event.score_target(
            {"target_id": "stock:US:UAL", "symbol": "UAL", "market": "US", "sector": "Airlines"},
            events,
        )
        self.assertGreater(defense["normalized_impact"], 0.0)
        self.assertLess(airline["normalized_impact"], 0.0)
        self.assertTrue(airline["entry_blocked"])

    def test_rhetoric_alone_does_not_reach_close_threshold(self):
        weak = synthetic_event(strength=0.35, confidence=0.62)
        score = event.score_target(
            {"target_id": "sp500_futures", "symbol": "ES=F", "market": "MULTI_ASSET"},
            [weak],
        )
        self.assertNotEqual("CLOSE", score["decision_overlay"])

    def test_high_confidence_sp500_escalation_can_close(self):
        score = event.score_target(
            {"target_id": "sp500_futures", "symbol": "ES=F", "market": "MULTI_ASSET"},
            [synthetic_event(strength=0.92, confidence=0.95)],
        )
        self.assertEqual("CLOSE", score["decision_overlay"])
        self.assertGreaterEqual(score["confidence"], event.MIN_CLOSE_CONFIDENCE)

    def test_weekly_close_request_uses_existing_material_event_contract_and_deduplicates(self):
        week = {
            "week_id": "2026-W38",
            "instruments": [
                {
                    "instrument_id": "sp500_futures",
                    "direction": "long",
                    "entry_price": 6500.0,
                    "exit_price": None,
                }
            ],
        }
        scores = {
            "sp500_futures": {
                "decision_overlay": "CLOSE",
                "normalized_impact": -0.85,
                "score_delta": -17.0,
                "confidence": 0.94,
                "top_events": [synthetic_event()],
            }
        }
        now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event_exit_requests.json"
            with patch.object(event, "EVENT_REQUESTS_PATH", path):
                first = event.apply_weekly_requests(now, week, scores)
                second = event.apply_weekly_requests(now, week, scores)
                payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(first))
        self.assertEqual([], second)
        self.assertEqual("pending", payload["requests"][0]["status"])
        self.assertEqual("close_only", payload["requests"][0]["action"])
        self.assertEqual("sp500_futures", payload["requests"][0]["instrument_id"])

    def test_candidate_gate_blocks_before_canonical_admission(self):
        payload = {
            "date": "2026-09-16",
            "generated_at": "2026-09-16T12:00:00Z",
            "decision": "TRADE",
            "selection": {"symbol": "UAL", "ticker": "UAL", "sector": "Airlines"},
        }
        state = {
            "markets": {
                "US": {
                    "open_positions": [],
                    "closed_positions": [],
                    "last_candidate_key": None,
                    "last_candidate_decision": None,
                    "last_candidate_reason": None,
                }
            }
        }
        target = {
            "target_id": "candidate:US:UAL",
            "symbol": "UAL",
            "market": "US",
            "sector": "Airlines",
            "candidate_payload": payload,
        }
        scores = {
            "candidate:US:UAL": {
                "entry_blocked": True,
                "normalized_impact": -0.8,
                "score_delta": -16.0,
                "confidence": 0.93,
                "top_events": [synthetic_event()],
            }
        }
        actions = event.apply_candidate_blocks(state, [target], scores, datetime(2026, 9, 16, 12, 0, tzinfo=UTC))
        self.assertEqual("BLOCK_OPEN", actions[0]["action"])
        self.assertEqual("CASH", state["markets"]["US"]["last_candidate_decision"])
        self.assertEqual("event_intelligence_entry_block", state["markets"]["US"]["last_candidate_reason"])


if __name__ == "__main__":
    unittest.main()
