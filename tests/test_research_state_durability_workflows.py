from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]

WORKFLOWS = {
    ".github/workflows/brace-broad-market-belief.yml": "pr10_broad_market",
    ".github/workflows/brace-sector-factor-belief.yml": "pr11_sector_factor",
    ".github/workflows/brace-company-entity-framework.yml": "pr12_company_entity",
    ".github/workflows/brace-entity-primary-source-evidence.yml": "pr13_primary_source",
    ".github/workflows/brace-entity-evidence-interpretation.yml": "pr14_interpretation",
    ".github/workflows/brace-entity-belief-state-forecast.yml": "pr15_belief_forecast",
    ".github/workflows/brace-entity-calibration-diagnostics.yml": "pr16_calibration",
    ".github/workflows/investment-semantics-world-state.yml": "pr16_1_world_state",
    ".github/workflows/brace-entity-belief-shadow-bridge.yml": "pr17_entity_bridge",
    ".github/workflows/belief-epistemic-causal-graph.yml": "pr19_epistemic_graph",
}

PRIMARY = {
    "pr10_broad_market": "brace-broad-market-belief-state",
    "pr11_sector_factor": "brace-sector-factor-belief-state",
    "pr12_company_entity": "brace-company-entity-framework-state",
    "pr13_primary_source": "brace-entity-primary-source-evidence-state",
    "pr14_interpretation": "brace-entity-evidence-interpretation-state",
    "pr15_belief_forecast": "brace-entity-belief-state-forecast-state",
    "pr16_calibration": "brace-entity-calibration-diagnostics-state",
    "pr16_1_world_state": "investment-semantics-world-state",
    "pr17_entity_bridge": "brace-entity-belief-shadow-bridge-state",
    "pr19_epistemic_graph": "belief-epistemic-causal-graph-state",
}

ARCHIVES = {
    "pr10_broad_market": "brace-broad-market-belief-state.tgz",
    "pr11_sector_factor": "brace-sector-factor-belief-state.tgz",
    "pr12_company_entity": "brace-company-entity-framework-state.tgz",
    "pr13_primary_source": "brace-entity-primary-source-evidence-state.tgz",
    "pr14_interpretation": "brace-entity-evidence-interpretation-state.tgz",
    "pr15_belief_forecast": "brace-entity-belief-state-forecast-state.tgz",
    "pr16_calibration": "brace-entity-calibration-diagnostics-state.tgz",
    "pr16_1_world_state": "investment-semantics-world-state.tgz",
    "pr17_entity_bridge": "brace-entity-belief-shadow-bridge-state.tgz",
    "pr19_epistemic_graph": "belief-epistemic-causal-graph-state.tgz",
}


class ResearchStateDurabilityWorkflowTests(unittest.TestCase):
    def test_every_canonical_research_layer_has_authoritative_restore_and_seal(self) -> None:
        for relative, layer in WORKFLOWS.items():
            with self.subTest(workflow=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("scripts/research_state_durability.py restore", text)
                self.assertIn("scripts/research_state_durability.py seal", text)
                self.assertIn(f'--layer "{layer}"', text)
                self.assertIn("GH_TOKEN: ${{ github.token }}", text)
                self.assertIn("contents: read", text)
                self.assertIn("actions: read", text)
                self.assertNotIn("contents: write", text)
                self.assertIn(PRIMARY[layer], text)

    def test_durability_script_changes_bootstrap_each_canonical_layer(self) -> None:
        for relative in WORKFLOWS:
            with self.subTest(workflow=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                push_section = text.split("  workflow_run:", 1)[0] if "  workflow_run:" in text else text.split("permissions:", 1)[0]
                self.assertIn('"scripts/research_state_durability.py"', push_section)
                self.assertIn('"tests/test_research_state_durability.py"', push_section)

    def test_heartbeat_refreshes_primary_compatibility_and_independent_checkpoint(self) -> None:
        text = (ROOT / ".github/workflows/research-state-durability-heartbeat.yml").read_text(encoding="utf-8")
        self.assertIn("name: Research State Durability Heartbeat", text)
        self.assertIn('cron: "15 6 * * 0"', text)
        self.assertIn("contents: read", text)
        self.assertIn("actions: read", text)
        self.assertNotIn("contents: write", text)
        self.assertIn("scripts/research_state_durability.py refresh", text)
        self.assertIn("name: ${{ matrix.primary }}", text)
        self.assertIn("name: research-state-durability-${{ matrix.layer }}", text)
        for layer in WORKFLOWS.values():
            self.assertIn(f"layer: {layer}", text)
            self.assertIn(f"primary: {PRIMARY[layer]}", text)
            self.assertIn(f"archive: {ARCHIVES[layer]}", text)

    def test_no_public_git_branch_is_used_as_research_state_store(self) -> None:
        heartbeat = (ROOT / ".github/workflows/research-state-durability-heartbeat.yml").read_text(encoding="utf-8")
        script = (ROOT / "scripts/research_state_durability.py").read_text(encoding="utf-8")
        combined = heartbeat + "\n" + script
        self.assertNotIn("research-state-ledger", combined)
        self.assertNotIn("git push", combined)
        self.assertIn('"public_repository_state_persistence": False', script)


if __name__ == "__main__":
    unittest.main()
