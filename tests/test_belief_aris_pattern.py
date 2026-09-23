import unittest
from datetime import datetime, timedelta, timezone

from belief_aris_pattern import build_pattern_report, validate_pattern_report


def ev(kind, direction=1, mass=0.8):
    return {
        "evidence_type": kind,
        "direction": direction,
        "effective_mass": mass,
    }


def verification(i, outcome, evidence, regime="neutral"):
    return {
        "forecast_id": f"f-{i:03d}",
        "belief_id": "spx.trend.bullish",
        "entity": "SPX",
        "forecast_at": (datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
        "horizon_hours": 24,
        "regime": regime,
        "outcome": outcome,
        "calibration_eligible": True,
        "legacy": False,
        "evidence_snapshot": evidence,
    }


class ARISPatternTests(unittest.TestCase):
    def test_discovers_composed_pattern_with_positive_mdl(self):
        rows = []
        pattern_rows = set(range(20)) | {45, 48, 52, 55, 58}
        for i in range(60):
            pattern = i in pattern_rows
            if pattern:
                evidence = [
                    ev("eps_revision", -1),
                    ev("rates_pressure", 1),
                    ev("noise_a", 1),
                ]
                outcome = False if i == 52 else True
            else:
                evidence = [ev("noise_a", 1), ev("noise_b", -1)]
                outcome = i % 2 == 0
            rows.append(
                verification(
                    i,
                    outcome,
                    evidence,
                    "risk_off" if i % 3 == 0 else "neutral",
                )
            )
        report = build_pattern_report({"verifications": rows})
        validate_pattern_report(report)
        self.assertTrue(report["patterns"])
        found = [
            p
            for p in report["patterns"]
            if {
                "e::eps_revision::-1",
                "e::rates_pressure::+1",
            }.issubset(set(p["atoms"]))
        ]
        self.assertTrue(found)
        p = found[0]
        self.assertGreater(p["discovery"]["mdl_gain_bits"], 0)
        self.assertIn(
            p["status"],
            {"OOS PASS", "REPLICATED", "UNSTABLE", "DISCOVERY"},
        )
        self.assertEqual(p["causal_status"], "ASSOCIATION_ONLY")

    def test_skips_hindsight_only_or_missing_snapshot_rows(self):
        rows = [
            verification(i, bool(i % 2), [ev("a"), ev("b", -1)])
            for i in range(8)
        ]
        rows[0]["legacy"] = True
        rows[1]["calibration_eligible"] = False
        rows[2]["evidence_snapshot"] = []
        report = build_pattern_report({"verifications": rows})
        self.assertEqual(report["sample"]["eligible_verified_forecasts"], 5)
        self.assertEqual(report["sample"]["groups_mined"], 0)

    def test_authority_is_zero(self):
        report = build_pattern_report({"verifications": []})
        self.assertEqual(report["causal_status"], "ASSOCIATION_ONLY")
        self.assertTrue(all(v is False for v in report["authority"].values()))


if __name__ == "__main__":
    unittest.main()
