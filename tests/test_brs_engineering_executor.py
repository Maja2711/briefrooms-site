import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from brs_engineering_graph import Job, execute
from brs_engineering_executor import ROLES, expected, run, read_profile


class GraphTests(unittest.TestCase):
    def test_cycle_rejected_before_execution(self):
        calls = []
        with self.assertRaises(ValueError):
            execute([Job('a', 'x', ('b',)), Job('b', 'x', ('a',))], {'x': lambda _: calls.append(1)})
        self.assertEqual(calls, [])

    def test_failure_blocks_dependents_not_independent_jobs(self):
        jobs = [Job('a', 'bad'), Job('b', 'ok', ('a',)), Job('c', 'ok')]
        result = execute(jobs, {'bad': lambda _: {'status': 'FAIL'}, 'ok': lambda _: {'status': 'PASS'}})
        self.assertEqual([result[k]['status'] for k in ('a', 'b', 'c')], ['FAIL', 'BLOCKED', 'PASS'])


class ExecutorTests(unittest.TestCase):
    def prepare(self, root):
        target = root / '.codex' / 'agents'
        target.mkdir(parents=True)
        for role in ROLES:
            mode = 'workspace-write' if role == 'audit' else expected(role)
            (target / (role + '.toml')).write_text(f'name = "{role}"\nsandbox_mode = "{mode}"\ndeveloper_instructions = "check"\n')

    def test_repair_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            src, dst = Path(folder) / 'src', Path(folder) / 'candidate'
            self.prepare(src)
            before = read_profile(src, 'audit')
            result = run(src, dst)
            self.assertTrue(result['candidate_valid'])
            self.assertFalse(result['release_authority'])
            self.assertEqual(result['results']['diagnose']['changes'], ['audit'])
            self.assertEqual(read_profile(src, 'audit'), before)
            self.assertIn(b'sandbox_mode = "read-only"', read_profile(dst, 'audit'))
            self.assertFalse(run(src, dst)['candidate_valid'])

    def test_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            src, dst = Path(folder) / 'src', Path(folder) / 'candidate'
            self.prepare(src)
            with self.assertRaises(ValueError):
                run(src, src / 'candidate')
            (src / '.codex/agents/audit.toml').unlink()
            (src / '.codex/agents/audit.toml').symlink_to(src / '.codex/agents/grep.toml')
            self.assertFalse(run(src, dst)['candidate_valid'])
            self.assertFalse(dst.exists())


if __name__ == '__main__':
    unittest.main()
