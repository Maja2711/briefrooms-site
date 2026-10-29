import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import stock_trading_quote_watchdog as wd


UTC = timezone.utc


def mark(price, observed, market_state="REGULAR"):
    return {
        "price": price, "observed_at": observed, "market_state": market_state,
        "provider": "Yahoo Finance chart", "received_at": observed,
    }


def position(ticker, quote, market="US"):
    return {
        "position_id": "test:" + ticker, "status": "OPEN",
        "symbol": ticker if market == "US" else ticker + ".WA",
        "ticker": ticker, "entry": 100.0, "last_mark": 101.0,
        "stop": 95.0, "target": 110.0, "current_mark": quote,
        "quantity": 50.0, "opened_at": "2026-10-27T14:00:00+00:00",
    }


class StockTradingQuoteWatchdogTests(unittest.TestCase):
    def test_recovers_mck_without_rewriting_gpw_or_other_us_position(self):
        now = datetime(2026, 10, 29, 19, 30, tzinfo=UTC)
        mck = position("MCK", mark(969.02, "2026-10-29T13:56:22Z"))
        cci = position("CCI", mark(110.25, "2026-10-29T19:29:00Z"))
        gpw = position("LPP", mark(27100, "2026-10-29T16:02:00Z"), "GPW")
        state = {"updated_at": "immutable", "markets": {
            "US": {"open_positions": [mck, cci], "quote_session": {"as_of": "original"}},
            "GPW": {"open_positions": [gpw]}}}
        source = mark(971.64, "2026-10-29T19:29:00Z")
        with patch.object(wd.provider, "quote_for_symbol", return_value=source) as fetch:
            updated, health = wd.monitor(state, now=now)
        fetch.assert_called_once_with("MCK", "US", now_utc=now)
        self.assertEqual("RECOVERED", next(p["status"] for p in health["positions"] if p["symbol"] == "MCK"))
        self.assertEqual("FRESH", next(p["status"] for p in health["positions"] if p["symbol"] == "CCI"))
        self.assertEqual("OUT_OF_SESSION", next(p["status"] for p in health["positions"] if p["symbol"] == "LPP.WA"))
        self.assertEqual(971.64, updated["markets"]["US"]["open_positions"][0]["current_mark"]["price"])
        self.assertEqual(mck["last_mark"], updated["markets"]["US"]["open_positions"][0]["last_mark"])
        self.assertEqual(mck["entry"], updated["markets"]["US"]["open_positions"][0]["entry"])
        self.assertEqual(mck["stop"], updated["markets"]["US"]["open_positions"][0]["stop"])
        self.assertEqual(mck["target"], updated["markets"]["US"]["open_positions"][0]["target"])
        self.assertEqual(cci, updated["markets"]["US"]["open_positions"][1])
        self.assertEqual(state["markets"]["GPW"], updated["markets"]["GPW"])
        self.assertEqual(state["markets"]["US"]["quote_session"], updated["markets"]["US"]["quote_session"])
        self.assertEqual("immutable", updated["updated_at"])
        self.assertEqual(state["markets"]["US"]["open_positions"][0], mck)

    def test_checks_each_open_gpw_position_and_independent_fallback(self):
        now = datetime(2026, 10, 29, 13, 30, tzinfo=UTC)
        rows = [position(ticker, mark(10, "2026-10-29T12:00:00Z"), "GPW")
                for ticker in ("LPP", "ALE", "OPL")]
        state = {"markets": {"GPW": {"open_positions": rows}, "US": {"open_positions": []}}}
        yahoo = mark(13.0, "2026-10-29T13:05:00Z")
        stooq = mark(14.0, "2026-10-29T13:29:30Z")
        stooq["provider"] = "Stooq.pl current quote"
        with patch.object(wd.provider, "quote_for_symbol", return_value=yahoo) as y, \
                patch.object(wd.provider, "_stooq_quote", return_value=stooq) as s:
            updated, health = wd.monitor(state, now=now)
        self.assertEqual(3, y.call_count)
        self.assertEqual(3, s.call_count)
        self.assertEqual(3, health["positions_recovered"])
        self.assertTrue(all(p["status"] == "RECOVERED" for p in health["positions"]))
        self.assertTrue(all(p["current_mark"]["price"] == 14.0
                            for p in updated["markets"]["GPW"]["open_positions"]))

    def test_unavailable_provider_does_not_replace_old_price(self):
        now = datetime(2026, 10, 29, 19, 30, tzinfo=UTC)
        old = position("MCK", mark(969.02, "2026-10-29T13:56:22Z"))
        state = {"markets": {"US": {"open_positions": [old]}}}
        with patch.object(wd.provider, "quote_for_symbol", side_effect=RuntimeError("no data")):
            updated, health = wd.monitor(state, now=now)
        self.assertEqual(old, updated["markets"]["US"]["open_positions"][0])
        self.assertEqual("ALERT", health["status"])
        self.assertEqual(1, health["alerts"])
        self.assertEqual("ALERT_STALE", health["positions"][0]["status"])
        self.assertTrue(health["positions"][0]["diagnostic"])

    def test_missing_time_never_masquerades_as_fresh(self):
        now = datetime(2026, 10, 29, 19, 30, tzinfo=UTC)
        self.assertEqual(("NO_OBSERVATION_TIMESTAMP", None),
                         wd.assess({"price": 42, "market_state": "REGULAR"}, "US", now))
        self.assertEqual("FUTURE_OBSERVATION",
                         wd.assess(mark(42, "2026-10-29T19:40:00Z"), "US", now)[0])

    def test_market_calendar_holidays_and_us_early_close(self):
        self.assertEqual("CLOSED", wd.trading_session("US", datetime(2026, 11, 26, 17, tzinfo=UTC)))
        self.assertEqual("CLOSED", wd.trading_session("US", datetime(2026, 11, 27, 18, 30, tzinfo=UTC)))
        self.assertEqual("ACTIVE", wd.trading_session("US", datetime(2026, 11, 27, 17, 30, tzinfo=UTC)))
        self.assertEqual("ACTIVE", wd.trading_session("US", datetime(2026, 11, 2, 15, tzinfo=UTC)))
        self.assertEqual("CLOSED", wd.trading_session("GPW", datetime(2026, 11, 11, 12, tzinfo=UTC)))
        self.assertEqual("ACTIVE", wd.trading_session("GPW", datetime(2026, 10, 29, 13, tzinfo=UTC)))
        self.assertEqual("UNVERIFIED", wd.trading_session("GPW", datetime(2029, 10, 29, 13, tzinfo=UTC)))

    def test_stale_cannot_be_replaced_with_older_or_pre_market_quote(self):
        now = datetime(2026, 10, 29, 19, 30, tzinfo=UTC)
        old = position("MCK", mark(969.02, "2026-10-29T14:00:00Z"))
        state = {"markets": {"US": {"open_positions": [old]}}}
        with patch.object(wd.provider, "quote_for_symbol", return_value=mark(888, "2026-10-29T19:29:00Z", "PRE")):
            updated, health = wd.monitor(state, now=now)
        self.assertEqual(old, updated["markets"]["US"]["open_positions"][0])
        self.assertEqual("ALERT_STALE", health["positions"][0]["status"])

    def test_heartbeat_is_batched_and_no_unnecessary_position_change(self):
        now = datetime(2026, 10, 29, 19, 30, tzinfo=UTC)
        state = {"markets": {"US": {"open_positions": [
            position("MCK", mark(970, "2026-10-29T19:29:00Z"))]}}}
        with tempfile.TemporaryDirectory() as directory:
            portfolio = Path(directory) / "portfolio.json"
            health = Path(directory) / "watchdog.json"
            portfolio.write_text(json.dumps(state), encoding="utf-8")
            first = wd.publish(now, portfolio_path=portfolio, health_path=health)
            second = wd.publish(now, portfolio_path=portfolio, health_path=health)
            self.assertEqual(first, second)
            self.assertEqual(state, json.loads(portfolio.read_text(encoding="utf-8")))

    def test_no_extra_stale_text_on_public_mck_card(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "scripts/stock-trading-live-refresh.js").read_text(encoding="utf-8")
        self.assertNotIn("Kurs nieaktualny", source)
        self.assertNotIn("Stale quote (", source)
        self.assertNotIn("positionSymbol(position) === 'MCK'", source)


if __name__ == "__main__":
    unittest.main()
