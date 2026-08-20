import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.belief_epistemic_causal_graph import (
    CAUSAL_STATUS,
    CONTRACT_VERSION,
    PR15_CONTRACT_VERSION,
    STATE_FILENAME,
    _assert_dag,
    build_epistemic_contracts,
    build_graph,
    capabilities,
    run,
    safety_controls,
)
from scripts.brace_broad_market_belief import BROAD_MARKET_BELIEFS, RATES


T0 = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def forecast(fid, belief_id, forecast_at="2026-08-20T13:30:00Z"):
    return {
        "forecast_id": fid,
        "belief_id": belief_id,
        "forecast_at": forecast_at,
        "target_at": "2026-12-18T13:30:00Z",
        "predicted_probability": 0.6,
        "confidence": 0.5,
        "metadata": {"engine_influence": False, "historical_backfill": False},
    }


def pr15_report(beliefs=("entity.amzn.revenue_durability",), forecasts=()):
    return {
        "contract_version": PR15_CONTRACT_VERSION,
        "mode": "research_shadow",
        "active_decision_influence": False,
        "belief_states": [{"belief_id": b, "probability": 0.5, "confidence": 0.0} for b in beliefs],
        "forecasts": list(forecasts),
    }


class BeliefEpistemicCausalGraphTests(unittest.TestCase):
    def test_governance_zero_authority(self):
        self.assertTrue(all(v is False for v in safety_controls().values()))
        caps = capabilities()
        self.assertTrue(caps["epistemic_contract_registry_enabled"])
        self.assertTrue(caps["causal_hypothesis_graph_enabled"])
        self.assertFalse(caps["causal_claim_verification_enabled"])
        self.assertFalse(caps["causal_graph_alpha_enabled"])
        self.assertFalse(caps["promotion_gate_enabled"])

    def test_contracts_match_canonical_claim_and_have_epistemic_fields(self):
        contracts = build_epistemic_contracts(("entity.amzn.revenue_durability",))
        canonical_rates = next(x for x in BROAD_MARKET_BELIEFS if x.belief_id == RATES)
        self.assertEqual(contracts[RATES]["claim"], canonical_rates.claim)
        self.assertEqual(contracts["entity.amzn.revenue_durability"]["claim"], "AMZN reported revenue durability will remain supportive at the next comparable reporting outcome.")
        for contract in contracts.values():
            self.assertEqual(contract["contract_version"], CONTRACT_VERSION)
            self.assertTrue(contract["causal_assumptions"])
            self.assertTrue(contract["transmission_path"])
            self.assertTrue(contract["falsifiers"])
            self.assertTrue(contract["alternative_explanations"])
            self.assertTrue(contract["unknowns"])
            self.assertTrue(contract["regime_dependencies"])
            self.assertTrue(contract["measurement_limitations"])
            self.assertTrue(any(x["machine_testable"] for x in contract["falsifiers"]))
            self.assertFalse(contract["causal_proof"])
            self.assertFalse(contract["decision_influence"])

    def test_measurement_semantics_distinguish_proxy_from_entity_target(self):
        contracts = build_epistemic_contracts(("entity.amzn.revenue_durability",))
        self.assertTrue(contracts[RATES]["measurement_relation"].startswith("PARTIAL"))
        self.assertEqual(
            contracts["entity.amzn.revenue_durability"]["measurement_relation"],
            "DIRECT_TO_REVIEWED_PR14_INTERPRETATION_CONTRACT",
        )
        self.assertIn("not stock return", contracts["entity.amzn.revenue_durability"]["measurement_limitations"][0])

    def test_graph_edges_are_unverified_hypotheses_and_no_engine_nodes(self):
        graph = build_graph(build_epistemic_contracts(("entity.amzn.revenue_durability",)))
        self.assertEqual(graph["causal_status"], CAUSAL_STATUS)
        self.assertFalse(graph["causal_proof"])
        self.assertFalse(graph["correlation_to_causation_inference"])
        for edge in graph["edges"]:
            self.assertEqual(edge["causal_status"], CAUSAL_STATUS)
            self.assertFalse(edge["causal_proof"])
            self.assertFalse(edge["decision_influence"])
        self.assertFalse(any(str(n["node_id"]).startswith("engine.") for n in graph["nodes"]))

    def test_causal_graph_rejects_cycles(self):
        with self.assertRaises(ValueError):
            _assert_dag(
                ("a", "b"),
                (
                    {"source": "a", "target": "b"},
                    {"source": "b", "target": "a"},
                ),
            )

    def _run(self, root: Path, payload, at):
        source = root / "pr15.json"
        write_json(source, payload)
        return run(root / "state", pr15_report_path=source, as_of=at)

    def test_first_run_existing_forecast_is_cursor_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = forecast("f-existing", "entity.amzn.revenue_durability", "2026-08-20T12:30:00Z")
            report = self._run(root, pr15_report(forecasts=(existing,)), T0)
            self.assertTrue(report["sample"]["activation_only_this_run"])
            self.assertEqual(report["sample"]["pre_activation_forecasts_total"], 1)
            self.assertEqual(report["sample"]["forecast_bindings_total"], 0)
            self.assertTrue(report["prospective_binding"]["first_run_existing_forecasts_cursor_only"])

    def test_new_forecast_binds_only_to_preexisting_graph_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, pr15_report(), T0)
            new = forecast("f-new", "entity.amzn.revenue_durability", "2026-08-20T13:30:00Z")
            report = self._run(root, pr15_report(forecasts=(new,)), T1)
            self.assertEqual(report["sample"]["forecast_bindings_total"], 1)
            binding = report["forecast_bindings"][0]
            self.assertTrue(binding["prospective"])
            self.assertFalse(binding["historical_backfill"])
            self.assertFalse(binding["retroactive_causal_classification"])
            state = json.loads((root / "state" / STATE_FILENAME).read_text())
            snapshot = state["graph_snapshots"][binding["graph_snapshot_id"]]
            self.assertLessEqual(snapshot["created_at"], new["forecast_at"])

    def test_new_entity_forecast_without_preexisting_contract_snapshot_is_terminal_unbound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, pr15_report(), T0)
            msft_belief = "entity.msft.revenue_durability"
            new = forecast("f-msft", msft_belief, "2026-08-20T13:30:00Z")
            report = self._run(root, pr15_report(beliefs=("entity.amzn.revenue_durability", msft_belief), forecasts=(new,)), T1)
            self.assertEqual(report["sample"]["forecast_bindings_total"], 0)
            self.assertEqual(report["sample"]["terminal_unbound_forecasts_total"], 1)
            self.assertEqual(report["terminal_unbound_forecasts"][0]["status"], "no_preexisting_epistemic_contract_snapshot")
            report2 = self._run(root, pr15_report(beliefs=("entity.amzn.revenue_durability", msft_belief), forecasts=(new,)), T2)
            self.assertEqual(report2["sample"]["forecast_bindings_total"], 0)
            self.assertEqual(report2["sample"]["terminal_unbound_forecasts_total"], 1)

    def test_future_dated_forecast_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, pr15_report(), T0)
            future = forecast("f-future", "entity.amzn.revenue_durability", "2026-08-20T15:30:00Z")
            report = self._run(root, pr15_report(forecasts=(future,)), T1)
            self.assertEqual(report["terminal_unbound_forecasts"][0]["status"], "future_dated_forecast_rejected")

    def test_repeated_source_is_idempotent_and_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, pr15_report(), T0)
            new = forecast("f-new", "entity.amzn.revenue_durability")
            report1 = self._run(root, pr15_report(forecasts=(new,)), T1)
            state1 = json.loads((root / "state" / STATE_FILENAME).read_text())
            report2 = self._run(root, pr15_report(forecasts=(new,)), T2)
            state2 = json.loads((root / "state" / STATE_FILENAME).read_text())
            self.assertEqual(state1["forecast_bindings"], state2["forecast_bindings"])
            self.assertEqual(state1["graph_snapshots"], state2["graph_snapshots"])
            self.assertEqual(report1["sample"]["forecast_bindings_total"], report2["sample"]["forecast_bindings_total"])

    def test_report_makes_no_causal_or_alpha_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(Path(tmp), pr15_report(), T0)
            self.assertEqual(report["sample"]["verified_causal_edges"], 0)
            self.assertFalse(report["graph_runtime"]["causal_proof"])
            self.assertFalse(report["graph_runtime"]["correlation_to_causation_inference"])
            self.assertTrue(report["research_boundary"]["graph_edges_are_hypotheses_not_causal_proof"])
            self.assertFalse(report["research_boundary"]["alpha_score_enabled"])
            self.assertFalse(report["research_boundary"]["miv_score_enabled"])
            self.assertEqual(report["promotion"]["status"], "NOT_ELIGIBLE_FOR_PROMOTION_REVIEW")
            self.assertGreater(report["measurement_epistemics"]["explicit_claim_outcome_gap_count"], 0)


if __name__ == "__main__":
    unittest.main()
