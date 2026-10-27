"""Offline tests for a separate, persistent private Saxo EURUSD Daily ledger."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import saxo_private_daily_runner as runner


class NotFound(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class PrivateDailyStateTests(unittest.TestCase):
    def make_state(self, root):
        (root / "eurusd_daily_spot.json").write_text('{"metadata":{"position":null}}')
        (root / "eurusd_daily_history.json").write_text('{"trades":[]}')
        (root / "eurusd_production_policy_state.json").write_text('{"status":"test"}')
        (root / "eurusd_production_decisions.json").write_text('{"manifests":[]}')

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            root = Path(first)
            other = Path(second)
            self.make_state(root)
            packed = runner.encode_state(root, 2)
            self.assertEqual(runner.decode_state(packed, other), 2)
            for name in runner.FILENAMES:
                self.assertEqual((root / name).read_bytes(), (other / name).read_bytes())

    def test_integrity_guard(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            root = Path(first)
            self.make_state(root)
            obj = json.loads(runner.encode_state(root, 0))
            obj["files"]["eurusd_daily_spot.json"]["sha256"] = "0" * 64
            with self.assertRaises(runner.PrivateStateFailure):
                runner.decode_state(json.dumps(obj).encode(), Path(second))
            self.assertEqual(list(Path(second).iterdir()), [])

    def test_clean_bootstrap_only_without_marker(self):
        client = Mock()
        client.get_object.side_effect = NotFound()
        client.head_object.side_effect = NotFound()
        with tempfile.TemporaryDirectory() as dirname:
            self.assertEqual(runner.load_r2_state(client, "private-bucket", Path(dirname)), (0, None))
            client.head_object.side_effect = None
            client.head_object.return_value = {"ETag": "initialized"}
            with self.assertRaises(runner.PrivateStateFailure):
                runner.load_r2_state(client, "private-bucket", Path(dirname))

    def test_cas_write_and_readback(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            self.make_state(root)
            client = Mock()
            client.get_object.return_value = {
                "Body": Mock(read=lambda *_: runner.encode_state(root, 4))
            }
            runner.save_r2_state(client, "private-bucket", root, 3, '"old"')
            params = client.put_object.call_args.kwargs
            self.assertEqual(params["IfMatch"], '"old"')
            self.assertEqual(json.loads(params["Body"])["sequence"], 4)

    def test_private_cycle_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {
            "BRIEFROOMS_PRIVATE_RUNTIME": "1",
            "SAXO_PRIVATE_PRIMARY": "1",
            "SAXO_PRIVATE_AUTO_ENABLED": "false",
        }):
            with patch.object(runner, "_client") as mocked:
                self.assertEqual(runner.main(), 2)
                mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
