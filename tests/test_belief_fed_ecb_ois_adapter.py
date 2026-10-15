from __future__ import annotations

import math
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from belief_fed_ecb_ois_adapter import (
    FedEcbOisForwardAdapter, calculate_policy_ois, CONTRACT,
)
from belief_core import BeliefCore, Evidence
from belief_macro_release_adapter import MACRO_BELIEFS
from epistemic_consumer_interface import EURUSD_PROFILE_WEIGHTS

NOW = datetime(2026, 10, 15, 19, 0, tzinfo=timezone.utc)


def example_curve(currency, observed_at="2026-10-15T18:56:00Z"):
    # All figures are synthetic TEST DATA, not live market rates.
    rates = {3: 4.00, 6: 3.70, 12: 3.40} if currency == "USD" else {3: 2.50, 6: 2.50, 12: 2.50}
    return {
        "currency": currency,
        "overnight_index": "SOFR" if currency == "USD" else "ESTR",
        "curve_type": "ois_discount_factors",
        "construction": "bootstrapped_from_ois_swaps",
        "market_observed_at": observed_at,
        "source_ref": f"https://vendor.example.test/ois/{currency}",
        "instrument_id": f"DEMO-{currency}-OIS-20261015",
        "discount_factors": [{"months": m, "df": math.exp(-v * m / 1200)}
                             for m, v in rates.items()],
    }


def example_feed():
    return {
        "schema_version": CONTRACT,
        "provider": "Synthetic Test Provider",
        "curves": {"USD": example_curve("USD"), "EUR": example_curve("EUR")},
    }


class OISForwardTests(unittest.TestCase):
    def test_real_ois_discount_factors_become_small_eur_supportive_signal(self):
        result = FedEcbOisForwardAdapter(
            url="https://vendor.example.test/feed", fetcher=lambda url, token: example_feed()
        ).run(NOW)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].belief_id, "eurusd.policy_differential.supportive")
        self.assertEqual(result.evidence[0].direction, 1)
        self.assertEqual(result.evidence[0].evidence_type, "market_ois_forward_spread_3m6m_6m12m")
        self.assertLessEqual(result.evidence[0].strength, 0.22)
        self.assertTrue(result.evidence[0].metadata["serial_state_snapshot"])
        self.assertEqual(result.evidence[0].observed_at, "2026-10-15T18:56:00Z")
        self.assertTrue(result.evidence[0].derived_from)
        self.assertTrue(result.evidence[0].metadata["ois_market_implied_not_official_policy"])
        self.assertEqual(result.observations[0].metadata["source_provenance"], [
            "https://vendor.example.test/ois/USD", "https://vendor.example.test/ois/EUR",
        ])

    def test_widening_far_usd_ois_spread_favours_usd(self):
        feed = example_feed()
        feed["curves"]["USD"]["discount_factors"] = [
            {"months": m, "df": math.exp(-v * m / 1200)}
            for m, v in [(3, 3.4), (6, 3.6), (12, 4.0)]
        ]
        adapter = FedEcbOisForwardAdapter(
            url="https://vendor.example.test/feed", fetcher=lambda url, token: feed
        )
        result = adapter.run(NOW)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].direction, -1)

    def test_unchanged_forward_spread_yields_no_vote(self):
        feed = example_feed()
        feed["curves"]["USD"]["discount_factors"] = [
            {"months": m, "df": math.exp(-4.0 * m / 1200)}
            for m in [3, 6, 12]
        ]
        status, obs = calculate_policy_ois(feed, NOW)
        self.assertIsNone(obs)
        self.assertTrue(status["ready"])
        self.assertEqual(status["signal_status"], "no_material_forward_spread_slope")

    def test_missing_vendor_configuration_is_safe_noop(self):
        adapter = FedEcbOisForwardAdapter(url="")
        self.assertEqual(len(adapter.run(NOW).evidence), 0)
        self.assertEqual(adapter.last_status["reason"], "ois_market_feed_not_configured")

    def test_no_mocked_one_leg_or_old_policy_fallback(self):
        feed = example_feed()
        del feed["curves"]["EUR"]
        adapter = FedEcbOisForwardAdapter(
            url="https://vendor.example.test/feed", fetcher=lambda url, token: feed
        )
        self.assertEqual(len(adapter.run(NOW).evidence), 0)
        self.assertEqual(adapter.last_status["error_code"], "ois_EUR_curve_missing")

    def test_cross_currency_stale_future_or_misaligned_fails_closed(self):
        for invalid in ("2026-10-15T16:00:00Z", "2026-10-15T19:05:00Z",
                        "2026-10-15T18:40:00Z"):
            with self.subTest(invalid=invalid):
                feed = example_feed()
                feed["curves"]["EUR"]["market_observed_at"] = invalid
                adapter = FedEcbOisForwardAdapter(
                    url="https://vendor.example.test/feed", fetcher=lambda url, token: feed
                )
                self.assertFalse(adapter.run(NOW).evidence)
                self.assertFalse(adapter.last_status["ready"])

    def test_par_rate_or_futures_disguised_as_ois_fail_closed(self):
        for changes in [
            {"curve_type": "ois_par_swap_rates"},
            {"construction": "interpolated_from_sofr_futures"},
            {"overnight_index": "FEDFUNDS"},
        ]:
            with self.subTest(changes=changes):
                feed = example_feed()
                feed["curves"]["USD"].update(changes)
                adapter = FedEcbOisForwardAdapter(
                    url="https://vendor.example.test/feed", fetcher=lambda url, token: feed
                )
                self.assertFalse(adapter.run(NOW).evidence)

    def test_missing_duplicate_or_impossible_discount_factors_fail_closed(self):
        for new_factors in [
            [{"months": 3, "df": 0.98}],
            [{"months": 3, "df": 0.99}, {"months": 3, "df": 0.98},
             {"months": 12, "df": 0.95}],
            [{"months": 3, "df": -1}, {"months": 6, "df": 0.99},
             {"months": 12, "df": 0.95}],
        ]:
            feed = example_feed()
            feed["curves"]["USD"]["discount_factors"] = new_factors
            adapter = FedEcbOisForwardAdapter(
                url="https://vendor.example.test/feed", fetcher=lambda url, token: feed
            )
            self.assertFalse(adapter.run(NOW).evidence)

    def test_official_rate_evidence_never_counts_in_new_ois_only_belief(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = BeliefCore(tmp)
            core.register_beliefs(MACRO_BELIEFS)
            legacy = Evidence(
                evidence_id="ev-legacy-official", belief_id="eurusd.policy_differential.supportive",
                source="Fed ECB old levels", observed_at="2026-10-15T18:55:00Z",
                direction=1, strength=0.5, reliability=1.0,
                independence_cluster="fed_ecb:official_policy_spread:30d",
                evidence_type="official_fed_ecb_policy_spread_change_30d",
            )
            core.ingest([legacy])
            core.recompute(NOW)
            stale = core.beliefs["eurusd.policy_differential.supportive"]
            self.assertEqual(stale.probability, 0.5)
            self.assertEqual(stale.freshness_score, 0.0)
            self.assertEqual(stale.representative_evidence_ids, [])
            feed = example_feed()
            valid = FedEcbOisForwardAdapter(
                url="https://vendor.example.test/feed", fetcher=lambda url, token: feed
            ).run(NOW).evidence[0]
            core.ingest([valid])
            core.recompute(NOW)
            active = core.beliefs["eurusd.policy_differential.supportive"]
            self.assertGreater(active.probability, 0.5)
            self.assertGreater(active.freshness_score, 0.95)
            self.assertEqual(active.representative_evidence_ids, [valid.evidence_id])
            core.recompute(NOW + timedelta(hours=3))
            expired = core.beliefs["eurusd.policy_differential.supportive"]
            self.assertEqual(expired.freshness_score, 0.0)

    def test_profile_keeps_only_five_percent_policy_weight(self):
        self.assertEqual(EURUSD_PROFILE_WEIGHTS["eurusd.policy_differential.supportive"], 0.05)
        self.assertEqual(EURUSD_PROFILE_WEIGHTS["eurusd.us_rates_pressure.supportive"], 0.15)


if __name__ == "__main__":
    unittest.main()
