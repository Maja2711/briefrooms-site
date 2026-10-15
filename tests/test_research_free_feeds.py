from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from research_free_feeds import (
    CHECKMYSWAP_URL, SOURCE_SCHEMA, collect, evaluate_checkmyswap,
    evaluate_twelvedata, fetch_checkmyswap, fetch_twelvedata,
)

NOW = datetime(2026, 10, 15, 20, 36, tzinfo=timezone.utc)

def points(date_="2026-10-14"):
    return [
        {"date": date_, "tenor": "1Y", "rate": 3.6, "rateBps": 360,
         "trades": 80, "closeTrades": 10, "method": "close", "source": "DTCC"},
        {"date": date_, "tenor": "2Y", "rate": 3.55, "rateBps": 355,
         "trades": 60, "closeTrades": 3, "method": "close-thin", "source": "DTCC"},
    ]

def checkmy(date_="2026-10-14"):
    return {
        "USD": {"currency": "USD", "curve": points(date_), "date": date_, "fetchedAt": "2026-10-15T01:00:00Z"},
        "EUR": {"currency": "EUR", "curve": points(date_), "date": date_, "fetchedAt": "2026-10-15T01:00:00Z"},
    }

def td_payload(stamp="2026-10-15 20:34:00"):
    return {"meta": {"symbol": "EUR/USD", "interval": "1min", "currency_base": "Euro"},
            "values": [{"datetime": stamp, "open": "1.12", "high": "1.13",
                        "low": "1.11", "close": "1.125"}], "status": "ok"}


class ResearchFreeFeedsTests(unittest.TestCase):
    def test_checkmyswap_real_schema_current_dated_par_curve_not_live_ois(self):
        status = evaluate_checkmyswap(checkmy(), NOW)
        self.assertEqual(status["status"], "current_daily_archive")
        self.assertTrue(status["research_usable"])
        self.assertEqual(status["observation_date"], "2026-10-14")
        self.assertEqual(status["tenors_usd"], ["1Y", "2Y"])
        self.assertEqual(status["usd_accepted_records"], 140)
        self.assertEqual(status["method_counts_usd"]["thin_close"], 1)
        self.assertFalse(status["live_policy_evidence_authorized"])
        self.assertFalse(status["no_forward_curve_substitution"] is False)
        self.assertFalse(status["daily_epe_price_authority"])
        self.assertNotIn("rate", status)
        self.assertNotIn("par_rates", status)

    def test_checkmyswap_older_snapshot_is_not_current(self):
        status = evaluate_checkmyswap(checkmy("2026-10-09"), NOW)
        self.assertEqual(status["status"], "stale_daily_archive")
        self.assertFalse(status["research_usable"])
        self.assertFalse(status["trade_authority"])

    def test_checkmyswap_missing_misaligned_and_future_fail_closed(self):
        for broken in ("missing_eur", "misaligned", "future", "wrong_ccy", "duplicate_tenor", "bad_rate"):
            with self.subTest(broken=broken):
                row = checkmy()
                if broken == "missing_eur":
                    del row["EUR"]
                elif broken == "misaligned":
                    row["EUR"]["date"] = "2026-10-13"
                elif broken == "future":
                    row["EUR"]["date"] = "2026-10-18"
                elif broken == "wrong_ccy":
                    row["EUR"]["currency"] = "GBP"
                elif broken == "duplicate_tenor":
                    row["USD"]["curve"][1]["tenor"] = "1Y"
                elif broken == "bad_rate":
                    row["USD"]["curve"][0]["rate"] = "NaN"
                result = evaluate_checkmyswap(row, NOW)
                self.assertEqual(result["status"], "unavailable")
                self.assertFalse(result["research_usable"])

    def test_checkmyswap_uses_verified_endpoint_and_never_authenticates(self):
        requests = []
        row = fetch_checkmyswap(fetcher=lambda url, headers:
                                requests.append((url, headers)) or checkmy())
        self.assertEqual(row["USD"]["currency"], "USD")
        self.assertEqual(requests[0][0], CHECKMYSWAP_URL)
        self.assertNotIn("Authorization", requests[0][1])

    def test_twelve_valid_intraday_private_comparison_only(self):
        status = evaluate_twelvedata(td_payload(), NOW)
        self.assertEqual(status["status"], "fresh_private_comparison")
        self.assertEqual(status["bar_observed_at"], "2026-10-15T20:34:00Z")
        self.assertEqual(status["bar_age_minutes"], 2.0)
        self.assertFalse(status["daily_epe_price_authority"])
        self.assertFalse(status["public_redistribution"])
        self.assertNotIn("close", status)

    def test_twelve_missing_wrong_stale_future_invalid_never_eligible(self):
        cases = [
            ({"status": "error", "code": 429, "message": "API credits used"}, "invalid_data"),
            ({"meta": {}, "values": []}, "invalid_data"),
            (td_payload("2026-10-15 19:59:00"), "stale_private_comparison"),
            (td_payload("2026-10-15 20:45:00"), "invalid_data"),
        ]
        for data, expected in cases:
            with self.subTest(expected=expected):
                output = evaluate_twelvedata(data, NOW)
                self.assertEqual(output["status"], expected)
                self.assertFalse(output["trade_authority"])

    def test_twelve_rejects_wrong_symbol_and_implausible_or_duplicate_bars(self):
        row = td_payload()
        row["meta"]["symbol"] = "BTC/USD"
        self.assertEqual(evaluate_twelvedata(row, NOW)["status"], "invalid_data")
        row = td_payload()
        row["values"][0]["close"] = "infinity"
        self.assertEqual(evaluate_twelvedata(row, NOW)["status"], "invalid_data")
        row = td_payload()
        row["values"].append(dict(row["values"][0]))
        self.assertEqual(evaluate_twelvedata(row, NOW)["status"], "invalid_data")

    def test_twelve_api_key_in_header_not_url(self):
        calls = []
        fetch_twelvedata("PRIVATE_KEY", fetcher=lambda url, headers:
                         calls.append((url, headers)) or td_payload())
        self.assertEqual(len(calls), 1)
        self.assertIn("symbol=EUR%2FUSD", calls[0][0])
        self.assertIn("timezone=UTC", calls[0][0])
        self.assertNotIn("PRIVATE_KEY", calls[0][0])
        self.assertEqual(calls[0][1]["Authorization"], "apikey PRIVATE_KEY")

    def test_free_plan_usage_gate_never_calls_twelve_data(self):
        def unexpected(*a):
            self.fail("free plan not authorized for this workflow")
        report = collect(NOW, checkmyswap_fetcher=lambda: checkmy(),
                         td_fetcher=unexpected, td_key="PRIVATE_KEY", research_allowed=False)
        self.assertEqual(report["schema_version"], SOURCE_SCHEMA)
        self.assertEqual(report["twelvedata"]["status"], "disabled_by_usage_rights_gate")
        self.assertEqual(report["checkmyswap"]["status"], "current_daily_archive")
        self.assertFalse(report["authority"]["production_trading"])
        self.assertNotIn("PRIVATE_KEY", str(report))

    def test_no_key_means_no_twelve_requests_even_when_rights_gate_open(self):
        report = collect(NOW, checkmyswap_fetcher=lambda: checkmy(),
                         td_fetcher=lambda k: self.fail("must not call without key"),
                         td_key="", research_allowed=True)
        self.assertEqual(report["twelvedata"]["status"], "api_key_not_configured")

    def test_twelve_entitled_internal_research_can_read_but_never_becomes_trading_feed(self):
        report = collect(NOW, checkmyswap_fetcher=lambda: checkmy(),
                         td_fetcher=lambda key: td_payload(), td_key="key", research_allowed=True)
        self.assertEqual(report["twelvedata"]["status"], "fresh_private_comparison")
        self.assertFalse(report["authority"]["daily_epe"])
        self.assertFalse(report["authority"]["website_quote"])
        self.assertFalse(report["authority"]["policy_differential_evidence"])

    def test_source_failure_keeps_report_and_never_promotes_second_leg(self):
        report = collect(NOW, checkmyswap_fetcher=lambda: (_ for _ in ()).throw(OSError()),
                         research_allowed=False)
        self.assertEqual(report["checkmyswap"]["status"], "source_unavailable")
        self.assertFalse(report["authority"]["production_trading"])


if __name__ == "__main__":
    unittest.main()
