import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "trading_notification_events",
    ROOT / "scripts" / "build_trading_notification_events.py",
)
mod = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(mod)


class TradingNotificationEventsTest(unittest.TestCase):
    def test_first_run_emits_nothing(self):
        previous = {"initialized": False, "engines": {}}
        current = {
            "initialized": True,
            "engines": {
                "daily": {"open_positions": [{"position_id": "d1"}]},
                "weekly": {"open_positions": []},
                "stock": {"open_positions": []},
            },
        }
        self.assertEqual(mod.build_events(previous, current), [])

    def test_open_then_close_transition(self):
        empty = {
            "initialized": True,
            "engines": {
                "daily": {"open_positions": []},
                "weekly": {"open_positions": []},
                "stock": {"open_positions": []},
            },
        }
        opened = json.loads(json.dumps(empty))
        opened["engines"]["daily"]["open_positions"] = [{
            "engine": "daily",
            "position_id": "eurusd:1",
            "instrument": "EUR/USD",
            "direction": "SHORT",
            "opened_at": "2026-10-01T10:00:00Z",
            "entry": 1.13,
        }]
        ev_open = mod.build_events(empty, opened)
        self.assertEqual(len(ev_open), 1)
        self.assertEqual(ev_open[0]["event_type"], "OPEN")
        self.assertEqual(ev_open[0]["position_id"], "eurusd:1")

        self.assertEqual(mod.build_events(opened, opened), [])

        ev_close = mod.build_events(opened, empty)
        self.assertEqual(len(ev_close), 1)
        self.assertEqual(ev_close[0]["event_type"], "CLOSE")
        self.assertEqual(ev_close[0]["position_id"], "eurusd:1")
        self.assertNotEqual(ev_open[0]["event_id"], ev_close[0]["event_id"])

    def test_ui_is_enabled_on_all_trading_pages(self):
        pages = [
            "pl/inwestycje/daily-trading.html",
            "pl/inwestycje/pozycje-tygodniowe.html",
            "pl/inwestycje/stock-trading.html",
            "en/investing/daily-trading.html",
            "en/investing/open-weekly-positions.html",
            "en/investing/stock-trading.html",
        ]
        for rel in pages:
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("/assets/trading-notifications.css?v=1", text, rel)
            self.assertIn("/scripts/trading-notifications.js?v=1", text, rel)


if __name__ == "__main__":
    unittest.main()
