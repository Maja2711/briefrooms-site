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

        original_data = mod.DATA
        with tempfile.TemporaryDirectory() as td:
            try:
                mod.DATA = Path(td)
                (mod.DATA / "eurusd_daily_history.json").write_text(json.dumps({
                    "trades": [{
                        "trade_id": "eurusd:1",
                        "direction": "SHORT",
                        "entry": 1.13,
                        "opened_at": "2026-10-01T10:00:00Z",
                        "exit_price": 1.12,
                        "exit_reason": "TAKE_PROFIT",
                        "closed_at": "2026-10-01T12:00:00Z",
                    }]
                }), encoding="utf-8")
                ev_close = mod.build_events(opened, empty)
            finally:
                mod.DATA = original_data
        self.assertEqual(len(ev_close), 1)
        self.assertEqual(ev_close[0]["event_type"], "CLOSE")
        self.assertEqual(ev_close[0]["position_id"], "eurusd:1")
        self.assertEqual(ev_close[0]["closed_at"], "2026-10-01T12:00:00Z")
        self.assertNotEqual(ev_open[0]["event_id"], ev_close[0]["event_id"])


    def test_recovery_close_requires_canonical_close_metadata(self):
        previous = {
            "initialized": True,
            "engines": {
                "daily": {"open_positions": []},
                "weekly": {"open_positions": [{
                    "engine": "weekly",
                    "position_id": "2026-W41:eurusd:2026-10-05T08:00:00Z",
                    "instrument": "EUR/USD",
                    "direction": "SHORT",
                    "opened_at": "2026-10-05T08:00:00Z",
                    "entry": 1.12,
                }]},
                "stock": {"open_positions": []},
            },
        }
        current = {
            "initialized": True,
            "engines": {
                "daily": {"open_positions": []},
                "weekly": {"open_positions": []},
                "stock": {"open_positions": []},
            },
        }
        original_data = mod.DATA
        with tempfile.TemporaryDirectory() as td:
            try:
                mod.DATA = Path(td)
                (mod.DATA / "weekly").mkdir(parents=True)
                self.assertEqual(mod.build_events(previous, current), [])
            finally:
                mod.DATA = original_data

    def test_weekly_pending_price_plan_is_not_open_position(self):
        original_data = mod.DATA
        with tempfile.TemporaryDirectory() as td:
            try:
                mod.DATA = Path(td)
                weekly = mod.DATA / "weekly"
                weekly.mkdir(parents=True)
                (weekly / "2026-W41.json").write_text(json.dumps({
                    "week_id": "2026-W41",
                    "instruments": [{
                        "instrument_id": "eurusd",
                        "label_pl": "EUR/USD",
                        "trade_status": "pending",
                        "direction": "short",
                        "entry_price": 1.12,
                        "exit_price": None,
                        "pending_entry_decision": {
                            "entry_price_plan": {"target_price": 1.12}
                        },
                    }],
                }), encoding="utf-8")
                self.assertEqual(mod.weekly_open_positions(), [])
            finally:
                mod.DATA = original_data

    def test_recovery_weekly_close_includes_exit_metadata(self):
        original_data = mod.DATA
        with tempfile.TemporaryDirectory() as td:
            try:
                mod.DATA = Path(td)
                weekly = mod.DATA / "weekly"
                weekly.mkdir(parents=True)
                payload = {
                    "week_id": "2026-W41",
                    "instruments": [{
                        "instrument_id": "btcusd",
                        "label_pl": "BTC/USD",
                        "trade_status": "closed",
                        "direction": "long",
                        "entry_price": 120000,
                        "entry_captured_at": "2026-10-05T08:00:00Z",
                        "exit_price": 125000,
                        "exit_reason": "TAKE_PROFIT",
                        "exit_captured_at": "2026-10-05T10:00:00Z",
                    }],
                }
                (weekly / "2026-W41.json").write_text(json.dumps(payload), encoding="utf-8")
                pid = mod.weekly_position_id(payload, payload["instruments"][0])
                event = mod.make_event("weekly", "CLOSE", {
                    "position_id": pid,
                    "instrument": "BTC/USD",
                    "direction": "LONG",
                    "opened_at": "2026-10-05T08:00:00Z",
                    "entry": 120000,
                })
                self.assertEqual(event["exit_reason"], "TAKE_PROFIT")
                self.assertEqual(event["exit_price"], 125000)
                self.assertEqual(event["closed_at"], "2026-10-05T10:00:00Z")
            finally:
                mod.DATA = original_data

    def test_recovery_stock_close_includes_exit_metadata(self):
        original_data = mod.DATA
        with tempfile.TemporaryDirectory() as td:
            try:
                mod.DATA = Path(td)
                (mod.DATA / "stock_trading_portfolio.json").write_text(json.dumps({
                    "markets": {
                        "US": {
                            "closed_positions": [{
                                "position_id": "us:a:AAPL",
                                "ticker": "AAPL",
                                "direction": "LONG",
                                "entry": 250,
                                "exit_price": 255,
                                "exit_reason": "TAKE_PROFIT",
                                "closed_at": "2026-10-05T10:00:00Z",
                            }]
                        }
                    }
                }), encoding="utf-8")
                event = mod.make_event("stock", "CLOSE", {
                    "position_id": "us:a:AAPL",
                    "instrument": "AAPL",
                    "market": "US",
                    "direction": "LONG",
                    "opened_at": "2026-10-05T08:00:00Z",
                    "entry": 250,
                })
                self.assertEqual(event["exit_reason"], "TAKE_PROFIT")
                self.assertEqual(event["exit_price"], 255)
                self.assertEqual(event["closed_at"], "2026-10-05T10:00:00Z")
            finally:
                mod.DATA = original_data

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
            self.assertIn("/assets/trading-notifications.css", text, rel)
            self.assertIn("/scripts/trading-notifications.js", text, rel)


if __name__ == "__main__":
    unittest.main()
