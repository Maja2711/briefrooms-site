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

if __name__ == "__main__":
    unittest.main()
