from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import canonical_market_snapshot as cms
from scripts import daily_stock_us_adapter as us_adapter
from scripts import market_snapshot_adapters as adapters
from scripts import us_daily_stock as us


class CanonicalMarketSnapshotTests(unittest.TestCase):
    def base(self, **overrides):
        value = {
            "instrument_id": "eurusd",
            "provider": "yahoo",
            "provider_symbol": "EURUSD=X",
            "source_ref": "https://finance.yahoo.com/quote/EURUSD=X",
            "observed_at": "2026-09-02T10:00:00Z",
            "received_at": "2026-09-02T10:00:03Z",
            "created_at": "2026-09-02T10:00:04Z",
            "market_status": "OPEN",
            "quote_kind": "OHLC",
            "open": 1.1600,
            "high": 1.1620,
            "low": 1.1590,
            "last": 1.1610,
        }
        value.update(overrides)
        return value

    def test_snapshot_id_and_hash_are_deterministic(self):
        first = cms.build_snapshot(**self.base())
        second = cms.build_snapshot(**self.base())
        self.assertEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(first.snapshot_hash, second.snapshot_hash)
        self.assertTrue(first.snapshot_id.startswith("mkt-"))

    def test_equivalent_timezone_instants_normalize_to_same_identity(self):
        first = cms.build_snapshot(**self.base())
        second = cms.build_snapshot(**self.base(
            observed_at="2026-09-02T12:00:00+02:00",
            received_at="2026-09-02T12:00:03+02:00",
            created_at="2026-09-02T12:00:04+02:00",
        ))
        self.assertEqual(first.snapshot_hash, second.snapshot_hash)
        self.assertEqual(second.observed_at, "2026-09-02T10:00:00.000000Z")

    def test_build_rejects_invalid_timestamp_lineage(self):
        with self.assertRaises(cms.MarketSnapshotError):
            cms.build_snapshot(**self.base(
                observed_at="2026-09-02T10:05:00Z",
                received_at="2026-09-02T10:00:00Z",
                created_at="2026-09-02T10:00:01Z",
                max_lineage_clock_skew_seconds=10,
            ))

    def test_naive_timestamp_is_rejected_not_assumed(self):
        with self.assertRaises(cms.MarketSnapshotError):
            cms.build_snapshot(**self.base(observed_at="2026-09-02T10:00:00"))

    def test_stale_snapshot_is_blocked_by_consumer_policy(self):
        snapshot = cms.build_snapshot(**self.base())
        policy = cms.FreshnessPolicy("test-5m", max_age_seconds=300, required_price_fields=("last",))
        assessment = cms.assess_snapshot(snapshot, as_of="2026-09-02T10:10:00Z", policy=policy)
        self.assertEqual(assessment.status, cms.STATUS_STALE)
        self.assertEqual(assessment.decision_status, cms.DATA_QUALITY_BLOCKED)
        with self.assertRaises(cms.MarketSnapshotError):
            cms.require_usable(assessment)

    def test_missing_required_price_is_incomplete(self):
        snapshot = cms.build_snapshot(**self.base(last=None))
        policy = cms.FreshnessPolicy("needs-last", max_age_seconds=900, required_price_fields=("last",))
        assessment = cms.assess_snapshot(snapshot, as_of="2026-09-02T10:01:00Z", policy=policy)
        self.assertEqual(assessment.status, cms.STATUS_INCOMPLETE)
        self.assertEqual(assessment.missing_fields, ("last",))

    def test_future_snapshot_is_invalid_at_decision_time(self):
        snapshot = cms.build_snapshot(**self.base(
            observed_at="2026-09-02T10:10:00Z",
            received_at="2026-09-02T10:10:01Z",
            created_at="2026-09-02T10:10:02Z",
        ))
        policy = cms.FreshnessPolicy("point-in-time", max_age_seconds=900, max_future_skew_seconds=2)
        assessment = cms.assess_snapshot(snapshot, as_of="2026-09-02T10:00:00Z", policy=policy)
        self.assertEqual(assessment.status, cms.STATUS_INVALID_TIMESTAMP)
        self.assertFalse(assessment.timestamp_lineage_valid)

    def test_bid_ask_are_not_fabricated_by_yahoo_adapter(self):
        snapshot = adapters.adapt_yahoo_ohlc_snapshot(
            instrument_id="eurusd",
            provider_symbol="EURUSD=X",
            observed_at="2026-09-02T10:00:00Z",
            received_at="2026-09-02T10:00:01Z",
            open=1.16,
            high=1.162,
            low=1.159,
            last=1.161,
        )
        self.assertIsNone(snapshot.bid)
        self.assertIsNone(snapshot.ask)

    def test_dynamic_equity_ids_are_stable_and_market_scoped(self):
        self.assertEqual(adapters.canonical_equity_id("gpw", "PKO.WA"), "equity.pl.pko")
        self.assertEqual(adapters.canonical_equity_id("us", "AAPL"), "equity.us.aapl")

    def test_equity_adapter_attaches_id_hash_utc_lineage_and_quality(self):
        now = datetime(2026, 9, 2, 9, 10, tzinfo=ZoneInfo("Europe/Warsaw"))
        raw = {
            "provider": "Yahoo",
            "symbol": "PKO.WA",
            "date": "2026-09-02",
            "observed_at": "2026-09-02T09:09:00+02:00",
            "open": 80.0,
            "high": 81.0,
            "low": 79.5,
            "last": 80.5,
            "volume": 1200,
        }
        policy = adapters.equity_execution_policy("gpw", max_age_seconds=20 * 60)
        enriched = adapters.attach_equity_canonical_snapshot(
            raw, market="gpw", received_at=now, created_at=now, decision_at=now, policy=policy
        )
        self.assertTrue(enriched["market_snapshot_id"].startswith("mkt-"))
        self.assertEqual(enriched["canonical_market_snapshot"]["instrument_id"], "equity.pl.pko")
        self.assertTrue(enriched["canonical_market_snapshot"]["observed_at"].endswith("Z"))
        self.assertEqual(enriched["canonical_data_quality"]["status"], cms.STATUS_OK)

    def test_us_adapter_wraps_real_execution_snapshot_without_strategy_change(self):
        now = datetime(2026, 9, 2, 9, 40, tzinfo=ZoneInfo("America/New_York"))
        raw = {
            "provider": "Yahoo",
            "symbol": "AAPL",
            "date": "2026-09-02",
            "observed_at": "2026-09-02T09:39:00-04:00",
            "open": 200.0,
            "high": 202.0,
            "low": 199.5,
            "last": 201.0,
            "volume": 100000,
            "status": "single_source",
        }
        with patch.object(us_adapter, "_ORIGINAL_OPENING_SNAPSHOT", return_value=raw):
            enriched = us_adapter._canonical_opening_snapshot("AAPL", now=now)
        self.assertEqual(enriched["last"], raw["last"])
        self.assertEqual(enriched["canonical_market_snapshot"]["instrument_id"], "equity.us.aapl")
        self.assertEqual(enriched["canonical_data_quality"]["status"], cms.STATUS_OK)

    def test_coverage_report_never_treats_unknown_as_pass(self):
        report = adapters.coverage_report()
        self.assertFalse(report["legacy_backfill"])
        self.assertFalse(report["unknown_means_pass"])
        self.assertEqual(report["components"]["gpw_daily"]["status"], adapters.COVERAGE_CANONICALIZED)
        self.assertEqual(report["components"]["us_daily"]["status"], adapters.COVERAGE_CANONICALIZED)
        self.assertNotEqual(report["components"]["brace_spx"]["status"], adapters.COVERAGE_CANONICALIZED)


if __name__ == "__main__":
    unittest.main()
