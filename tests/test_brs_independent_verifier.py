import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from brs_independent_verifier import verify, validate_scope, REPO, MODULES, test_path

FP = "1234567890abcdef"
HEAD = "a"*40
PARENT = "b"*40
BASE = "c"*40
FILES = [{"filename": MODULES[0], "status": "modified"},
         {"filename": test_path(FP), "status": "added"}]

def fixture(stale=False, files=None):
    reads = 0
    def get(path):
        nonlocal reads
        if "/files?" in path:
            return FILES if files is None else files
        if "/commits?" in path:
            return [{"sha":HEAD,"parents":[{"sha":PARENT}]}]
        reads += 1
        return {"state":"open","merged":False,
                "head":{"ref":"brs/self-heal/news-"+FP,"sha":HEAD,"repo":{"full_name":REPO}},
                "base":{"ref":"main","sha":"d"*40 if stale and reads>1 else BASE,"repo":{"full_name":REPO}}}
    return get

def fetch(*args):
    return (HEAD+" "+PARENT+"\n").encode() if args[0]=="rev-list" else b""

class IndependentVerifierTests(unittest.TestCase):
    def test_exact_candidate_verification_not_release(self):
        report=verify(123, get=fixture(), fetch=fetch,
                      test=lambda *args: {"red":True,"green":True})
        self.assertEqual(report["status"],"VERIFIED_CANDIDATE")
        self.assertEqual(report["head_sha"],HEAD)
        self.assertFalse(report["release_authority"])

    def test_stale_base_rejected(self):
        report=verify(123,get=fixture(stale=True),fetch=fetch,
                      test=lambda *args: {"red":True,"green":True})
        self.assertEqual(report["status"],"STALE")

    def test_red_and_green_required(self):
        for red,green in [(False,True),(True,False),(False,False)]:
            report=verify(123,get=fixture(),fetch=fetch,
                          test=lambda *args: {"red":red,"green":green})
            self.assertEqual(report["status"],"FAILED")

    def test_out_of_scope_rejected_before_fetch(self):
        files=FILES+[{"filename":".github/workflows/deploy.yml","status":"modified"}]
        with self.assertRaises(ValueError):
            verify(123,get=fixture(files=files),
                   fetch=lambda *args: self.fail("unexpected fetch"))
        with self.assertRaises(ValueError):
            validate_scope([dict(FILES[0],status="renamed"),FILES[1]],FP)

    def test_parent_mismatch_rejected_before_test(self):
        with self.assertRaises(ValueError):
            verify(123,get=fixture(),fetch=lambda *args: b"wrong",
                   test=lambda *args: self.fail("unexpected test"))

if __name__=="__main__":
    unittest.main()
