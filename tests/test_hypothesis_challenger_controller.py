"""P2 Evolution Controller handoff: only independently revalidated OOS gates."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from briefrooms_evolution_controller import _default_state, _ingest_hypothesis_challengers
from hypothesis_challenger_engine import run, SCHEMA
from tests.test_hypothesis_challenger_engine import candidate, registry, utility, forecast, verification, stamp


def full_pass():
    state = {"forecasts": [], "verifications": []}
    c = candidate()
    current = registry(c)
    for day in range(1, 51):
        if day > 1:
            state["verifications"].append(verification(state["forecasts"][-1]))
        state["forecasts"].append(forecast(day))
        current = run(state, utility(), current, now=stamp(day, 12))
    state["verifications"].append(verification(state["forecasts"][-1]))
    result = run(state, utility(), current, now=stamp(51, 12))
    assert result["summary"]["gate_pass"] == 1, result["summary"]
    return state, result, c["candidate_id"]


class EvolutionP2HandoffTests(unittest.TestCase):
    def _ingest(self, raw, state):
        with tempfile.TemporaryDirectory() as tmp:
            evo = _default_state()
            _ingest_hypothesis_challengers(evo, raw, state, stamp(51, 12),
                                           Path(tmp) / "audit.jsonl")
            return evo

    def test_verified_pass_registers_promotion_eligible_without_production_write(self):
        state, raw, cid = full_pass()
        evo = self._ingest(raw, state)
        self.assertEqual("PROMOTION_ELIGIBLE", evo["candidates"][cid]["status"])
        self.assertFalse(evo["candidates"][cid]["automatic_promotion_allowed"])
        self.assertFalse(evo["candidates"][cid]["trade_execution_authority"])
        self.assertEqual("manual_hypothesis_probability_review",
                         evo["candidates"][cid]["promotion_route"])
        self.assertEqual("PASS", evo["promotion_gates"][cid]["status"])
        self.assertEqual(50, evo["promotion_gates"][cid]["observed_sample"])
        self.assertEqual(1, evo["source_status"]["hypothesis_challengers"]["gate_pass"])
        self.assertTrue(any(a["candidate_id"] == cid and
                            a["action"] == "REQUEST_OWNER_REVIEW_AND_CONTROLLED_PROMOTION" and
                            a["materialization_authority"] is False
                            for a in evo["delegated_actions"]))
        self.assertEqual({}, evo["production_versions"])

    def test_forged_pass_without_real_shadow_observations_is_held(self):
        c = candidate()
        fake = registry(c)
        fake.update({"schema_version": SCHEMA,
                     "generated_at": stamp(51, 12),
                     "authority": {"automatic_production_promotion": False,
                                   "frozen_forecast_mutation": False,
                                   "trade_execution": False}})
        fake["candidates"][c["candidate_id"]]["gate"]["status"] = "PASS"
        fake["candidates"][c["candidate_id"]]["gate"]["observed_sample"] = 999
        evo = self._ingest(fake, {"forecasts": [], "verifications": []})
        self.assertEqual("PARKED", evo["candidates"][c["candidate_id"]]["status"])
        self.assertEqual("HOLD", evo["promotion_gates"][c["candidate_id"]]["status"])
        self.assertEqual(0, evo["source_status"]["hypothesis_challengers"]["gate_pass"])
        self.assertFalse(evo["delegated_actions"])

    def test_later_conflicting_verified_outcome_revokes_pass(self):
        state, raw, cid = full_pass()
        other = copy.deepcopy(state["verifications"][-1])
        other["verification_id"] = "new-counter-verification"
        other["outcome"] = True
        state["verifications"].append(other)
        evo = self._ingest(raw, state)
        self.assertEqual("HOLD", evo["promotion_gates"][cid]["status"])
        self.assertEqual("PARKED", evo["candidates"][cid]["status"])
        self.assertEqual(0, evo["source_status"]["hypothesis_challengers"]["gate_pass"])

    def test_missing_p2_state_revokes_previous_gate(self):
        state, raw, cid = full_pass()
        evo = self._ingest(raw, state)
        with tempfile.TemporaryDirectory() as tmp:
            _ingest_hypothesis_challengers(evo, None, state, stamp(52, 12),
                                           Path(tmp) / "audit.jsonl")
        self.assertEqual("PARKED", evo["candidates"][cid]["status"])
        self.assertEqual("HOLD", evo["promotion_gates"][cid]["status"])
        self.assertEqual("p2_challenger_state_unavailable",
                         evo["source_status"]["hypothesis_challengers"]["reason"])

    def test_unauthorized_auto_promotion_is_rejected(self):
        c = candidate()
        source = registry(c)
        source["generated_at"] = stamp(51, 12)
        source["authority"] = {"automatic_production_promotion": True,
                               "frozen_forecast_mutation": False,
                               "trade_execution": False}
        with self.assertRaises(RuntimeError):
            self._ingest(source, {"forecasts": [], "verifications": []})


if __name__ == "__main__":
    unittest.main()
