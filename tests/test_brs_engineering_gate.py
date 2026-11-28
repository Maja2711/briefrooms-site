import unittest
from scripts.brs_engineering_gate import blockers

class GateTests(unittest.TestCase):
    def record(self):
        sha = "a"*40
        return dict(candidate_sha=sha, builder="builder", unresolved_blockers=[],
                    **{role: dict(identity=role, sha=sha, passed=True, reference="test-ref")
                       for role in ("tester", "audit", "architect")})
    def test_valid_structure(self):
        self.assertEqual(blockers(self.record()), [])
    def test_stale(self):
        r = self.record()
        r["audit"]["sha"] = "b"*40
        self.assertTrue(blockers(r))
    def test_self_review(self):
        r = self.record()
        r["audit"]["identity"] = "builder"
        self.assertTrue(blockers(r))
    def test_missing(self):
        self.assertTrue(blockers({}))
    def test_unresolved(self):
        r = self.record()
        r["unresolved_blockers"] = ["regression"]
        self.assertTrue(blockers(r))
