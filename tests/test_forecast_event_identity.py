"""P0 regression: one market outcome cannot be counted eight times."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from forecast_event_identity import identity, canonical_event_rows


def frozen(i, threshold=20.0, target="2026-10-08T20:00:00Z"):
    return {
        "forecast_id": "l3af-test-%02d" % i,
        "belief_id": "spx.volatility.benign",
        "entity": "SPX",
        "outcome_rule": "vix_below_dynamic_cap",
        "forecast_at": "2026-10-07T%02d:00:00Z" % (i + 1),
        "target_at": target,
        "predicted_probability": 0.40 + .01 * i,
        "metadata": {"hypothesis_version": "1",
                     "outcome_spec": {"kind": "value_below", "symbol": "^VIX",
                                      "reference": 15.0 + .10 * i,
                                      "threshold": threshold}},
    }


class P0EventIdentityTests(unittest.TestCase):
    def test_eight_spx_revisions_one_independent_outcome(self):
        forecasts = [frozen(i) for i in range(8)]
        ids = [identity(f) for f in forecasts]
        self.assertEqual(1, len({v["event_id"] for v in ids}))
        self.assertEqual(8, len({v["forecast_revision_id"] for v in ids}))
        scored, audit = canonical_event_rows([
            {"f": f, "p": f["predicted_probability"], "y": 1.0,
             "brier": (f["predicted_probability"] - 1) ** 2}
            for f in forecasts
        ])
        self.assertEqual(1, len(scored))
        self.assertEqual(8, audit["raw_forecast_count"])
        self.assertEqual(1, audit["independent_event_count"])
        self.assertEqual(7, audit["excluded_revisions"])
        self.assertEqual(forecasts[0]["forecast_id"], scored[0]["f"]["forecast_id"])
        self.assertAlmostEqual(.36, scored[0]["brier"])

    def test_distinct_frozen_threshold_never_collapses(self):
        self.assertNotEqual(identity(frozen(0))["event_id"],
                            identity(frozen(1, threshold=22.0))["event_id"])

    def test_version_change_separates_hypothesis_contract(self):
        a, b = frozen(0), frozen(1)
        b["metadata"]["hypothesis_version"] = "2"
        self.assertNotEqual(identity(a)["event_id"], identity(b)["event_id"])

    def test_legacy_missing_contract_does_not_merge_on_same_target(self):
        a, b = frozen(0), frozen(1)
        a["metadata"].pop("outcome_spec")
        b["metadata"].pop("outcome_spec")
        self.assertNotEqual(identity(a)["event_id"], identity(b)["event_id"])

    def test_conflicting_settlements_quarantined(self):
        a, b = frozen(0), frozen(1)
        scored, audit = canonical_event_rows([
            {"f": a, "y": 0.0}, {"f": b, "y": 1.0}
        ])
        self.assertEqual([], scored)
        self.assertEqual(1, audit["conflict_event_count"])
        self.assertEqual(2, audit["quarantined_forecasts"])

    def test_revisions_are_immutable_keys(self):
        a = frozen(0)
        original = identity(a)
        a["predicted_probability"] = .9
        self.assertEqual(original, identity(a))
        self.assertEqual("spx.volatility.benign", original["hypothesis_id"])


if __name__ == "__main__":
    unittest.main()
