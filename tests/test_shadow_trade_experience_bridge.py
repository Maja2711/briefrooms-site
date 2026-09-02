import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.experience_store_multi_source import materialize
from scripts.learning_ledger import append_event, read_events, verify_chain
from scripts.shadow_trade_experience_bridge import (
    ACTIVATION_FILENAME,
    LEDGER_FILENAME,
    safety_controls,
    sync_eurusd_abc,
    verify_state,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def arm(direction: str, available: bool = True) -> dict:
    return {
        "arm_id": "A",
        "label": "TEST",
        "available": available,
        "direction": direction if available else "UNAVAILABLE",
        "score": 65.0 if direction == "LONG" else 50.0,
        "confidence": 0.3 if direction in {"LONG", "SHORT"} else 0.0,
        "decision_influence": False,
    }


def capture(capture_id: str, captured_at: str, direction: str, path_status: str = "OPEN", *, resolved_24h: bool = False) -> dict:
    available = direction != "UNAVAILABLE"
    plan_status = "NO_TRADE" if direction == "FLAT" else ("TRACKED" if available else "UNAVAILABLE")
    path_arm = {
        "status": path_status,
        "mfe_bps": 18.0,
        "mae_bps": -7.0,
        "first_touch": None,
        "first_touch_at": None,
        "minutes_to_first_touch": None,
        "exit_reason": None,
        "exit_at": None,
        "exit_price": None,
        "realized_bps": None,
    }
    if path_status == "CLOSED":
        path_arm.update({
            "first_touch": "TAKE_PROFIT",
            "first_touch_at": "2026-09-03T09:00:00Z",
            "minutes_to_first_touch": 55.0,
            "exit_reason": "TAKE_PROFIT",
            "exit_at": "2026-09-03T09:00:00Z",
            "exit_price": 1.1054,
            "realized_bps": 49.0,
        })
    horizons = {
        "1440m": {
            "minutes": 1440,
            "target_at": "2026-09-04T08:05:00Z",
            "outcome": None,
        }
    }
    if resolved_24h:
        horizons["1440m"]["outcome"] = {
            "resolved_at": "2026-09-04T08:30:00Z",
            "price": 1.1020,
            "raw_return_bps": 20.0,
            "cost_adjusted": False,
            "arms": {},
        }
    return {
        "capture_id": capture_id,
        "engine_version": "eurusd-daily-abc-v1.3.0",
        "mode": "research_shadow",
        "captured_at": captured_at,
        "market_observed_at": captured_at,
        "reference_price": 1.1000,
        "decision_sha256": "decision-test",
        "trade_plan_sha256": "plan-test",
        "arms": {
            "A": arm(direction, available),
            "B": arm("UNAVAILABLE", False),
            "C": arm("UNAVAILABLE", False),
        },
        "horizons": horizons,
        "trade_plan": {
            "signal_generated_at": captured_at,
            "horizon_end_at": "2026-09-04T08:05:00Z",
            "risk_contract": {"risk_distance": 0.0030, "reward_risk": 1.8},
            "arms": {
                "A": {
                    "available": available,
                    "direction": direction,
                    "status": plan_status,
                    "entry_price": 1.1000 if direction in {"LONG", "SHORT"} else None,
                    "stop_price": 1.0970 if direction == "LONG" else (1.1030 if direction == "SHORT" else None),
                    "target_price": 1.1054 if direction == "LONG" else (1.0946 if direction == "SHORT" else None),
                },
                "B": {"available": False, "direction": "UNAVAILABLE", "status": "UNAVAILABLE"},
                "C": {"available": False, "direction": "UNAVAILABLE", "status": "UNAVAILABLE"},
            },
        },
        "trade_path": {
            "arms": {
                "A": path_arm if direction != "FLAT" else {"status": "NO_TRADE"},
                "B": {"status": "UNAVAILABLE"},
                "C": {"status": "UNAVAILABLE"},
            }
        },
        "research_boundary": {
            "historical_backfill": False,
            "decision_influence": False,
            "trade_execution": False,
            "belief_writeback": False,
        },
    }


class ShadowTradeBridgeTests(unittest.TestCase):
    def test_bootstrap_creates_strict_zero_authority_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "abc.json"
            write_json(source, {"mode": "research_shadow", "engine_version": "eurusd-daily-abc-v1.3.0", "captures": []})
            result = sync_eurusd_abc(
                root / "state", source,
                now=datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc), bootstrap=True,
            )
            self.assertEqual(0, result["events_after"])
            activation = json.loads((root / "state" / ACTIVATION_FILENAME).read_text())
            self.assertEqual("2026-09-03T08:00:00Z", activation["activated_at"])
            self.assertTrue(all(value is False for value in safety_controls().values()))
            self.assertTrue(verify_state(root / "state")["ok"])

    def test_open_shadow_decision_then_later_trade_outcome_is_prospective(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            source = root / "abc.json"
            write_json(source, {"mode": "research_shadow", "engine_version": "eurusd-daily-abc-v1.3.0", "captures": []})
            sync_eurusd_abc(state, source, now=datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc), bootstrap=True)

            open_capture = capture("cap-1", "2026-09-03T08:05:00Z", "LONG", "OPEN")
            write_json(source, {"mode": "research_shadow", "engine_version": "eurusd-daily-abc-v1.3.0", "captures": [open_capture]})
            first = sync_eurusd_abc(state, source, now=datetime(2026, 9, 3, 8, 10, tzinfo=timezone.utc))
            self.assertEqual(1, first["decisions_appended"])
            self.assertEqual(["decision"], [row["event_type"] for row in read_events(state / LEDGER_FILENAME)])

            closed_capture = capture("cap-1", "2026-09-03T08:05:00Z", "LONG", "CLOSED")
            write_json(source, {"mode": "research_shadow", "engine_version": "eurusd-daily-abc-v1.3.0", "captures": [closed_capture]})
            second = sync_eurusd_abc(state, source, now=datetime(2026, 9, 3, 9, 5, tzinfo=timezone.utc))
            self.assertEqual(1, second["outcomes_appended"])
            events = read_events(state / LEDGER_FILENAME)
            self.assertEqual(["decision", "outcome"], [row["event_type"] for row in events])
            self.assertIsNone(events[-1]["payload"].get("return_fraction"))
            self.assertAlmostEqual(0.0049, events[-1]["payload"]["gross_return_fraction"])
            self.assertFalse(events[-1]["payload"]["cost_adjusted"])

    def test_first_seen_resolved_trade_is_rejected_as_hindsight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            source = root / "abc.json"
            write_json(source, {"mode": "research_shadow", "engine_version": "eurusd-daily-abc-v1.3.0", "captures": []})
            sync_eurusd_abc(state, source, now=datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc), bootstrap=True)
            write_json(source, {
                "mode": "research_shadow",
                "engine_version": "eurusd-daily-abc-v1.3.0",
                "captures": [capture("cap-2", "2026-09-03T08:05:00Z", "LONG", "CLOSED")],
            })
            result = sync_eurusd_abc(state, source, now=datetime(2026, 9, 3, 9, 5, tzinfo=timezone.utc))
            self.assertEqual([], read_events(state / LEDGER_FILENAME))
            self.assertEqual(1, result["skipped_hindsight"])

    def test_flat_decision_is_settled_only_after_24h_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            source = root / "abc.json"
            write_json(source, {"mode": "research_shadow", "engine_version": "eurusd-daily-abc-v1.3.0", "captures": []})
            sync_eurusd_abc(state, source, now=datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc), bootstrap=True)
            flat = capture("cap-flat", "2026-09-03T08:05:00Z", "FLAT", "NO_TRADE", resolved_24h=False)
            write_json(source, {"mode": "research_shadow", "engine_version": "eurusd-daily-abc-v1.3.0", "captures": [flat]})
            sync_eurusd_abc(state, source, now=datetime(2026, 9, 3, 8, 10, tzinfo=timezone.utc))
            self.assertEqual(["decision"], [row["event_type"] for row in read_events(state / LEDGER_FILENAME)])

            flat_done = capture("cap-flat", "2026-09-03T08:05:00Z", "FLAT", "NO_TRADE", resolved_24h=True)
            write_json(source, {"mode": "research_shadow", "engine_version": "eurusd-daily-abc-v1.3.0", "captures": [flat_done]})
            sync_eurusd_abc(state, source, now=datetime(2026, 9, 4, 8, 31, tzinfo=timezone.utc))
            events = read_events(state / LEDGER_FILENAME)
            self.assertEqual(["decision", "outcome"], [row["event_type"] for row in events])
            self.assertEqual(0.0, events[-1]["payload"]["return_fraction"])
            self.assertEqual(0.002, events[-1]["payload"]["market_return_fraction"])

    def test_multi_source_store_combines_core_and_shadow_ledgers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = root / "core.jsonl"
            shadow = root / "shadow.jsonl"
            append_event(
                core,
                event_type="decision", occurred_at="2026-09-03T08:00:00Z",
                subject_id="core-1", source_ref="core://1",
                payload={"engine": "core-test", "instrument": "TEST", "action": "LONG"},
            )
            append_event(
                core,
                event_type="outcome", occurred_at="2026-09-03T09:00:00Z",
                subject_id="core-1", source_ref="core://outcome/1",
                payload={"status": "RESOLVED", "return_fraction": 0.01},
            )
            append_event(
                shadow,
                event_type="decision", occurred_at="2026-09-03T08:05:00Z",
                subject_id="shadow-1", source_ref="shadow://1",
                payload={"engine": "eurusd-abc-a", "instrument": "EUR/USD", "action": "FLAT"},
            )
            append_event(
                shadow,
                event_type="outcome", occurred_at="2026-09-04T08:30:00Z",
                subject_id="shadow-1", source_ref="shadow://outcome/1",
                payload={"status": "RESOLVED", "return_fraction": 0.0},
            )
            store = root / "experience.jsonl"
            status = root / "status.json"
            result = materialize([core, shadow], store, status)
            self.assertEqual(2, result["experience_count"])
            self.assertEqual(2, result["settled_count"])
            self.assertEqual(2, len(result["source_ledgers"]))
            self.assertTrue(verify_chain(core)["ok"])
            self.assertTrue(verify_chain(shadow)["ok"])


if __name__ == "__main__":
    unittest.main()
