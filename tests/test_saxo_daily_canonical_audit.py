"""Deterministic no-network private/public Daily ledger parity tests."""
import json
import tempfile
import unittest
from pathlib import Path

from saxo_daily_canonical_audit import FILES, MigrationBlocked, verify_same_ledger

class AuditTests(unittest.TestCase):
    def _data(self, dir):
        for name in FILES:
            (dir/name).write_text(json.dumps({"status":"NO_TRADE","trades":[],
                  "metadata":{"position":None},"decisions":[]}),encoding="utf-8")

    def test_identical_ledger_is_review_only_not_automatic_migration(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pa,pb=Path(a),Path(b)
            self._data(pa);self._data(pb)
            self.assertEqual(verify_same_ledger(pa,pb),"MIGRATION_READY_FOR_HUMAN_REVIEW")

    def test_history_conflict_refuses_rewrite(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pa,pb=Path(a),Path(b)
            self._data(pa);self._data(pb)
            changed=json.loads((pb/FILES[1]).read_text())
            changed["trades"]=[{"id":"different"}]
            (pb/FILES[1]).write_text(json.dumps(changed))
            with self.assertRaisesRegex(MigrationBlocked,"LEDGER_STATE_CONFLICT"):
                verify_same_ledger(pa,pb)

    def test_missing_policy_epoch_is_blocked(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pa,pb=Path(a),Path(b)
            self._data(pa);self._data(pb)
            (pb/FILES[2]).unlink()
            with self.assertRaisesRegex(MigrationBlocked,"STATE_MISSING_OR_UNREADABLE"):
                verify_same_ledger(pa,pb)

if __name__=="__main__":
    unittest.main()
