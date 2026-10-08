from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import execution_price_engine as epe
from belief_market_data_adapter import Bar
from daily_engine_contract import DailyEngineOutput
import daily_eurusd_lifecycle as lifecycle
import daily_eurusd_spot_v17 as v17


class ExecutionPriceEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 29, 19, 2, 30, tzinfo=timezone.utc)

    def quote(self, price: float, age_seconds: float, source: str) -> epe.Quote:
        return epe.Quote(
            price=price,
            timestamp=self.now - timedelta(seconds=age_seconds),
            source=source,
        )

    def test_yahoo_quote_prefers_latest_bar_inside_normal_future_tolerance(self) -> None:
        now = self.now

        class Client:
            def bars(self, symbol, range_="1d", interval="1m"):
                return [
                    Bar(timestamp=now - timedelta(seconds=10), close=1.13400),
                    Bar(timestamp=now + timedelta(seconds=50), close=1.13408),
                ]

        with patch("execution_price_engine.utc_now", return_value=self.now):
            quote = epe.fetch_yahoo_eurusd_quote(Client())

        self.assertEqual(quote.price, 1.13400)
        self.assertEqual(quote.timestamp, self.now - timedelta(seconds=10))

    @patch("execution_price_engine._http_text")
    def test_stooq_live_quote_parses_bid_ask_and_warsaw_timestamp(self, http_text) -> None:
        http_text.return_value = (
            "Symbol,Date,Time,Open,High,Low,Close,Volume,Bid,Ask\n"
            "EURUSD,2026-10-02,10:44:08,1.12405,1.12689,1.12318,1.12506,,1.12499,1.12512\n"
        )
        quote = epe.fetch_stooq_eurusd_quote()
        self.assertAlmostEqual(quote.price, 1.125055, places=7)
        self.assertEqual(quote.timestamp.isoformat(), "2026-10-02T08:44:08+00:00")
        self.assertEqual(quote.source, "Stooq:EURUSD:bid-ask-mid")

    def test_consensus_prefers_stooq_inside_cluster(self) -> None:
        result = epe.verify_live_mid_quotes(
            "SHORT",
            [
                self.quote(1.13403, 7, "fxapi.app:EUR/USD:mid"),
                self.quote(1.13401, 5, "Stooq:EURUSD:bid-ask-mid"),
                self.quote(1.13404, 8, "Yahoo Finance:EURUSD=X:1m:mid-proxy"),
            ],
            now=self.now,
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["selected_mid_price"], 1.13401)
        self.assertTrue(result["selected_quote"]["source"].startswith("Stooq:"))

    def test_live_eurusd_fill_uses_current_primary_mid_after_cross_check(self) -> None:
        result = epe.verify_live_mid_fill(
            "SHORT",
            self.quote(1.13400, 8, "primary"),
            self.quote(1.13408, 14, "secondary"),
            now=self.now,
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["status"], "VERIFIED_FILL")
        self.assertEqual(result["selected_mid_price"], 1.13400)
        self.assertEqual(result["fill_price"], 1.13393)
        self.assertEqual(result["fill_side"], "BID")
        self.assertEqual(result["synthetic_spread_pips"], 1.5)
        self.assertEqual(result["price_type"], "MID_VERIFIED_SYNTHETIC_SPREAD")
        self.assertLess(result["cross_feed_difference_pips"], 1.0)
        self.assertFalse(result["executable_bid_ask_available"])

    def test_live_eurusd_fill_blocks_ghost_price_divergence(self) -> None:
        result = epe.verify_live_mid_fill(
            "SHORT",
            self.quote(1.13400, 8, "primary"),
            self.quote(1.13430, 10, "secondary"),
            now=self.now,
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["status"], "NO_FILL")
        self.assertEqual(result["reason"], "cross_feed_divergence")

    def test_live_eurusd_fill_blocks_stale_primary(self) -> None:
        result = epe.verify_live_mid_fill(
            "LONG",
            self.quote(1.13400, 181, "primary"),
            self.quote(1.13402, 5, "secondary"),
            now=self.now,
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "primary_quote_stale_or_future")

    def test_two_quotes_from_same_provider_count_as_one_source(self) -> None:
        result = epe.verify_live_mid_quotes(
            "SHORT",
            [
                self.quote(1.13400, 8, "fxapi.app:EUR/USD:mid"),
                self.quote(1.13402, 4, "fxapi.app:EUR/USD:backup"),
            ],
            now=self.now,
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["status"], "NO_FILL")
        self.assertEqual(result["reason"], "insufficient_independent_quotes")
        self.assertEqual(result["details"]["fresh_source_count"], 1)
        self.assertTrue(any(
            row.get("reason") == "duplicate_provider_quote"
            for row in result["details"]["rejected_quotes"]
        ))

    def test_single_fresh_source_is_not_enough_for_verified_fill(self) -> None:
        result = epe.verify_live_mid_quotes(
            "SHORT",
            [self.quote(1.13400, 8, "fxapi.app:EUR/USD:mid")],
            now=self.now,
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["status"], "NO_FILL")
        self.assertEqual(result["reason"], "insufficient_independent_quotes")
        self.assertEqual(result["details"]["fresh_source_count"], 1)

    def test_yahoo_current_minute_boundary_within_75s_can_cross_check_daily_fill(self) -> None:
        result = epe.verify_live_mid_quotes(
            "SHORT",
            [
                self.quote(1.13400, 8, "fxapi.app:EUR/USD:mid"),
                self.quote(1.13404, -50, "Yahoo Finance:EURUSD=X:1m:mid-proxy"),
            ],
            now=self.now,
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["fresh_source_count"], 2)
        self.assertEqual(result["verification_quality"], "CONSENSUS")

    def test_non_yahoo_future_quote_keeps_default_30s_tolerance(self) -> None:
        result = epe.verify_live_mid_quotes(
            "SHORT",
            [
                self.quote(1.13400, 8, "fxapi.app:EUR/USD:mid"),
                self.quote(1.13404, -50, "Currency Exchange Tool:EUR/USD:mid"),
            ],
            now=self.now,
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "insufficient_independent_quotes")
        rejected = result["details"]["rejected_quotes"]
        self.assertTrue(any(
            row.get("source") == "Currency Exchange Tool:EUR/USD:mid"
            and row.get("reason") == "stale_or_future"
            for row in rejected
        ))

    def test_yahoo_more_than_75s_future_is_still_rejected(self) -> None:
        result = epe.verify_live_mid_quotes(
            "SHORT",
            [
                self.quote(1.13400, 8, "fxapi.app:EUR/USD:mid"),
                self.quote(1.13404, -90, "Yahoo Finance:EURUSD=X:1m:mid-proxy"),
            ],
            now=self.now,
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "insufficient_independent_quotes")
        rejected = result["details"]["rejected_quotes"]
        self.assertTrue(any(
            row.get("source") == "Yahoo Finance:EURUSD=X:1m:mid-proxy"
            and row.get("reason") == "stale_or_future"
            for row in rejected
        ))

    def test_consensus_ignores_one_stale_vendor(self) -> None:
        result = epe.verify_live_mid_quotes(
            "LONG",
            [
                self.quote(1.13400, 9, "fxapi.app:EUR/USD:mid"),
                self.quote(1.13404, 14, "Currency Exchange Tool:EUR/USD:mid"),
                self.quote(1.14000, 500, "Yahoo Finance:EURUSD=X:1m:mid-proxy"),
            ],
            now=self.now,
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["verification_quality"], "CONSENSUS")
        self.assertEqual(result["fresh_source_count"], 2)
        self.assertEqual(result["selected_mid_price"], 1.13400)
        self.assertEqual(result["fill_price"], 1.13408)
        self.assertEqual(result["fill_side"], "ASK")

    def test_three_feed_consensus_rejects_yahoo_outlier_without_blocking_trade(self) -> None:
        result = epe.verify_live_mid_quotes(
            "SHORT",
            [
                self.quote(1.13400, 8, "fxapi.app:EUR/USD:mid"),
                self.quote(1.13405, 12, "Currency Exchange Tool:EUR/USD:mid"),
                self.quote(1.13456, 7, "Yahoo Finance:EURUSD=X:1m:mid-proxy"),
            ],
            now=self.now,
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["verification_quality"], "CONSENSUS")
        self.assertEqual(result["selected_mid_price"], 1.13400)
        self.assertEqual(result["fill_price"], 1.13393)
        self.assertEqual(result["fill_side"], "BID")
        self.assertGreater(result["cross_feed_range_pips"], 5.0)

    def test_two_divergent_feeds_block_entry(self) -> None:
        result = epe.verify_live_mid_quotes(
            "LONG",
            [
                self.quote(1.13400, 8, "fxapi.app:EUR/USD:mid"),
                self.quote(1.13440, 6, "Yahoo Finance:EURUSD=X:1m:mid-proxy"),
            ],
            now=self.now,
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["status"], "NO_FILL")
        self.assertEqual(result["reason"], "cross_feed_divergence")
        self.assertGreater(result["details"]["cross_feed_range_pips"], 1.5)

    def test_market_fill_survives_provider_outage(self) -> None:
        def down() -> epe.Quote:
            raise RuntimeError("vendor down")

        result = epe.eurusd_market_fill(
            "SHORT",
            now=self.now,
            fetchers=[
                down,
                lambda: self.quote(1.13402, 5, "Currency Exchange Tool:EUR/USD:mid"),
                lambda: self.quote(1.13406, 11, "Yahoo Finance:EURUSD=X:1m:mid-proxy"),
            ],
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["status"], "VERIFIED_FILL")
        self.assertEqual(result["fresh_source_count"], 2)
        self.assertEqual(len(result["provider_errors"]), 1)

    def test_market_fill_blocks_only_when_no_fresh_quote_exists(self) -> None:
        result = epe.eurusd_market_fill(
            "SHORT",
            now=self.now,
            fetchers=[
                lambda: self.quote(1.13400, 500, "fxapi.app:EUR/USD:mid"),
                lambda: self.quote(1.13402, 600, "Currency Exchange Tool:EUR/USD:mid"),
            ],
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["status"], "NO_FILL")
        self.assertEqual(result["reason"], "no_fresh_eurusd_quote")

    def test_daily_current_price_short_uses_full_one_point_five_pip_buffer(self) -> None:
        result = epe.verify_daily_current_price(
            "SHORT",
            self.quote(1.11920, 45, "Yahoo Finance:EURUSD=X:chart:1d:1m"),
            [self.quote(1.11884, 38, "fxapi.app:EUR/USD:mid")],
            now=self.now,
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["fill_price"], 1.11905)
        self.assertEqual(result["selected_mid_price"], 1.11920)
        self.assertTrue(result["cross_feed_warning"])
        self.assertEqual(result["synthetic_entry_buffer_pips"], 1.5)

    def test_daily_current_price_long_uses_full_one_point_five_pip_buffer(self) -> None:
        result = epe.verify_daily_current_price(
            "LONG",
            self.quote(1.11920, 45, "Yahoo Finance:EURUSD=X:chart:1d:1m"),
            [self.quote(1.11918, 38, "fxapi.app:EUR/USD:mid")],
            now=self.now,
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["fill_price"], 1.11935)

    def test_daily_current_price_rejects_stale_and_extreme_disagreement(self) -> None:
        self.assertEqual(
            epe.verify_daily_current_price(
                "SHORT",
                self.quote(1.11920, 800, "Yahoo Finance:EURUSD=X:chart:1d:1m"),
                [self.quote(1.11918, 2, "fxapi.app:EUR/USD:mid")],
                now=self.now,
            )["reason"],
            "daily_primary_stale_or_invalid",
        )
        self.assertEqual(
            epe.verify_daily_current_price(
                "SHORT",
                self.quote(1.11920, 8, "Yahoo Finance:EURUSD=X:chart:1d:1m"),
                [self.quote(1.11700, 2, "fxapi.app:EUR/USD:mid")],
                now=self.now,
            )["reason"],
            "daily_primary_reference_extreme_divergence",
        )

    def test_daily_current_price_requires_independent_reference(self) -> None:
        result = epe.verify_daily_current_price(
            "SHORT",
            self.quote(1.11920, 10, "Yahoo Finance:EURUSD=X:chart:1d:1m"),
            [],
            now=self.now,
        )
        self.assertEqual(result["reason"], "daily_independent_quality_check_unavailable")

    def test_recenter_geometry_preserves_daily_risk_distances(self) -> None:
        geometry = epe.recenter_geometry(
            "SHORT",
            analytical_entry=1.13456,
            stop=1.13762,
            target=1.12904,
            fill_price=1.13393,
            market_mid=1.13400,
        )
        self.assertEqual(geometry["entry"], 1.13393)
        self.assertEqual(geometry["market_mid"], 1.13400)
        self.assertEqual(geometry["stop"], 1.13706)
        self.assertEqual(geometry["target"], 1.12848)
        self.assertAlmostEqual(geometry["risk_distance"], 0.00313, places=8)
        self.assertAlmostEqual(geometry["reward_distance"], 0.00545, places=8)
        self.assertAlmostEqual(geometry["model_mid_risk_distance"], 0.00306, places=8)
        self.assertAlmostEqual(geometry["model_mid_reward_distance"], 0.00552, places=8)

    def test_synthetic_bid_ask_is_exactly_one_point_five_pips_wide(self) -> None:
        levels = epe.synthetic_bid_ask(1.13400)
        self.assertEqual(levels["bid"], 1.13393)
        self.assertEqual(levels["ask"], 1.13408)
        self.assertAlmostEqual((levels["ask"] - levels["bid"]) / epe.EURUSD_PIP, 1.5, places=6)

    def test_daily_time_exit_uses_opposite_spread_side(self) -> None:
        opened = self.now - timedelta(hours=24, minutes=1)
        position = {
            "trade_id": "synthetic-short",
            "direction": "SHORT",
            "opened_at": opened.isoformat(),
            "expires_at": (opened + timedelta(hours=24)).isoformat(),
            "entry": 1.13393,
            "stop": 1.13706,
            "target": 1.12848,
            "entry_score": 30.0,
            "entry_confidence": 0.5,
            "entry_components": {},
            "entry_weights": lifecycle.BASE_WEIGHTS,
            "engine_version": "test",
            "execution_price_engine": {
                "price_type": "MID_VERIFIED_SYNTHETIC_SPREAD",
                "synthetic_spread_pips": 1.5,
                "synthetic_half_spread_pips": 0.75,
            },
        }
        bar = Bar(
            timestamp=opened + timedelta(hours=24),
            open=1.13400,
            high=1.13405,
            low=1.13395,
            close=1.13400,
        )
        trade = lifecycle.evaluate_position(position, [bar], self.now)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "TIME_EXIT")
        self.assertEqual(trade["exit_price"], 1.13408)
        self.assertEqual(trade["monitor"]["execution_price_basis"], "SYNTHETIC_ASK")
        self.assertEqual(trade["monitor"]["synthetic_spread_pips"], 1.5)

    def test_mid_touch_does_not_fake_short_take_profit_before_ask_touches(self) -> None:
        opened = self.now - timedelta(minutes=10)
        position = {
            "trade_id": "synthetic-short-tp",
            "direction": "SHORT",
            "opened_at": opened.isoformat(),
            "expires_at": (opened + timedelta(hours=24)).isoformat(),
            "entry": 1.13393,
            "stop": 1.13706,
            "target": 1.13300,
            "entry_score": 30.0,
            "entry_confidence": 0.5,
            "entry_components": {},
            "entry_weights": lifecycle.BASE_WEIGHTS,
            "engine_version": "test",
            "execution_price_engine": {
                "price_type": "MID_VERIFIED_SYNTHETIC_SPREAD",
                "synthetic_spread_pips": 1.5,
                "synthetic_half_spread_pips": 0.75,
            },
        }
        mid_only_touch = Bar(
            timestamp=self.now - timedelta(minutes=2),
            open=1.13310,
            high=1.13320,
            low=1.13296,
            close=1.13305,
        )
        self.assertIsNone(lifecycle.evaluate_position(position, [mid_only_touch], self.now))

        executable_touch = Bar(
            timestamp=self.now - timedelta(minutes=1),
            open=1.13305,
            high=1.13315,
            low=1.13292,
            close=1.13300,
        )
        trade = lifecycle.evaluate_position(position, [executable_touch], self.now)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["exit_reason"], "TAKE_PROFIT")
        self.assertEqual(trade["exit_price"], 1.13300)

    def test_wes_market_bar_fill_requires_fresh_post_authorization_bar(self) -> None:
        start = self.now - timedelta(minutes=10)
        expires = self.now + timedelta(minutes=10)
        point = {
            "price": 1.12473,
            "timestamp": (self.now - timedelta(minutes=5)).isoformat(),
            "source": "Yahoo Finance:EURUSD=X:5m:market_now_completed_bar",
            "observed_high": 1.12490,
            "observed_low": 1.12450,
        }
        result = epe.verify_market_bar_fill(
            point,
            direction="short",
            entry_not_before=start,
            expires_at=expires,
            checked_at=self.now,
        )
        self.assertTrue(result["verified"])
        self.assertEqual("MARKET_NOW", result["mode"])
        self.assertEqual(1.12473, result["fill_price"])
        self.assertEqual("FRESH_COMPLETED_5M_CLOSE", result["price_type"])

        early = dict(point, timestamp=(start - timedelta(seconds=1)).isoformat())
        rejected = epe.verify_market_bar_fill(
            early,
            direction="short",
            entry_not_before=start,
            expires_at=expires,
            checked_at=self.now,
        )
        self.assertFalse(rejected["verified"])
        self.assertEqual("market_bar_before_authorization", rejected["reason"])

    def test_wes_market_bar_fill_rejects_stale_completed_bar(self) -> None:
        start = self.now - timedelta(minutes=30)
        expires = self.now + timedelta(minutes=10)
        stale = {
            "price": 7808.0,
            "timestamp": (self.now - timedelta(minutes=16)).isoformat(),
            "source": "Yahoo Finance:ES=F:5m:market_now_completed_bar",
            "observed_high": 7810.0,
            "observed_low": 7802.0,
        }
        rejected = epe.verify_market_bar_fill(
            stale,
            direction="long",
            entry_not_before=start,
            expires_at=expires,
            checked_at=self.now,
            max_age_seconds=15 * 60,
        )
        self.assertFalse(rejected["verified"])
        self.assertEqual("market_bar_stale_or_future", rejected["reason"])

    def test_wes_frozen_limit_touch_is_verified_only_inside_authorized_window(self) -> None:
        start = self.now - timedelta(minutes=10)
        expires = self.now + timedelta(minutes=30)
        point = {
            "price": 1.13420,
            "timestamp": (self.now - timedelta(minutes=2)).isoformat(),
            "source": "Yahoo Finance:EURUSD=X:5m:frozen_entry_target_touch",
            "observed_high": 1.13428,
            "observed_low": 1.13395,
        }
        result = epe.verify_frozen_limit_touch(
            point,
            direction="short",
            target_price=1.13420,
            entry_not_before=start,
            expires_at=expires,
            checked_at=self.now,
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["fill_price"], 1.13420)
        self.assertEqual(result["mode"], "FROZEN_LIMIT_TOUCH")

        early = dict(point, timestamp=(start - timedelta(seconds=1)).isoformat())
        rejected = epe.verify_frozen_limit_touch(
            early,
            direction="short",
            target_price=1.13420,
            entry_not_before=start,
            expires_at=expires,
            checked_at=self.now,
        )
        self.assertFalse(rejected["verified"])
        self.assertEqual(rejected["reason"], "touch_before_authorization")


class DailyEpeIntegrationTests(unittest.TestCase):
    def candidate(self) -> DailyEngineOutput:
        return DailyEngineOutput(
            instrument="EUR/USD",
            timestamp="2026-09-29T19:02:00Z",
            direction="SHORT",
            score=25.57,
            confidence=0.489,
            entry=1.13456,
            stop=1.13762,
            target=1.12904,
            horizon="intraday_to_27h",
            engine_version=v17.ENGINE_VERSION,
            status="SIGNAL",
            decision_mode="WITHOUT",
            metadata={
                "candidate": {
                    "direction": "SHORT",
                    "score": 25.57,
                    "confidence": 0.489,
                    "accepted": True,
                    "gate_reasons": [],
                },
                "risk": {},
            },
        ).validate()

    @patch("daily_eurusd_spot_v17.epe.daily_eurusd_market_fill")
    def test_daily_entry_is_repriced_to_verified_epe_fill(self, market_fill) -> None:
        market_fill.return_value = {
            "schema_version": epe.SCHEMA_VERSION,
            "engine_version": epe.ENGINE_VERSION,
            "instrument": "EUR/USD",
            "mode": "MARKET_NOW",
            "status": "VERIFIED_FILL",
            "verified": True,
            "selected_mid_price": 1.13400,
            "synthetic_bid": 1.13393,
            "synthetic_ask": 1.13408,
            "synthetic_spread_pips": 1.5,
            "synthetic_half_spread_pips": 0.75,
            "fill_price": 1.13393,
            "fill_side": "BID",
            "verified_at": "2026-09-29T19:03:01Z",
            "price_type": "MID_VERIFIED_SYNTHETIC_SPREAD",
        }

        output = v17._prepare_entry_candidate(self.candidate(), [], self.now())

        self.assertEqual(output.direction, "SHORT")
        self.assertEqual(output.entry, 1.13393)
        self.assertEqual(output.stop, 1.13706)
        self.assertEqual(output.target, 1.12848)
        self.assertEqual(output.timestamp, "2026-09-29T19:03:01Z")
        self.assertTrue(output.metadata["execution_price_engine"]["verified"])
        self.assertTrue(output.metadata["risk"]["execution_geometry_recentered"])

    @patch("daily_eurusd_spot_v17.epe.daily_eurusd_market_fill")
    def test_daily_position_persists_verified_epe_evidence(self, market_fill) -> None:
        market_fill.return_value = {
            "schema_version": epe.SCHEMA_VERSION,
            "engine_version": epe.ENGINE_VERSION,
            "instrument": "EUR/USD",
            "mode": "MARKET_NOW",
            "status": "VERIFIED_FILL",
            "verified": True,
            "selected_mid_price": 1.13400,
            "synthetic_bid": 1.13393,
            "synthetic_ask": 1.13408,
            "synthetic_spread_pips": 1.5,
            "synthetic_half_spread_pips": 0.75,
            "fill_price": 1.13393,
            "fill_side": "BID",
            "verified_at": "2026-09-29T19:03:01Z",
            "price_type": "MID_VERIFIED_SYNTHETIC_SPREAD",
        }
        executable = v17._prepare_entry_candidate(self.candidate(), [], self.now())
        position = v17._create_position(executable.to_dict())

        self.assertTrue(position["execution_price_engine"]["verified"])
        self.assertEqual(position["execution_price_engine"]["fill_price"], 1.13393)

    def test_daily_closed_trade_keeps_epe_evidence(self) -> None:
        position = {
            "direction": "SHORT",
            "execution_price_engine": {
                "schema_version": epe.SCHEMA_VERSION,
                "engine_version": epe.ENGINE_VERSION,
                "status": "VERIFIED_FILL",
                "verified": True,
                "fill_price": 1.13400,
            },
        }
        with patch.object(v17, "_original_evaluate_position", return_value={"trade_id": "x"}):
            trade = v17._evaluate_position(position, [], self.now())

        self.assertIsNotNone(trade)
        self.assertTrue(trade["execution_price_engine"]["verified"])
        self.assertEqual(trade["execution_price_engine"]["fill_price"], 1.13400)

    @patch("daily_eurusd_spot_v17.epe.daily_eurusd_market_fill")
    def test_daily_entry_fails_closed_when_epe_cannot_verify_price(self, market_fill) -> None:
        market_fill.return_value = epe.blocked(
            "cross_feed_divergence",
            mode="MARKET_NOW",
        )

        output = v17._prepare_entry_candidate(self.candidate(), [], self.now())

        self.assertEqual(output.direction, "FLAT")
        self.assertEqual(output.status, "NO_TRADE")
        self.assertIsNone(output.entry)
        self.assertFalse(output.metadata["candidate"]["accepted"])
        self.assertIn("epe_cross_feed_divergence", output.metadata["candidate"]["gate_reasons"])

    @staticmethod
    def now() -> datetime:
        return datetime(2026, 9, 29, 19, 3, 1, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
