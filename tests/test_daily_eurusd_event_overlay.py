import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from belief_market_data_adapter import Bar
import daily_eurusd_event_overlay as overlay
import investment_event_engine_profiles as profiles

UTC = timezone.utc


def events(pressure=1.0):
    rows = []
    for idx, strength in enumerate((0.98, 0.95, 0.90), start=1):
        rows.append({
            "event_id": f"evt-{idx}",
            "title": f"President ordered military strike {idx}" if pressure > 0 else f"Government signed ceasefire agreement {idx}",
            "published_at": "2026-09-16T11:30:00Z",
            "source": "Reuters",
            "source_ref": f"https://example.test/{idx}",
            "confidence": 0.95,
            "action_type": "executed_action",
            "event_type": "escalation" if pressure > 0 else "deescalation",
            "scenario_tags": ["middle_east"],
            "event_strength": strength,
            "pressure": pressure,
        })
    return rows


def write_snapshot(path: Path, generated_at: datetime, rows):
    path.write_text(json.dumps({
        "schema_version": "investment-event-intelligence-v1",
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "status": "healthy",
        "events": rows,
    }), encoding="utf-8")


class DailyEventOverlayTests(unittest.TestCase):
    def test_fresh_snapshot_uses_daily_profile_and_can_block_long_entry(self):
        now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            write_snapshot(path, now - timedelta(minutes=5), events())
            context = overlay.build_context(now, path)
        self.assertEqual("ACTIVE", context["status"])
        self.assertTrue(context["coverage"])
        self.assertEqual(profiles.DAILY, context["score"]["engine_profile"])
        self.assertEqual(0.75, context["score"]["dominant_engine_weight"])
        self.assertTrue(overlay.entry_blocked(context, "LONG"))
        self.assertFalse(overlay.entry_blocked(context, "SHORT"))

    def test_stale_snapshot_is_fail_neutral(self):
        now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            write_snapshot(path, now - timedelta(minutes=90), events())
            context = overlay.build_context(now, path)
        self.assertEqual("EVENT_SNAPSHOT_STALE", context["status"])
        self.assertFalse(context["coverage"])
        self.assertFalse(overlay.entry_blocked(context, "LONG"))
        self.assertEqual("HOLD", overlay.position_decision(context, "LONG"))

    def test_fresh_close_event_uses_canonical_daily_trade_record(self):
        now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
        position = {
            "trade_id": "eurusd:test:LONG",
            "status": "OPEN",
            "direction": "LONG",
            "opened_at": "2026-09-16T10:00:00Z",
            "expires_at": "2026-09-17T10:00:00Z",
            "entry": 1.1000,
            "stop": 1.0900,
            "target": 1.1200,
            "entry_score": 70.0,
            "entry_confidence": 0.5,
            "entry_components": {},
            "entry_weights": {},
            "engine_version": "eurusd-daily-spot-v1.7.0",
        }
        bars = [Bar(timestamp=now - timedelta(minutes=1), close=1.1010, open=1.1008, high=1.1012, low=1.1006)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            write_snapshot(path, now - timedelta(minutes=5), events())
            trade = overlay.maybe_close_position(position, bars, now, path=path)
        self.assertIsNotNone(trade)
        self.assertEqual("EVENT_INTELLIGENCE_THESIS_INVALIDATION", trade["exit_reason"])
        self.assertEqual("EUR/USD", trade["instrument"])
        self.assertEqual(profiles.DAILY, trade["event_intelligence"]["engine_profile"])
        self.assertIn("r_multiple", trade)

    def test_positive_event_can_close_short_when_daily_threshold_is_reached(self):
        now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
        position = {
            "trade_id": "eurusd:test:SHORT",
            "status": "OPEN",
            "direction": "SHORT",
            "opened_at": "2026-09-16T10:00:00Z",
            "expires_at": "2026-09-17T10:00:00Z",
            "entry": 1.1000,
            "stop": 1.1100,
            "target": 1.0800,
            "entry_score": 30.0,
            "entry_confidence": 0.5,
            "entry_components": {},
            "entry_weights": {},
            "engine_version": "eurusd-daily-spot-v1.7.0",
        }
        bars = [Bar(timestamp=now - timedelta(minutes=1), close=1.1010, open=1.1008, high=1.1012, low=1.1006)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            write_snapshot(path, now - timedelta(minutes=5), events(pressure=-1.0))
            trade = overlay.maybe_close_position(position, bars, now, path=path)
        self.assertIsNotNone(trade)
        self.assertEqual("EVENT_INTELLIGENCE_THESIS_INVALIDATION", trade["exit_reason"])


if __name__ == "__main__":
    unittest.main()
