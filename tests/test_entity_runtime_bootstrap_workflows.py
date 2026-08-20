from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]

WORKFLOWS = {
    ".github/workflows/brace-entity-evidence-interpretation.yml": (
        ".github/workflows/brace-entity-evidence-interpretation.yml",
        "scripts/brace_entity_evidence_interpretation.py",
        "tests/test_brace_entity_evidence_interpretation.py",
        "docs/BRACE_ENTITY_EVIDENCE_INTERPRETATION.md",
    ),
    ".github/workflows/brace-entity-belief-state-forecast.yml": (
        ".github/workflows/brace-entity-belief-state-forecast.yml",
        "scripts/brace_entity_belief_state_forecast.py",
        "tests/test_brace_entity_belief_state_forecast.py",
        "docs/BRACE_ENTITY_BELIEF_STATE_FORECAST.md",
    ),
}


class EntityRuntimeBootstrapWorkflowTests(unittest.TestCase):
    def test_entity_runtime_layers_bootstrap_on_main_push(self) -> None:
        for relative_path, bootstrap_paths in WORKFLOWS.items():
            with self.subTest(workflow=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8").replace("\r\n", "\n")
                trigger_section = text.split("\npermissions:\n", 1)[0]

                self.assertIn("\non:\n", "\n" + trigger_section)
                self.assertIn("  workflow_dispatch:\n", trigger_section)
                self.assertIn("  push:\n", trigger_section)
                self.assertIn("    branches: [main]\n", trigger_section)
                self.assertIn("  workflow_run:\n", trigger_section)
                self.assertIn("  schedule:\n", trigger_section)

                push_block = trigger_section.split("  push:\n", 1)[1].split("  workflow_run:\n", 1)[0]
                self.assertIn("    branches: [main]\n", push_block)
                self.assertIn("    paths:\n", push_block)
                for path in bootstrap_paths:
                    self.assertIn(f'      - "{path}"\n', push_block)

    def test_bootstrap_push_does_not_remove_serial_state_protection(self) -> None:
        for relative_path in WORKFLOWS:
            with self.subTest(workflow=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("concurrency:\n", text)
                self.assertIn("cancel-in-progress: false", text)


if __name__ == "__main__":
    unittest.main()
