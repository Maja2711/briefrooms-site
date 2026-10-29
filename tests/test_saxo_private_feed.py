"""Private Saxo SIM adapter + optional EPE veto; no broker data in public output."""
from __future__ import annotations

import base64
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import execution_price_engine as epe
import saxo_private_feed as saxo


class SaxoPrivateFeedTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 19, 10, 15, tzinfo=timezone.utc)
        self.payload = {
            "source": "SAXO_SIM", "instrument": "EUR/USD",
            "bid": 1.11351, "ask": 1.11371, "mid": 1.11361,
            "last_updated": "2026-10-27T19:10:14.500Z",
            "delayed_by_minutes": 0, "market_state": "Open",
            "trading_enabled": False, "redistributable": False,
            "usage": "PRIVATE_TEST_SIM_ONLY",
        }

    def test_valid_sim_mid_is_read_in_memory_only(self):
        self.assertEqual(saxo.validate_private_saxo_quote(self.payload, now=self.now), 1.11361)

    def test_expired_quote_fails_closed(self):
        self.payload["last_updated"] = "2026-10-27T19:07:14Z"
        with self.assertRaises(saxo.PrivateSaxoFeedError):
            saxo.validate_private_saxo_quote(self.payload, now=self.now)

    def test_future_quote_fails_closed(self):
        self.payload["last_updated"] = "2026-10-27T19:11:00Z"
        with self.assertRaises(saxo.PrivateSaxoFeedError):
            saxo.validate_private_saxo_quote(self.payload, now=self.now)

    def test_mismatched_bid_ask_mid_fails_closed(self):
        self.payload["mid"] = 1.11400
        with self.assertRaises(saxo.PrivateSaxoFeedError):
            saxo.validate_private_saxo_quote(self.payload, now=self.now)

    def test_missing_or_redistributable_metadata_fails_closed(self):
        for key, value in (("redistributable", True), ("trading_enabled", True),
                           ("source", "UNKNOWN"), ("market_state", "Closed"),
                           ("delayed_by_minutes", 15)):
            row = dict(self.payload, **{key: value})
            with self.subTest(key=key), self.assertRaises(saxo.PrivateSaxoFeedError):
                saxo.validate_private_saxo_quote(row, now=self.now)

    def test_fetch_requires_private_password(self):
        with patch.dict(os.environ, {"SAXO_PRIVATE_BASIC_PASSWORD": ""}):
            with self.assertRaises(saxo.PrivateSaxoFeedError):
                saxo.fetch_private_saxo_mid(now=self.now)

    def test_http_auth_sent_only_to_pinned_worker_no_loggable_secrets(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(self.payload).encode("utf8")
        response.__enter__.return_value = response
        with patch.dict(os.environ, {
            "SAXO_PRIVATE_BASIC_USER": "admin",
            "SAXO_PRIVATE_BASIC_PASSWORD": "private-password",
        }), patch("saxo_private_feed.urllib.request.urlopen", return_value=response) as opener:
            self.assertEqual(saxo.fetch_private_saxo_mid(now=self.now), 1.11361)
        req = opener.call_args.args[0]
        self.assertEqual(req.full_url, saxo.SAXO_WORKER_URL)
        self.assertEqual(base64.b64decode(req.get_header("Authorization").split()[1]).decode(),
                         "admin:private-password")

    def test_private_stream_activation_does_not_fallback_to_legacy_rest(self):
        with patch.dict(os.environ, {"SAXO_STREAM_PRIMARY":"1",
                                  "SAXO_PRIVATE_STREAM_URL":""}):
            with self.assertRaisesRegex(saxo.PrivateSaxoFeedError,
                                        "stream_endpoint_not_configured"):
                saxo._private_quote_endpoint()
        with patch.dict(os.environ, {"SAXO_STREAM_PRIMARY":"1",
                                  "SAXO_PRIVATE_STREAM_URL":"http://evil.example/saxo/quote"}):
            with self.assertRaises(saxo.PrivateSaxoFeedError):
                saxo._private_quote_endpoint()

    def test_stream_endpoint_is_pinned_and_private(self):
        url = saxo.SAXO_STREAM_WORKER_URL
        with patch.dict(os.environ, {"SAXO_STREAM_PRIMARY":"1",
                                  "SAXO_PRIVATE_STREAM_URL":url,
                                  "SAXO_PRIVATE_BASIC_PASSWORD":"test-secret"}), patch(
            "saxo_private_feed.urllib.request.urlopen"
        ) as opener:
            response = MagicMock()
            response.status=200
            payload = dict(self.payload, timestamp_origin="SAXO_STREAM_PRICE_SIDE_RECEIVED_AT")
            response.read.return_value=json.dumps(payload).encode("utf8")
            response.__enter__.return_value=response
            opener.return_value=response
            quote=saxo.fetch_private_saxo_quote(now=self.now)
            self.assertEqual(quote["source"], "SAXO_SIM")
            self.assertEqual(opener.call_args.args[0].full_url, url)

    def test_stream_endpoint_rejects_other_workers_dev_hosts(self):
        with patch.dict(os.environ, {
            "SAXO_STREAM_PRIMARY": "1",
            "SAXO_PRIVATE_STREAM_URL": "https://other-site.workers.dev/saxo/quote"
        }):
            with self.assertRaises(saxo.PrivateSaxoFeedError):
                saxo._private_quote_endpoint()

    def test_external_error_is_sanitized(self):
        with patch.dict(os.environ, {"SAXO_PRIVATE_BASIC_PASSWORD": "very-secret"}), patch(
            "saxo_private_feed.urllib.request.urlopen", side_effect=RuntimeError("very-secret")
        ):
            with self.assertRaises(saxo.PrivateSaxoFeedError) as err:
                saxo.fetch_private_saxo_mid(now=self.now)
            self.assertEqual(str(err.exception), "private_saxo_quote_unavailable")


class SaxoDailyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 19, 10, 15, tzinfo=timezone.utc)

    def _run(self):
        q1 = epe.Quote(1.11360, self.now - timedelta(seconds=2), "Stooq:EURUSD:live")
        q2 = epe.Quote(1.11361, self.now - timedelta(seconds=3), "fxapi.app:EUR/USD:mid")
        with patch.object(epe, "fetch_stooq_eurusd_quote", return_value=q1), patch.object(
            epe, "fetch_fxapi_eurusd_quote", return_value=q2
        ), patch.object(epe, "fetch_currency_exchange_tool_eurusd_quote",
                        side_effect=OSError("vendor down")), patch.object(
            epe, "fetch_yahoo_eurusd_quote", side_effect=OSError("vendor down")
        ):
            return epe.daily_eurusd_market_fill("LONG", now=self.now)

    def test_default_remains_original_public_epe(self):
        with patch.dict(os.environ, {"SAXO_PRIVATE_QUALITY_GATE": "0"}), patch(
            "saxo_private_feed.fetch_private_saxo_mid",
            side_effect=AssertionError("SIM data cannot be fetched without opt-in"),
        ) as read:
            result = self._run()
        self.assertTrue(result["verified"])
        self.assertNotIn("private_saxo_gate", result)
        read.assert_not_called()

    def test_private_consensus_checks_without_publishing_saxo_quote(self):
        with patch.dict(os.environ, {"SAXO_PRIVATE_QUALITY_GATE": "1"}), patch(
            "saxo_private_feed.fetch_private_saxo_mid", return_value=1.11361
        ) as read:
            result = self._run()
        self.assertTrue(result["verified"])
        self.assertEqual(result["private_saxo_gate"], "PASS_NO_PRICE_REDISTRIBUTION")
        self.assertNotIn("SAXO_SIM", json.dumps(result))
        read.assert_called_once()

    def test_private_feed_failure_vetoes_without_secret_leak(self):
        with patch.dict(os.environ, {"SAXO_PRIVATE_QUALITY_GATE": "1"}), patch(
            "saxo_private_feed.fetch_private_saxo_mid",
            side_effect=RuntimeError("PRIVATE_SAXO_SECRET_SHOULD_NOT_LEAK")
        ):
            result = self._run()
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "private_saxo_quote_unavailable")
        self.assertNotIn("PRIVATE_SAXO_SECRET", json.dumps(result))

    def test_private_price_disagreement_vetoes_without_disclosure(self):
        with patch.dict(os.environ, {"SAXO_PRIVATE_QUALITY_GATE": "1"}), patch(
            "saxo_private_feed.fetch_private_saxo_mid", return_value=1.12361
        ):
            result = self._run()
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "private_saxo_public_consensus_mismatch")
        self.assertNotIn("1.12361", json.dumps(result))

    def test_injected_test_fetchers_never_touch_private_feed(self):
        q1 = epe.Quote(1.11360, self.now, "Stooq:EURUSD:live")
        q2 = epe.Quote(1.11361, self.now, "fxapi.app:EUR/USD:mid")
        with patch.dict(os.environ, {"SAXO_PRIVATE_QUALITY_GATE": "1"}), patch(
            "saxo_private_feed.fetch_private_saxo_mid",
            side_effect=AssertionError("not allowed")
        ) as read:
            result = epe.daily_eurusd_market_fill(
                "SHORT", now=self.now, fetchers=[lambda: q1, lambda: q2]
            )
        self.assertTrue(result["verified"])
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
