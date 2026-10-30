"""Deterministic no-network private/public Daily ledger parity tests."""
import json
import tempfile
import unittest
from pathlib import Path

from saxo_daily_canonical_audit import FILES, MigrationBlocked, verify_same_ledger, read_only_divergence_categories

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

    def test_readonly_categories_distinguish_flat_branched_history(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pa,pb=Path(a),Path(b)
            self._data(pa);self._data(pb)
            h=json.loads((pa/FILES[1]).read_text())
            h["trades"]=[{"trade_id":"legacy","entry":1.12}]
            (pa/FILES[1]).write_text(json.dumps(h))
            k=json.loads((pb/FILES[1]).read_text())
            k["trades"]=[{"trade_id":"private","entry":1.13}]
            (pb/FILES[1]).write_text(json.dumps(k))
            self.assertEqual(read_only_divergence_categories(pa,pb),
                ("BOTH_FLAT","BRANCHED_HISTORY","MATCHED"))
            with self.assertRaisesRegex(MigrationBlocked,"LEDGER_STATE_CONFLICT"):
                verify_same_ledger(pa,pb)

    def test_conflicting_same_trade_id_blocks_migration(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pa,pb=Path(a),Path(b);self._data(pa);self._data(pb)
            for directory,entry in ((pa,1.12),(pb,1.14)):
                h=json.loads((directory/FILES[1]).read_text())
                h["trades"]=[{"trade_id":"same-id","entry":entry}]
                (directory/FILES[1]).write_text(json.dumps(h))
            self.assertEqual(read_only_divergence_categories(pa,pb)[1],
                             "SHARED_TRADE_CONFLICT")

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
