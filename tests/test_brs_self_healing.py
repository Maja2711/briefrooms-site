import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import brs_self_healing as heal


class SelfHealingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        (self.root / "tests").mkdir()
        self.path = heal.MODULES[0]
        (self.root / self.path).write_text("def match():\n    return False\n")
        self.fp = "1234567890abcdef"
        self.sha = "a" * 40
        self.raw = {
            "diagnosis": "missed duplicate",
            "edits": [{"path": self.path, "old": "return False", "new": "return True"}],
            "regression": "import unittest\nfrom scripts.news_editorial_guard import match\n"
                          "class Regression(unittest.TestCase):\n"
                          "    def test_match(self):\n        self.assertTrue(match())\n",
        }

    def test_apply_only_scoped_source_and_new_test(self):
        plan = heal.prepare_plan(self.raw, self.root, self.sha, self.fp)
        self.assertEqual(heal.apply_plan(plan, self.root),
                         sorted([self.path, heal.test_path(self.fp)]))
        self.assertIn("return True", (self.root / self.path).read_text())
        self.assertTrue((self.root / heal.test_path(self.fp)).is_file())
        self.assertIn("tests.test_brs_heal_" + self.fp, heal.suite(self.root))

    def test_reject_unscoped_paths(self):
        for path in ("../outside.py", ".github/workflows/publish-news.yml",
                     "data/news/pl.json", "tests/test_news_editorial_guard.py",
                     "scripts/brs_self_healing.py", "scripts/stock_trading.py"):
            with self.subTest(path=path):
                raw = dict(self.raw, edits=[dict(self.raw["edits"][0], path=path)])
                with self.assertRaises(ValueError):
                    heal.prepare_plan(raw, self.root, self.sha, self.fp)

    def test_reject_stale_and_ambiguous_snippets(self):
        for old in ("not present", "r"):
            raw = dict(self.raw, edits=[dict(self.raw["edits"][0], old=old)])
            with self.assertRaises(ValueError):
                heal.prepare_plan(raw, self.root, self.sha, self.fp)

    def test_reject_symlink_and_existing_regression(self):
        plan = heal.prepare_plan(self.raw, self.root, self.sha, self.fp)
        test = self.root / heal.test_path(self.fp)
        test.write_text("# original")
        with self.assertRaises(ValueError):
            heal.apply_plan(plan, self.root)
        self.assertIn("return False", (self.root / self.path).read_text())
        target = self.root / self.path
        target.unlink()
        target.symlink_to(test)
        with self.assertRaises(ValueError):
            heal.prepare_plan(self.raw, self.root, self.sha, self.fp)

    def test_reject_invalid_python_and_incident(self):
        with self.assertRaises(SyntaxError):
            heal.prepare_plan(dict(self.raw, regression="def :"), self.root, self.sha, self.fp)
        with self.assertRaises(ValueError):
            heal.prepare_plan(self.raw, self.root, self.sha, "../../main")

    def test_reject_new_unsafe_code(self):
        for new in ("return __import__('os').system('id')", "return open('/tmp/data')"):
            raw = dict(self.raw, edits=[dict(self.raw["edits"][0], new=new)])
            with self.assertRaises(ValueError):
                heal.prepare_plan(raw, self.root, self.sha, self.fp)

    def test_commit_identity_reproducible_and_no_ref_movement(self):
        def git(*args):
            return subprocess.run(["git", *args], cwd=self.root, check=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True).stdout.strip()
        git("init", "-q")
        git("config", "user.name", "test")
        git("config", "user.email", "test@example.invalid")
        git("add", self.path)
        git("commit", "-qm", "base")
        base = git("rev-parse", "HEAD")
        plan = heal.prepare_plan(self.raw, self.root, base, self.fp)
        paths = heal.apply_plan(plan, self.root)
        stamp = "1795795200 +0000"
        with patch.object(heal, "ROOT", self.root):
            a = heal.candidate_commit(self.root, paths, base, self.fp, stamp)
            b = heal.candidate_commit(self.root, paths, base, self.fp, stamp)
        self.assertEqual(a, b)
        self.assertEqual(git("rev-parse", "HEAD"), base)
        self.assertEqual(sorted(git("diff", "--name-only", base, a).splitlines()), paths)
        self.assertEqual(git("show", a + ":" + self.path).splitlines()[-1].strip(), "return True")

    @unittest.skipUnless(os.environ.get("BR_HEAL_SANDBOX_TEST") == "1", "Docker integration opt-in")
    def test_real_isolated_regression_red_then_green(self):
        (self.root / "scripts/__init__.py").touch()
        (self.root / "tests/__init__.py").touch()
        plan = heal.prepare_plan(self.raw, self.root, self.sha, self.fp)
        regression = self.root / heal.test_path(self.fp)
        module = "tests.test_brs_heal_" + self.fp
        regression.write_text(plan["regression"])
        red = heal.sandbox(self.root, (module,))
        self.assertNotEqual(red.returncode, 0, red.stdout)
        self.assertIn("FAIL: test_match", red.stdout)
        regression.unlink()
        heal.apply_plan(plan, self.root)
        green = heal.sandbox(self.root, (module,))
        self.assertEqual(green.returncode, 0, green.stdout)
        self.assertIn("Ran 1 test", green.stdout)

    def test_sandbox_has_no_network_or_write_mount(self):
        with patch.object(heal, "cmd", return_value=SimpleNamespace(returncode=0, stdout="OK")) as run:
            heal.sandbox(self.root, ("tests.test_example",))
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertIn("--read-only", args)
        self.assertIn(str(self.root) + ":/repo:ro", args)
        self.assertNotIn("GH_TOKEN", args)
        self.assertNotIn("GEMINI_API_KEY", args)

    def test_only_named_failed_tests_are_extracted(self):
        result = SimpleNamespace(stdout="FAIL: test_match (tests.foo.Bar.test_match)\n"
                                       "secret-like debug output\nRan 3 tests\n")
        self.assertEqual(heal.failures(result), ["test_match (tests.foo.Bar.test_match)"])

    def test_marker_mismatch_does_not_trigger_repair(self):
        (self.root / "data/system").mkdir(parents=True)
        (self.root / "data/news").mkdir()
        report = {"checked_at": datetime.now(timezone.utc).isoformat(), "status": "failed",
                  "marker": "old", "languages": {}}
        (self.root / "data/system/news_live_health.json").write_text(json.dumps(report))
        for lang in ("pl", "en"):
            (self.root / ("data/news/" + lang + ".json")).write_text(json.dumps({"marker": "new"}))
        self.assertEqual(heal.health_evidence(self.root), [])

    def test_writer_rejects_wrong_base_before_edit(self):
        report = self.root / "report.json"
        plan = heal.prepare_plan(self.raw, self.root, self.sha, self.fp)
        report.write_text(json.dumps({"status": "candidate", "plan": plan}))
        with patch.object(heal, "seen", return_value=False), patch.object(heal, "git", return_value="b"*40):
            with self.assertRaisesRegex(RuntimeError, "base mismatch"):
                heal.publish(report)

    def test_writer_never_edits_when_clean(self):
        report = self.root / "report.json"
        report.write_text(json.dumps({"status": "clean"}))
        with patch.object(heal, "git") as git, patch.object(heal, "gh") as gh:
            heal.publish(report)
        git.assert_not_called()
        gh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
