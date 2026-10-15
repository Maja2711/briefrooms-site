from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from belief_fed_ecb_policy_adapter import (  # noqa: E402
    FedEcbPolicyDifferentialAdapter,
    build_policy_spread,
)
from epistemic_consumer_interface import EURUSD_PROFILE_WEIGHTS  # noqa: E402

NOW = datetime(2026, 10, 15, 19, 0, tzinfo=timezone.utc)
FED = "observation_date,DFEDTARU\n2026-09-12,4.00\n2026-10-14,3.75\n"
ECB = "TIME_PERIOD,OBS_VALUE\n2026-08-01,2.50\n2026-10-01,2.50\n"


class PolicyDifferentialTests(unittest.TestCase):
    def test_fed_cut_relative_to_unchanged_ecb_is_eur_supportive(self):
        result = FedEcbPolicyDifferentialAdapter(
            fetcher=lambda url: FED if "fred.stlouisfed.org" in url else ECB
        ).run(NOW)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].belief_id, "eurusd.policy_differential.supportive")
        self.assertEqual(result.evidence[0].direction, 1)
        self.assertAlmostEqual(result.observations[0].value, -0.25)
        metadata = result.evidence[0].metadata
        self.assertEqual(metadata["fed_series"], "DFEDTARU")
        self.assertEqual(metadata["ecb_series"], "FM.B.U2.EUR.4F.KR.DFR.LEV")
        self.assertTrue(metadata["serial_state_snapshot"])
        self.assertTrue(metadata["no_trade_authority"])

    def test_ecb_cut_relative_to_unchanged_fed_is_usd_supportive(self):
        fed = "DATE,DFEDTARU\n2026-09-12,3.75\n2026-10-14,3.75\n"
        ecb = "TIME_PERIOD,OBS_VALUE\n2026-08-01,2.75\n2026-10-01,2.50\n"
        r = FedEcbPolicyDifferentialAdapter(
            fetcher=lambda url: fed if "fred.stlouisfed.org" in url else ecb
        ).run(NOW)
        self.assertEqual(len(r.evidence), 1)
        self.assertEqual(r.evidence[0].direction, -1)
        self.assertLessEqual(r.evidence[0].strength, 0.24)

    def test_cancelled_relative_rate_change_is_no_signal(self):
        fed = "DATE,DFEDTARU\n2026-09-12,4.00\n2026-10-14,3.75\n"
        ecb = "TIME_PERIOD,OBS_VALUE\n2026-08-01,2.75\n2026-10-01,2.50\n"
        status, row = build_policy_spread(NOW, fed, ecb)
        self.assertTrue(status["ready"])
        self.assertEqual(status["reason"], "no_material_spread_change")
        self.assertIsNone(row)

    def test_future_dated_fed_observation_does_not_enter_decision(self):
        future = "DATE,DFEDTARU\n2026-09-12,4.00\n2026-10-16,3.50\n"
        result = FedEcbPolicyDifferentialAdapter(
            fetcher=lambda url: future if "fred.stlouisfed.org" in url else ECB
        ).run(NOW)
        self.assertEqual(len(result.evidence), 0)
        self.assertFalse(result.last_status["ready"])
        self.assertEqual(result.last_status["reason"], "fed_daily_rate_stale")

    def test_stale_fed_or_empty_ecb_fails_closed(self):
        old = "DATE,DFEDTARU\n2026-09-12,4.00\n2026-10-10,3.75\n"
        adapter = FedEcbPolicyDifferentialAdapter(
            fetcher=lambda url: old if "fred.stlouisfed.org" in url else ECB
        )
        self.assertEqual(len(adapter.run(NOW).evidence), 0)
        self.assertEqual(adapter.last_status["reason"], "fed_daily_rate_stale")
        bad = FedEcbPolicyDifferentialAdapter(
            fetcher=lambda url: FED if "fred.stlouisfed.org" in url else "invalid"
        )
        self.assertEqual(len(bad.run(NOW).evidence), 0)
        self.assertEqual(bad.last_status["reason"], "official_source_unavailable")

    def test_no_lookback_predecessor_fails_closed(self):
        fed = "DATE,DFEDTARU\n2026-10-13,3.75\n2026-10-14,3.75\n"
        status, obs = build_policy_spread(NOW, fed, ECB)
        self.assertFalse(status["ready"])
        self.assertIsNone(obs)

    def test_production_policy_weight_is_capped_at_five_percent(self):
        self.assertEqual(EURUSD_PROFILE_WEIGHTS["eurusd.policy_differential.supportive"], 0.05)
        self.assertGreater(EURUSD_PROFILE_WEIGHTS["eurusd.us_rates_pressure.supportive"], 0.05)


if __name__ == "__main__":
    unittest.main()
