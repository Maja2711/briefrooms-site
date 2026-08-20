from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / ".github/workflows/entity-semantic-eligibility-validation.yml"
PR12_RUNTIME = ROOT / ".github/workflows/brace-company-entity-framework.yml"

SEMANTIC_RUNTIME_PATHS = (
    "scripts/entity_semantic_eligibility.py",
    "scripts/brace_entity_evidence_interpretation_semantic.py",
    "scripts/brace_entity_belief_state_forecast_semantic.py",
    "scripts/brace_entity_calibration_diagnostics_semantic.py",
    "scripts/investment_semantics_world_state_semantic.py",
    "scripts/brace_entity_belief_shadow_bridge_semantic.py",
    "scripts/belief_epistemic_causal_graph_semantic.py",
)

AFFECTED_TEST_MODULES = (
    "tests.test_entity_semantic_eligibility",
    "tests.test_brace_company_entity_framework",
    "tests.test_brace_entity_primary_source_evidence",
    "tests.test_brace_entity_sec_source_aliases",
    "tests.test_brace_entity_evidence_interpretation",
    "tests.test_brace_entity_belief_state_forecast",
    "tests.test_brace_entity_calibration_diagnostics",
    "tests.test_investment_semantics_world_state",
    "tests.test_brace_entity_belief_shadow_bridge",
    "tests.test_belief_epistemic_causal_graph",
)


class EntitySemanticWorkflowContractTests(unittest.TestCase):
    def test_validation_workflow_covers_shared_and_downstream_semantic_surface(self) -> None:
        text = VALIDATION.read_text(encoding="utf-8")
        self.assertIn("pull_request:\n", text)
        self.assertIn("push:\n", text)
        self.assertIn("permissions:\n  contents: read\n", text)
        self.assertNotIn("actions: write", text)
        for path in SEMANTIC_RUNTIME_PATHS:
            self.assertIn(f'      - "{path}"', text)
        for module in AFFECTED_TEST_MODULES:
            self.assertIn(module, text)

    def test_pr12_is_the_single_semantic_runtime_bootstrap_root(self) -> None:
        text = PR12_RUNTIME.read_text(encoding="utf-8")
        trigger_section = text.split("\npermissions:\n", 1)[0]
        push_block = trigger_section.split("  push:\n", 1)[1].split("  workflow_run:\n", 1)[0]
        for path in SEMANTIC_RUNTIME_PATHS:
            self.assertIn(f'      - "{path}"\n', push_block)
        self.assertIn('      - "tests/test_entity_semantic_eligibility.py"\n', push_block)
        self.assertIn('      - "docs/ENTITY_SEMANTIC_ELIGIBILITY.md"\n', push_block)

    def test_pr12_runtime_asserts_semantic_contract_before_persisting_state(self) -> None:
        text = PR12_RUNTIME.read_text(encoding="utf-8")
        self.assertIn("scripts/entity_semantic_eligibility.py scripts/brace_company_entity_framework.py", text)
        self.assertIn("entity_semantic_eligibility_enabled", text)
        self.assertIn("bank_specific_dimensions_require_bank_archetype", text)
        self.assertIn("entity-semantic-eligibility-v1", text)


if __name__ == "__main__":
    unittest.main()
