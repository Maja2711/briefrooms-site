from __future__ import annotations

import json
import math
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from belief_lseg_ois_market_feed import (
    CURVES_URL, AUTH_URL, fetch_lseg_ois_curves, normalize_lseg_ipa_response,
    lseg_ois_request_body,
)
from belief_fed_ecb_ois_adapter import FedEcbOisForwardAdapter

NOW = datetime(2026, 10, 15, 19, 0, tzinfo=timezone.utc)


def sample_curve(currency, *, timestamp="2026-10-15T18:56:00Z"):
    index = "SOFR" if currency == "USD" else "ESTR"
    rates = [(3, 4.0), (6, 3.6), (12, 3.1)] if currency == "USD" else [
        (3, 2.5), (6, 2.5), (12, 2.5),
    ]
    return {
        "curveDefinition": {
            "currency": currency,
            "indexName": index, "indexTenors": ["OIS"],
            "discountingTenor": "OIS", "source": "Refinitiv",
            "id": f"TEST-{currency}-{index}-ID",
        },
        "curveParameters": {"marketDataDate": timestamp, "priceSide": "Mid"},
        "curves": {"OIS": {"curvePoints": [
            {"tenor": f"{months}M" if months < 12 else "1Y",
             "discountFactor": math.exp(-rate * months / 1200.0)}
            for months, rate in rates
        ]}},
    }


def lseg_reply():
    return {"data": [sample_curve("EUR"), sample_curve("USD")]}


class LSEGMarketOISTest(unittest.TestCase):
    def test_requests_only_sofr_estr_ois_mid_curves(self):
        body = lseg_ois_request_body()
        self.assertEqual(len(body["universe"]), 2)
        self.assertEqual([x["curveDefinition"]["indexName"] for x in body["universe"]],
                         ["SOFR", "ESTR"])
        self.assertTrue(all(x["curveDefinition"]["discountingTenor"] == "OIS"
                            for x in body["universe"]))
        self.assertTrue(all(x["curveDefinition"]["indexTenors"] == ["OIS"]
                            for x in body["universe"]))

    def test_lseg_two_leg_ipa_payload_is_normalized_and_accepted_by_live_adapter(self):
        normalized = normalize_lseg_ipa_response(lseg_reply())
        self.assertEqual(normalized["curves"]["EUR"]["overnight_index"], "ESTR")
        self.assertEqual(normalized["curves"]["USD"]["overnight_index"], "SOFR")
        self.assertEqual(normalized["curves"]["USD"]["market_observed_at"], "2026-10-15T18:56:00Z")
        adapter = FedEcbOisForwardAdapter(
            lseg_client_id="LSEG-CLIENT", lseg_client_secret="hidden",
            lseg_fetcher=lambda client, secret: normalized,
            url="", token="",
        )
        result = adapter.run(NOW)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(adapter.last_status["access_route"], "lseg_ipa_oauth_v2")
        self.assertEqual(result.evidence[0].belief_id, "eurusd.policy_differential.supportive")
        self.assertEqual(result.evidence[0].direction, 1)
        self.assertLessEqual(result.evidence[0].strength, 0.22)
        self.assertEqual(result.observations[0].observed_at, "2026-10-15T18:56:00Z")

    def test_lseg_cloud_oauth2_token_then_entitled_ipa_endpoint(self):
        requests = []
        def post(url, body, headers, timeout):
            requests.append((url, body, headers, timeout))
            if url == AUTH_URL:
                self.assertEqual(headers["Content-Type"], "application/x-www-form-urlencoded")
                self.assertIn(b"grant_type=client_credentials", body)
                return {"access_token": "SECRET_ACCESS_TOKEN", "expires_in": 600}
            self.assertEqual(url, CURVES_URL)
            self.assertEqual(headers["Authorization"], "Bearer SECRET_ACCESS_TOKEN")
            self.assertEqual(json.loads(body), lseg_ois_request_body())
            return lseg_reply()
        normalized = fetch_lseg_ois_curves("client-id", "secret", post=post)
        self.assertEqual(len(requests), 2)
        self.assertEqual(set(normalized["curves"]), {"USD", "EUR"})
        self.assertNotIn("SECRET_ACCESS_TOKEN", json.dumps(normalized))

    def test_missing_market_quote_timestamp_is_unusable_not_fetch_time(self):
        response = lseg_reply()
        for row in response["data"]:
            row["curveParameters"] = {"valuationDate": "2026-10-15", "priceSide": "Mid"}
        with self.assertRaisesRegex(ValueError, "quote_time_missing_or_date_only"):
            normalize_lseg_ipa_response(response)

    def test_date_only_market_data_date_is_not_a_live_quote(self):
        response = lseg_reply()
        response["data"][0]["curveParameters"]["marketDataDate"] = "2026-10-15"
        with self.assertRaisesRegex(ValueError, "quote_time_missing_or_date_only"):
            normalize_lseg_ipa_response(response)

    def test_reject_unauthorized_wrong_index_and_duplicate_curve(self):
        for issue in ("wrong_index", "missing_eur", "duplicate_usd", "missing_ois", "missing_tenor"):
            with self.subTest(issue=issue):
                response = lseg_reply()
                if issue == "wrong_index":
                    response["data"][0]["curveDefinition"]["indexName"] = "EURIBOR"
                elif issue == "missing_eur":
                    response["data"] = [response["data"][1]]
                elif issue == "duplicate_usd":
                    response["data"] = [response["data"][1], response["data"][1]]
                elif issue == "missing_ois":
                    response["data"][0]["curves"] = {"3M": response["data"][0]["curves"]["OIS"]}
                elif issue == "missing_tenor":
                    response["data"][0]["curves"]["OIS"]["curvePoints"].pop()
                with self.assertRaises(ValueError):
                    normalize_lseg_ipa_response(response)

    def test_provider_entitlement_error_has_zero_evidence_and_no_secret_leak(self):
        def unavailable(client, secret):
            raise PermissionError("LSEG entitlement refused")
        adapter = FedEcbOisForwardAdapter(
            lseg_client_id="client", lseg_client_secret="VERY_SECRET",
            lseg_fetcher=unavailable, url="", token="",
        )
        result = adapter.run(NOW)
        self.assertFalse(result.evidence)
        self.assertEqual(adapter.last_status["reason"], "ois_market_feed_unavailable_or_invalid")
        self.assertNotIn("VERY_SECRET", json.dumps(adapter.last_status))

    def test_lseg_credential_priority_over_legacy_feed_url(self):
        adapter = FedEcbOisForwardAdapter(
            lseg_client_id="client", lseg_client_secret="secret",
            lseg_fetcher=lambda *a: normalize_lseg_ipa_response(lseg_reply()),
            fetcher=lambda *a: self.fail("Legacy URL should not be called"),
            url="https://old-optional-provider.example.test/ois",
        )
        self.assertTrue(adapter.run(NOW).evidence)


if __name__ == "__main__":
    unittest.main()
