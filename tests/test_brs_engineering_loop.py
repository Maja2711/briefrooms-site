import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from brs_engineering_loop import initial, advance
from brs_engineering_checkpoint import read, apply

SHA = "a" * 40
NEW = "b" * 40

def event(stage, **kwargs):
    return {"stage": stage, "sha": SHA, "evidence": "test evidence", **kwargs}

def evidence():
    return {"candidate_sha": SHA, "builder": "builder", "unresolved_blockers": [],
            **{r: {"identity": r, "sha": SHA, "passed": True, "reference": "test/" + r}
               for r in ("tester", "audit", "architect")}}

class LoopTests(unittest.TestCase):
    def test_retry_escalates(self):
        s = advance(initial("1128"), event("DETECT"))
        for i in range(3):
            for e in (event("DIAGNOSE", hypothesis=str(i)), event("REPAIR"),
                      event("VERIFY", passed=False), event("LEARN")):
                s = advance(s, e)
        self.assertEqual(s["stage"], "ESCALATED")
        self.assertEqual(s["attempts"], 3)

    def test_no_blind_retry(self):
        s = initial("1128")
        for e in (event("DETECT"), event("DIAGNOSE", hypothesis="conflict"),
                  event("REPAIR"), event("VERIFY", passed=False), event("LEARN"),
                  event("DIAGNOSE", hypothesis=" Conflict ")):
            s = advance(s, e)
        self.assertEqual(s["stage"], "ESCALATED")

    def test_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "tasks.db"
            for i, e in enumerate((event("DETECT"), event("DIAGNOSE", hypothesis="CI"),
                                   event("REPAIR"))):
                apply(db, "1128", i, e)
            self.assertEqual(read(db, "1128")[0], 3)
            for e, rev in ((event("VERIFY", passed=True), 3),
                           (event("VERIFY", sha=NEW, passed=False), 3),
                           (event("VERIFY", passed=False), 2)):
                with self.assertRaises(ValueError):
                    apply(db, "1128", rev, e)
                self.assertEqual(read(db, "1128")[0], 3)
            record = evidence()
            record["audit"]["identity"] = " TESTER "
            with self.assertRaises(ValueError):
                apply(db, "1128", 3, event("VERIFY", passed=True, release_evidence=record))
            apply(db, "1128", 3, event("VERIFY", passed=True, release_evidence=evidence()))
            apply(db, "1128", 4, event("LEARN"))
            revision, s = read(db, "1128")
            self.assertEqual(revision, 5)
            self.assertEqual(s["stage"], "COMPLETE")
            self.assertFalse(s["release_authority"])
            self.assertEqual(len(s["history"]), 5)
            self.assertEqual(read(db, "other")[0], 0)
            with self.assertRaises(ValueError):
                apply(db, "1128", 5, event("COMPLETE"))

if __name__ == "__main__":
    unittest.main()
