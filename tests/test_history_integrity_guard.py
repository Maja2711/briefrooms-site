import json
import tempfile
import unittest
from pathlib import Path
from scripts.history_integrity_guard import snapshot, verify

class HistoryIntegrityGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "forecast.jsonl").write_text('{"p":0.7,"issued_at":"2026-10-10T10:00:00Z"}\n')

    def test_roundtrip(self):
        manifest = snapshot(self.root, ["forecast.jsonl"], "2026-10-10T10:01:00Z")
        self.assertEqual(verify(self.root, manifest), 1)

    def test_changed_probability(self):
        manifest = snapshot(self.root, ["forecast.jsonl"])
        (self.root / "forecast.jsonl").write_text('{"p":0.8,"issued_at":"2026-10-10T10:00:00Z"}\n')
        with self.assertRaisesRegex(ValueError, "INTEGRITY_MISMATCH"):
            verify(self.root, manifest)

    def test_changed_timestamp(self):
        manifest = snapshot(self.root, ["forecast.jsonl"])
        (self.root / "forecast.jsonl").write_text('{"p":0.7,"issued_at":"2026-10-09T10:00:00Z"}\n')
        with self.assertRaisesRegex(ValueError, "INTEGRITY_MISMATCH"):
            verify(self.root, manifest)

    def test_missing_file(self):
        manifest = snapshot(self.root, ["forecast.jsonl"])
        (self.root / "forecast.jsonl").unlink()
        with self.assertRaises(ValueError):
            verify(self.root, manifest)

    def test_modified_manifest(self):
        manifest = snapshot(self.root, ["forecast.jsonl"])
        manifest["files"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "manifest checksum"):
            verify(self.root, manifest)

    def test_traversal(self):
        with self.assertRaises(ValueError):
            snapshot(self.root, ["../secret"])

    def test_symlink(self):
        (self.root / "alias.jsonl").symlink_to(self.root / "forecast.jsonl")
        with self.assertRaises(ValueError):
            snapshot(self.root, ["alias.jsonl"])


    def test_real_wes_decision_ledger_snapshot_and_tamper(self):
        """Integration: actual tracked WES ledger, copied read-only into isolation."""
        import shutil
        actual = Path(__file__).resolve().parents[1] / "data/investments/wes_decision_ledger.json"
        self.assertTrue(actual.is_file(), "WES ledger missing: fail closed")
        raw = actual.read_bytes()
        payload = json.loads(raw)
        self.assertEqual(payload.get("schema_version"), "briefrooms-wes-decision-ledger-v1")
        self.assertIsInstance(payload.get("records"), list)
        self.assertGreater(len(payload["records"]), 0, "WES ledger unexpectedly empty")
        (self.root / "actual_wes_decision_ledger.json").write_bytes(raw)
        manifest = snapshot(self.root, ["actual_wes_decision_ledger.json"])
        self.assertEqual(verify(self.root, manifest), 1)
        self.assertEqual(manifest["files"][0]["bytes"], len(raw))
        # Byte-accurate tampering must be rejected; no production file is touched.
        (self.root / "actual_wes_decision_ledger.json").write_bytes(raw + b" ")
        with self.assertRaisesRegex(ValueError, "INTEGRITY_MISMATCH"):
            verify(self.root, manifest)

if __name__ == "__main__":
    unittest.main()
