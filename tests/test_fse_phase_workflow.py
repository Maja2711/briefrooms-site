import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/fse-v2-deep-fractal-memory.yml"


class TestFSEPhaseWorkflow(unittest.TestCase):
    def test_restore_selects_latest_real_artifact_not_latest_success_run(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('/actions/artifacts?name=$STATE_ARTIFACT&per_page=100', text)
        self.assertIn('select(.expired == false)', text)
        self.assertIn("FSE_STATE_RESTORE_PASS", text)
        self.assertNotIn(
            'gh run list --workflow fse-v2-deep-fractal-memory.yml --branch main --status success --limit 1',
            text,
        )

    def test_existing_public_state_fails_closed_without_durable_artifact(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('if [[ -s "$PUBLIC_PATH" ]]', text)
        self.assertIn("FSE_STATE_RESTORE_FAIL", text)
        self.assertIn('exit 1', text)

    def test_state_archive_is_required_before_extract(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('if [[ ! -s "$archive" ]]', text)
        self.assertIn('tar -xzf "$archive" -C "$FSE_V2_STATE_DIR"', text)


if __name__ == "__main__":
    unittest.main()
