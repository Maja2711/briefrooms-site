"""P1: Hypothesis Utility Engine acceptance and safety tests."""
import copy
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from hypothesis_utility_engine import build
from forecast_event_identity import identity


def at(day, hour=12):
    return (datetime(2026, 9, 1, hour, tzinfo=timezone.utc) +
            timedelta(days=day)).isoformat().replace("+00:00", "Z")


def frozen(i, *, probability=.8, bid="spx.volatility.benign", target=None,
           version="1", reference=15.0):
    target = target or at(i, 20)
    return {
        "forecast_id": "forecast-p1-%d" % i,
        "belief_id": bid, "predicted_probability": probability,
        "forecast_confidence": .65, "forecast_at": at(i, 12),
        "target_at": target, "outcome_rule": "vix_below_dynamic_cap",
        "entity": "SPX",
        "metadata": {"hypothesis_version": version,
                     "outcome_spec": {"kind": "value_below", "symbol": "^VIX",
                                      "threshold": 20.0, "reference": reference}},
    }


def verification(f, i, *, outcome=True):
    return {"verification_id": "verification-p1-%d" % i,
            "forecast_id": f["forecast_id"], "belief_id": f["belief_id"],
            "forecast_at": f["forecast_at"], "target_at": f["target_at"],
            "verified_at": (datetime.fromisoformat(f["target_at"].replace("Z", "+00:00")) +
                            timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "predicted_probability": f["predicted_probability"],
            "brier_score": round((f["predicted_probability"] - float(outcome)) ** 2, 6),
            "outcome": outcome, "calibration_eligible": True,
            "horizon_bucket": "1S_US_SESSION"}


def setup(rows, outcomes=None):
    return {
        "schema_version": "briefrooms-belief-core-v2",
        "definitions": [{"belief_id": "spx.volatility.benign",
                         "claim": "VIX stays below frozen cap"}],
        "forecasts": rows,
        "verifications": [
            verification(f, i, outcome=outcomes[i] if outcomes else True)
            for i, f in enumerate(rows)
        ],
    }


class HypothesisUtilityP1Tests(unittest.TestCase):
    def test_eight_spx_revisions_one_independent_event_and_no_mutation(self):
        shared_target = at(8, 20)
        f = [frozen(i, probability=.4 + i * .01, target=shared_target,
                    reference=14.9 + i * .1) for i in range(8)]
        state = setup(f)
        before = copy.deepcopy(state)
        report = build(state, now="2026-10-08T11:00:00Z")
        self.assertEqual(state, before)
        self.assertEqual(8, report["source"]["matched_eligible_forecasts"])
        self.assertEqual(1, report["source"]["independent_resolved_events"])
        self.assertEqual(7, report["source"]["revision_diagnostics"]["excluded_revisions"])
        utility = report["hypotheses"]["spx.volatility.benign"]
        self.assertEqual(1, utility["predictive_utility"]["independent_events"])
        self.assertEqual("COLLECTING", utility["lifecycle_status"])
        self.assertAlmostEqual(.36, utility["predictive_utility"]["brier"])
        self.assertFalse(report["authority"]["production_writeback"])
        self.assertFalse(report["authority"]["automatic_retirement"])
        self.assertFalse(report["authority"]["frozen_forecast_mutation"])

    def test_predictive_sufficient_sample_and_prequential_no_future_labels(self):
        forecasts = [frozen(i, probability=.9) for i in range(32)]
        state = setup(forecasts)
        # Every settlement becomes known a month AFTER the forecasts: using
        # empirical prevalence at report-time here would be outcome leakage.
        for v in state["verifications"]:
            v["verified_at"] = at(90)
        report = build(state)
        p = report["hypotheses"]["spx.volatility.benign"]["predictive_utility"]
        self.assertEqual(32, p["independent_events"])
        self.assertEqual(32, p["distinct_target_dates"])
        self.assertAlmostEqual(.01, p["brier"])
        # Prequential benchmark remains 0.5: gain = .25 - .01.
        self.assertAlmostEqual(.24, p["brier_gain_vs_prequential_base_rate"])
        self.assertEqual("ACTIVE", p["status"])

    def test_review_for_miscalibration_never_auto_retires(self):
        forecasts = [frozen(i, probability=.95) for i in range(33)]
        state = setup(forecasts, outcomes=[False] * 33)
        report = build(state)
        h = report["hypotheses"]["spx.volatility.benign"]
        self.assertIn(h["lifecycle_status"], ("REVIEW", "CHALLENGER"))
        self.assertFalse(h["automatic_retirement"])
        self.assertFalse(report["authority"]["automatic_promotion"])

    def test_frozen_threshold_and_version_separate_outcomes(self):
        a = frozen(0)
        b = frozen(1, target=a["target_at"], reference=16.0)
        b["forecast_at"] = at(-1, 12)
        b["metadata"]["outcome_spec"]["threshold"] = 21.0
        c = frozen(2, target=a["target_at"], version="2")
        c["forecast_at"] = at(-2, 12)
        self.assertEqual(3, len({identity(f)["event_id"] for f in [a, b, c]}))

    def test_model_freeze_release_not_an_hypothesis_version(self):
        a = frozen(0, target=at(8, 20))
        b = frozen(1, target=at(8, 20))
        a["metadata"]["model_freeze_version"] = "belief-core-v2-shadow-2026-09-27"
        b["metadata"]["model_freeze_version"] = "belief-core-v2-shadow-2026-10-08"
        r = build(setup([a, b]))
        self.assertEqual(1, r["source"]["independent_resolved_events"])
        self.assertEqual(1, r["summary"]["hypothesis_versions"])

    def test_research_requires_real_settlement_and_no_cost_imputation(self):
        forecasts = [frozen(i, probability=.8) for i in range(12)]
        state = setup(forecasts)
        records = []
        for i, f in enumerate(forecasts):
            v = state["verifications"][i]
            records.append({
                "attempt_id": "a%d" % i,
                "attempted_at": f["forecast_at"],
                "belief_id": f["belief_id"], "forecast_id": f["forecast_id"],
                "evidence_ids": ["source-%d" % i],
                "p_before": .6,
                "research_result_status": "SETTLED",
                "future_metrics": {"verification_id": v["verification_id"],
                                   "brier_gain_vs_pre_research": round((.6 - 1)**2 - .04, 6)}
            })
        # Invalid / forged attribution is not admitted.
        records.append({
            "attempt_id": "bad", "belief_id": forecasts[0]["belief_id"],
            "forecast_id": forecasts[0]["forecast_id"], "p_before": .6,
            "future_metrics": {"verification_id": state["verifications"][0]["verification_id"],
                               "brier_gain_vs_pre_research": 0.999}
        })
        experience = {"schema_version": "briefrooms-l3a-experience-state-v1",
                      "records": records}
        report = build(state, experience)
        ru = report["hypotheses"]["spx.volatility.benign"]["research_utility"]
        self.assertEqual(12, ru["settled_independent_events"])
        self.assertEqual(1, ru["unvalidated_settlement_records"])
        self.assertEqual("POSITIVE_ASSOCIATION", ru["status"])
        self.assertAlmostEqual(.12, ru["mean_brier_gain_vs_pre_research"])
        self.assertIsNone(ru["measured_cost_usd"])
        self.assertIsNone(ru["gain_per_usd"])
        self.assertEqual("NOT_INSTRUMENTED_OR_INCOMPLETE", ru["cost_data_status"])

    def test_duplicate_research_attempts_do_not_inflate_samples(self):
        forecasts = [frozen(0, probability=.8)]
        state = setup(forecasts)
        v = state["verifications"][0]
        base = {
            "belief_id": forecasts[0]["belief_id"],
            "forecast_id": forecasts[0]["forecast_id"],
            "p_before": .6,
            "future_metrics": {"verification_id": v["verification_id"],
                               "brier_gain_vs_pre_research": .12},
        }
        experience = {"schema_version": "briefrooms-l3a-experience-state-v1",
                      "records": [{**base, "attempt_id": "a"}, {**base, "attempt_id": "b"}]}
        ru = build(state, experience)["hypotheses"]["spx.volatility.benign"]["research_utility"]
        self.assertEqual(2, ru["research_attempts_observed"])
        self.assertEqual(1, ru["settled_independent_events"])

    def test_conflicting_verification_outcomes_are_quarantined(self):
        forecasts = [frozen(i, target=at(8, 20)) for i in range(2)]
        state = setup(forecasts, outcomes=[True, False])
        report = build(state)
        self.assertEqual(1, report["source"]["revision_diagnostics"]["conflict_event_count"])
        self.assertEqual(0, report["source"]["independent_resolved_events"])
        self.assertEqual("COLLECTING",
                         report["hypotheses"]["spx.volatility.benign"]["lifecycle_status"])

    def test_bad_l3a_schema_rejected(self):
        with self.assertRaises(ValueError):
            build(setup([]), {"schema_version": "wrong", "records": []})


if __name__ == "__main__":
    unittest.main()
