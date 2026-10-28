"""No-network tests for the isolated single-instrument Saxo EURUSD D1 ledger."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import saxo_private_daily_d1_runner as d1_runner


class MemoryD1:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row

    def query(self, sql, params=None):
        cursor = self.connection.execute(sql, params or [])
        rows = [dict(row) for row in cursor.fetchall()]
        self.connection.commit()
        return {"results": rows}


def example_files(folder):
    (folder / "eurusd_daily_spot.json").write_text('{"instrument":"EUR/USD","metadata":{"position":null}}')
    (folder / "eurusd_daily_history.json").write_text('{"trades":[]}')
    (folder / "eurusd_production_policy_state.json").write_text('{"status":"PAPER"}')
    (folder / "eurusd_production_decisions.json").write_text('{"manifests":[]}')


class IsolatedD1Tests(unittest.TestCase):
    def test_genesis_state_and_one_cas_cycle(self):
        memory = MemoryD1()
        d1_runner.initialize(memory)
        with tempfile.TemporaryDirectory() as name, tempfile.TemporaryDirectory() as other:
            root = Path(name)
            check = Path(other)
            self.assertEqual(d1_runner.load(memory, root), (0, ""))
            example_files(root)
            d1_runner.save(memory, root, 0, "")
            self.assertEqual(d1_runner.load(memory, check)[0], 1)
            self.assertEqual(
                (root / "eurusd_daily_history.json").read_bytes(),
                (check / "eurusd_daily_history.json").read_bytes()
            )
            with self.assertRaisesRegex(d1_runner.PrivateStateFailure, "concurrent_update"):
                d1_runner.save(memory, root, 0, "")

    def test_existing_history_can_never_be_reinitialized(self):
        memory = MemoryD1()
        d1_runner.initialize(memory)
        with self.assertRaisesRegex(d1_runner.PrivateStateFailure, "already_initialized"):
            d1_runner.initialize(memory)
        memory.query("DELETE FROM private_saxo_eurusd_daily")
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaisesRegex(d1_runner.PrivateStateFailure, "missing_fail_closed"):
                d1_runner.load(memory, Path(name))

    def test_tampering_blocks_restore(self):
        memory = MemoryD1()
        d1_runner.initialize(memory)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            example_files(root)
            d1_runner.save(memory, root, 0, "")
            memory.query("UPDATE private_saxo_eurusd_daily SET sha256 = 'bad' WHERE id=1")
            with self.assertRaisesRegex(d1_runner.PrivateStateFailure, "checksum_mismatch"):
                d1_runner.load(memory, root)

    def test_bootstrap_requires_explicit_authority(self):
        with patch.dict(os.environ, {"SAXO_PRIVATE_D1_BOOTSTRAP_ALLOWED": "false"}):
            with patch.object(d1_runner, "D1", return_value=MemoryD1()):
                self.assertEqual(d1_runner.main(["--initialize"]), 1)

    def test_inactive_market_never_reads_or_writes_private_state(self):
        with patch.dict(os.environ, {
            "BRIEFROOMS_PRIVATE_RUNTIME": "1",
            "SAXO_PRIVATE_PRIMARY": "1",
            "SAXO_PRIVATE_AUTO_ENABLED": "true",
            "SAXO_PRIVATE_BASIC_PASSWORD": "test-only",
        }):
            with patch("daily_eurusd_cycle_watchdog.fx_market_active", return_value=False):
                with patch.object(d1_runner, "private_cycle") as cycle:
                    with patch.object(d1_runner, "load") as load:
                        with patch.object(d1_runner, "save") as save:
                            with patch.object(d1_runner, "D1", return_value=MemoryD1()):
                                self.assertEqual(d1_runner.main([]), 0)
                            cycle.assert_not_called()
                            load.assert_not_called()
                            save.assert_not_called()

    def test_private_cycle_off_by_default(self):
        with patch.dict(os.environ, {
            "SAXO_PRIVATE_AUTO_ENABLED": "false",
            "BRIEFROOMS_PRIVATE_RUNTIME": "1",
            "SAXO_PRIVATE_PRIMARY": "1"
        }):
            with patch.object(d1_runner, "D1", return_value=MemoryD1()):
                self.assertEqual(d1_runner.main([]), 1)

    def test_roundtrip_compresses_without_exposing_public_files(self):
        with tempfile.TemporaryDirectory() as name, tempfile.TemporaryDirectory() as other:
            root, restored = Path(name), Path(other)
            example_files(root)
            data = d1_runner._encode(root, 1)
            self.assertEqual(d1_runner._decode(data, restored), 1)
            self.assertFalse((Path(__file__).resolve().parents[1] / "data/private_saxo").exists())


if __name__ == "__main__":
    unittest.main()
