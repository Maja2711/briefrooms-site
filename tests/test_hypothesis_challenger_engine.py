"""L3 P2 E2E: preregistered Shadow/OOS, immutable event weighting, strict gates."""
import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from hypothesis_challenger_engine import (
    SCHEMA, _new_candidate, _gate, _settle, _freeze, _candidate_key,
    fit_candidate, run, public_view, valid_resolved,
)
from forecast_event_identity import identity

DAY0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
BID = "btc.liquidity.supportive"
HORIZON = "1S_US_SESSION"


def stamp(day, hour=12, minutes=0):
    dt = DAY0 + timedelta(days=day, hours=hour, minutes=minutes)
    return dt.isoformat().replace("+00:00", "Z")


def forecast(day, idx=0, p=.7, target_day=None, target_hour=20):
    return {
        "forecast_id": "f-%d-%d" % (day, idx),
        "belief_id": BID, "entity": "BTC",
        "forecast_at": stamp(day, 9, idx),
        "target_at": stamp(target_day if target_day is not None else day, target_hour),
        "outcome_rule": "vix_below_dynamic_cap",
        "predicted_probability": p, "forecast_confidence": .7,
        "metadata": {
            "hypothesis_version": "1",
            "calibration_horizon_bucket": HORIZON,
            "outcome_spec": {"kind": "value_below", "symbol": "^VIX", "threshold": 20.0,
                             "reference": 15.0 + idx / 10.0},
        },
    }


def verification(f, outcome=False, suffix=""):
    dt = datetime.fromisoformat(f["target_at"].replace("Z", "+00:00")) + timedelta(minutes=5)
    return {
        "verification_id": "v-" + f["forecast_id"] + suffix,
        "forecast_id": f["forecast_id"],
        "belief_id": f["belief_id"], "calibration_eligible": True,
        "verified_at": dt.isoformat().replace("+00:00", "Z"),
        "outcome": outcome,
    }


def candidate(now=None, transform=None):
    fit = {
        "transform": transform or {"type": "logit_affine_v1", "intercept": -1.5, "slope": .7},
        "source_event_ids_hash": "historical-verifications-hash",
        "discovery_n": 40, "validation_n": 20,
    }
    return _new_candidate(BID, "1", HORIZON, fit, now or DAY0, 1)


def utility(status="COLLECTING"):
    return {"schema_version": "briefrooms-hypothesis-utility-v1",
            "hypotheses": {BID: {"hypothesis_version": "1",
                                 "lifecycle_status": status}}}


def registry(c):
    return {"schema_version": SCHEMA, "candidates": {c["candidate_id"]: c}}


class HypothesisChallengerE2ETests(unittest.TestCase):
    def test_real_prospective_oos_pass_only_after_50_unique_settlements(self):
        state = {"forecasts": [], "verifications": []}
        c = candidate(DAY0)
        initial_frozen = copy.deepcopy(c["shadow_forecasts"])
        prior = registry(c)
        for day in range(1, 52):
            if day > 1:
                state["verifications"].append(verification(state["forecasts"][-1]))
            state["forecasts"].append(forecast(day))
            output = run(state, utility(), prior, now=stamp(day, 12))
            prior = output
            current = output["candidates"][c["candidate_id"]]
            self.assertEqual(day, len(current["shadow_forecasts"]))
            self.assertEqual(day-1, len(current["settlements"]))
            self.assertEqual("OOS_RUNNING", current["status"])
        state["verifications"].append(verification(state["forecasts"][-1]))
        output = run(state, utility(), prior, now=stamp(52, 12))
        selected = output["candidates"][c["candidate_id"]]
        self.assertEqual("PROMOTION_ELIGIBLE", selected["status"])
        self.assertEqual("PASS", selected["gate"]["status"])
        self.assertEqual(51, selected["gate"]["observed_sample"])
        self.assertEqual(51, selected["gate"]["distinct_target_dates"])
        self.assertEqual(51, len(selected["shadow_forecasts"]))
        self.assertEqual(51, len(selected["settlements"]))
        self.assertFalse(output["authority"]["automatic_production_promotion"])
        self.assertEqual(0, output["summary"]["production_promotions"])
        # Re-run: commitments/results must not be overwritten or duplicated.
        again = run(state, utility(), output, now=stamp(52, 12))
        actual = again["candidates"][c["candidate_id"]]
        self.assertEqual(actual["shadow_forecasts"], selected["shadow_forecasts"])
        self.assertEqual(actual["settlements"], selected["settlements"])
        self.assertEqual(0, len(again["events_this_run"]))
        self.assertEqual(1, again["summary"]["promotion_eligible"])
        public = public_view(again)
        self.assertEqual("SHADOW_OOS_GOVERNED", public["status"])
        self.assertNotIn("shadow_forecasts", public["candidates"][0])
        self.assertFalse(public["authority"]["production_writeback"])

    def test_same_event_eight_revisions_freeze_as_one(self):
        c = candidate(DAY0)
        state = {"forecasts": [forecast(2, i, p=.5 + i * .01, target_day=3) for i in range(8)],
                 "verifications": []}
        result = run(state, utility(), registry(c), now=stamp(2, 12))
        cand = result["candidates"][c["candidate_id"]]
        self.assertEqual(1, len(cand["shadow_forecasts"]))
        self.assertEqual(state["forecasts"][0]["forecast_id"],
                         next(iter(cand["shadow_forecasts"].values()))["forecast_id"])
        for f in state["forecasts"]:
            state["verifications"].append(verification(f))
        next_result = run(state, utility(), result, now=stamp(4, 12))
        self.assertEqual(1, len(next_result["candidates"][c["candidate_id"]]["settlements"]))

    def test_no_freeze_from_past_target_or_forecast_pre_activation(self):
        c = candidate(DAY0 + timedelta(days=5))
        state = {"forecasts": [forecast(2), forecast(6, target_day=6)],
                 "verifications": []}
        output = run(state, utility(), registry(c), now=stamp(7, 12))
        self.assertEqual(0, output["summary"]["frozen_shadow_forecasts"])

    def test_no_challenger_score_without_prior_commitment(self):
        c = candidate()
        f = forecast(2)
        state = {"forecasts": [f], "verifications": [verification(f)]}
        result = run(state, utility(), registry(c), now=stamp(3, 12))
        self.assertEqual(0, result["summary"]["settled_oos_events"])
        self.assertEqual(0, result["summary"]["frozen_shadow_forecasts"])

    def test_immutability_violation_and_conflicts_hold_gate(self):
        c = candidate()
        a = forecast(2)
        state = {"forecasts": [a], "verifications": []}
        reg = run(state, utility(), registry(c), now=stamp(2, 12))
        f = state["forecasts"][0]
        f["predicted_probability"] = .8
        state["verifications"].append(verification(f))
        result = run(state, utility(), reg, now=stamp(3, 12))
        item = result["candidates"][c["candidate_id"]]
        self.assertEqual("HOLD", item["status"])
        self.assertIn("identity_or_outcome_conflict", item["gate"]["blockers"])
        self.assertEqual(0, item["gate"]["observed_sample"])

    def test_no_automatic_creation_from_review_or_insufficient_sample(self):
        forecasts = [forecast(i) for i in range(1, 13)]
        state = {"forecasts": forecasts, "verifications": [verification(f) for f in forecasts]}
        before = copy.deepcopy(state)
        out = run(state, utility("REVIEW"), now=stamp(20))
        self.assertEqual(0, out["summary"]["candidates_total"])
        out = run(state, utility("CHALLENGER"), now=stamp(20))
        self.assertEqual(0, out["summary"]["candidates_total"])
        self.assertEqual(state, before)

    def test_discovery_only_uses_verified_outcomes_and_date_holdout(self):
        f = [forecast(i, p=.65) for i in range(1, 81)]
        state = {"forecasts": f,
                 "verifications": [verification(x) for x in f[:70]]}
        rows, d = valid_resolved(state, datetime.fromisoformat(stamp(80).replace("Z","+00:00")))
        self.assertEqual(70, len(rows))
        fit = fit_candidate(rows)
        self.assertIsNotNone(fit)
        self.assertGreaterEqual(fit["discovery_n"], 30)
        self.assertGreaterEqual(fit["validation_n"], 15)
        self.assertGreaterEqual(fit["validation_brier_relative_improvement"], .02)
        out = run(state, utility("CHALLENGER"), now=stamp(80))
        self.assertEqual(1, out["summary"]["new_candidate_count"])
        chosen = list(out["candidates"].values())[0]
        self.assertEqual("OOS_RUNNING", chosen["status"])
        self.assertEqual(0, len(chosen["shadow_forecasts"]))
        self.assertGreaterEqual(chosen["discovery"]["validation_n"], 15)

    def test_gate_fails_when_ece_or_block_unsafe(self):
        c = candidate()
        rows = {}
        settles = {}
        for i in range(1, 61):
            f = forecast(i, p=.7)
            id_ = identity(f)["event_id"]
            rows[id_] = {
                "event_id": id_, "forecast_id": f["forecast_id"],
                "raw_probability": .7,
                "challenger_probability": .999,
                "forecast_at": f["forecast_at"], "target_at": f["target_at"],
                "frozen_at": stamp(i, 12),
            }
            settles[id_] = {"event_id": id_, "outcome": False,
                           "target_at": f["target_at"]}
        c["shadow_forecasts"] = rows
        c["settlements"] = settles
        g = _gate(c, [], datetime.fromisoformat(stamp(70).replace("Z","+00:00")))
        self.assertEqual("FAIL", g["status"])
        self.assertIn("brier_relative_improvement_below_5pct", g["blockers"])

    def test_invalid_prior_schema_fails_closed(self):
        with self.assertRaises(ValueError):
            run({"forecasts": [], "verifications": []}, utility(),
                {"schema_version": "bad"}, now=stamp(5))


if __name__ == "__main__":
    unittest.main()
