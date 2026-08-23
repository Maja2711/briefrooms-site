import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.learning_ledger import read_events, verify_chain
from scripts.learning_outcome_loop import (
    ACTIVATION_FILENAME,
    LEDGER_FILENAME,
    integration_safety_controls,
    sync_all,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class LearningOutcomeLoopTests(unittest.TestCase):
    def _dirs(self, tmp: str):
        root = Path(tmp)
        state = root / "state"
        investments = root / "investments"
        investments.mkdir(parents=True)
        write_json(investments / "eurusd_daily_spot.json", {})
        write_json(investments / "eurusd_daily_history.json", {"trades": []})
        return state, investments

    def test_bootstrap_sets_prospective_boundary_and_no_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, investments = self._dirs(tmp)
            hist = investments / "gpw_daily_pick_history"
            write_json(hist / "2026-08-22.json", {
                "date": "2026-08-22",
                "generated_at": "2026-08-22T09:15:00+02:00",
                "decision": "TRANSAKCJA",
                "selection": {"symbol": "PKO.WA", "score": 75.0},
                "outcome": {"status": "PENDING"},
            })
            result = sync_all(
                state,
                belief_state_path=None,
                investments_dir=investments,
                now=datetime(2026, 8, 23, 8, 30, tzinfo=timezone.utc),
                bootstrap=True,
            )
            self.assertEqual(0, result["events_after"])
            activation = json.loads((state / ACTIVATION_FILENAME).read_text())
            self.assertEqual("2026-08-23T08:30:00Z", activation["activated_at"])
            self.assertTrue(verify_chain(state / LEDGER_FILENAME)["ok"])

    def test_gpw_decision_then_later_outcome_is_prospective(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, investments = self._dirs(tmp)
            hist = investments / "gpw_daily_pick_history"
            day = hist / "2026-08-24.json"
            base = {
                "date": "2026-08-24",
                "generated_at": "2026-08-24T09:20:00+02:00",
                "decision": "TRANSAKCJA",
                "policy_version": "gpw-test-v1",
                "selection": {
                    "symbol": "PKO.WA", "score": 76.0, "reference_price": 100.0,
                    "entry_zone": [99.5, 100.5], "stop": 97.0, "target": 105.4, "reward_risk": 1.8,
                },
                "outcome": {"status": "PENDING"},
            }
            write_json(day, base)
            sync_all(
                state, belief_state_path=None, investments_dir=investments,
                now=datetime(2026, 8, 24, 6, 0, tzinfo=timezone.utc), bootstrap=True,
            )
            events = read_events(state / LEDGER_FILENAME)
            self.assertEqual(["decision"], [x["event_type"] for x in events])

            resolved = dict(base)
            resolved["outcome"] = {
                "status": "RESOLVED", "activated": True,
                "entry_price": 100.0, "exit_price": 105.4,
                "exit_reason": "target", "return_percent": 5.0,
                "r_multiple": 1.8, "resolved_at": "2026-08-24T14:00:00+02:00",
            }
            write_json(day, resolved)
            sync_all(
                state, belief_state_path=None, investments_dir=investments,
                now=datetime(2026, 8, 24, 12, 5, tzinfo=timezone.utc), bootstrap=False,
            )
            events = read_events(state / LEDGER_FILENAME)
            self.assertEqual(["decision", "outcome"], [x["event_type"] for x in events])
            self.assertEqual("gpw:2026-08-24:PKO.WA", events[-1]["subject_id"])

    def test_resolved_trade_first_seen_after_outcome_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, investments = self._dirs(tmp)
            sync_all(
                state, belief_state_path=None, investments_dir=investments,
                now=datetime(2026, 8, 24, 6, 0, tzinfo=timezone.utc), bootstrap=True,
            )
            write_json(investments / "gpw_daily_pick_history" / "2026-08-24.json", {
                "date": "2026-08-24",
                "generated_at": "2026-08-24T09:20:00+02:00",
                "decision": "TRANSAKCJA",
                "selection": {"symbol": "PKO.WA", "score": 76.0},
                "outcome": {
                    "status": "RESOLVED", "activated": True, "return_percent": 2.0,
                    "r_multiple": 1.0, "resolved_at": "2026-08-24T13:00:00+02:00",
                },
            })
            result = sync_all(
                state, belief_state_path=None, investments_dir=investments,
                now=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc), bootstrap=False,
            )
            self.assertEqual([], read_events(state / LEDGER_FILENAME))
            self.assertGreaterEqual(result["sources"]["gpw"]["skipped_hindsight"], 1)

    def test_eurusd_open_decision_then_closed_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, investments = self._dirs(tmp)
            sync_all(
                state, belief_state_path=None, investments_dir=investments,
                now=datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc), bootstrap=True,
            )
            write_json(investments / "eurusd_daily_spot.json", {
                "instrument": "EUR/USD", "direction": "LONG", "decision_mode": "WITHOUT",
                "engine_version": "eurusd-test-v1",
                "metadata": {"position": {
                    "trade_id": "eurusd:test:LONG", "status": "OPEN", "direction": "LONG",
                    "opened_at": "2026-08-24T08:05:00Z", "expires_at": "2026-08-25T08:05:00Z",
                    "entry": 1.17, "stop": 1.167, "target": 1.1754,
                    "entry_score": 62.0, "entry_confidence": 0.3,
                    "entry_weights": {"trend": 0.5}, "engine_version": "eurusd-test-v1",
                }},
            })
            sync_all(
                state, belief_state_path=None, investments_dir=investments,
                now=datetime(2026, 8, 24, 8, 6, tzinfo=timezone.utc), bootstrap=False,
            )
            self.assertEqual("decision", read_events(state / LEDGER_FILENAME)[0]["event_type"])

            write_json(investments / "eurusd_daily_spot.json", {})
            write_json(investments / "eurusd_daily_history.json", {"trades": [{
                "trade_id": "eurusd:test:LONG", "instrument": "EUR/USD", "direction": "LONG",
                "opened_at": "2026-08-24T08:05:00Z", "closed_at": "2026-08-24T10:00:00Z",
                "entry": 1.17, "exit_price": 1.1754, "exit_reason": "TAKE_PROFIT",
                "result_percent": 0.46, "return_fraction": 0.0046, "r_multiple": 1.8,
                "outcome": "WIN", "engine_version": "eurusd-test-v1",
            }]})
            sync_all(
                state, belief_state_path=None, investments_dir=investments,
                now=datetime(2026, 8, 24, 10, 1, tzinfo=timezone.utc), bootstrap=False,
            )
            events = read_events(state / LEDGER_FILENAME)
            self.assertEqual(["decision", "outcome"], [x["event_type"] for x in events])

    def test_belief_forecast_requires_prior_cycle_before_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, investments = self._dirs(tmp)
            belief = Path(tmp) / "belief-state.json"
            sync_all(
                state, belief_state_path=belief, investments_dir=investments,
                now=datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc), bootstrap=True,
            )
            forecast = {
                "forecast_id": "forecast-1", "forecast_set_id": "set-1", "belief_id": "spx.trend.bullish",
                "predicted_probability": 0.7, "forecast_confidence": 0.8,
                "forecast_at": "2026-08-24T08:05:00Z", "target_at": "2026-08-25T08:05:00Z",
                "horizon_hours": 24.0, "domain": "market", "entity": "SPX", "regime": "normal",
                "outcome_rule": "test", "representative_evidence_ids": ["ev-1"],
            }
            write_json(belief, {"forecasts": [forecast], "verifications": []})
            sync_all(
                state, belief_state_path=belief, investments_dir=investments,
                now=datetime(2026, 8, 24, 8, 6, tzinfo=timezone.utc), bootstrap=False,
            )
            self.assertEqual(["forecast"], [x["event_type"] for x in read_events(state / LEDGER_FILENAME)])

            verification = {
                "verification_id": "verify-1", "forecast_id": "forecast-1", "belief_id": "spx.trend.bullish",
                "predicted_probability": 0.7, "forecast_confidence": 0.8, "outcome": True,
                "target_at": "2026-08-25T08:05:00Z", "verified_at": "2026-08-25T08:10:00Z",
                "horizon_hours": 24.0, "domain": "market", "entity": "SPX", "regime": "normal",
                "brier_score": 0.09, "log_loss": 0.356675, "calibration_eligible": True,
                "outcome_source": "official_close", "outcome_ref": "market://SPX/close",
            }
            write_json(belief, {"forecasts": [forecast], "verifications": [verification]})
            sync_all(
                state, belief_state_path=belief, investments_dir=investments,
                now=datetime(2026, 8, 25, 8, 11, tzinfo=timezone.utc), bootstrap=False,
            )
            self.assertEqual(
                ["forecast", "outcome", "verification"],
                [x["event_type"] for x in read_events(state / LEDGER_FILENAME)],
            )

    def test_rerun_is_idempotent_and_zero_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, investments = self._dirs(tmp)
            write_json(investments / "gpw_daily_pick.json", {
                "date": "2026-08-24", "generated_at": "2026-08-24T09:20:00+02:00",
                "decision": "BRAK_TRANSAKCJI", "reason": "no candidate", "selection": None,
            })
            first = sync_all(
                state, belief_state_path=None, investments_dir=investments,
                now=datetime(2026, 8, 24, 6, 0, tzinfo=timezone.utc), bootstrap=True,
            )
            second = sync_all(
                state, belief_state_path=None, investments_dir=investments,
                now=datetime(2026, 8, 24, 7, 30, tzinfo=timezone.utc), bootstrap=False,
            )
            self.assertEqual(first["events_after"], second["events_after"])
            self.assertTrue(all(value is False for value in integration_safety_controls().values()))
            self.assertTrue(verify_chain(state / LEDGER_FILENAME)["ok"])


if __name__ == "__main__":
    unittest.main()
