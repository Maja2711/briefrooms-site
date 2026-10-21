"""Exercise the real post-commit shell handoff with a network-free HTTP stub."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/sync_trading_push_commit.sh"
SHA = "a" * 40
BASH = shutil.which("bash")
if not BASH and Path("C:/Program Files/Git/bin/bash.exe").is_file():
    BASH = "C:/Program Files/Git/bin/bash.exe"


def receipt(**changes):
    return {"ok": True, "sha": SHA, "engines": ["daily"],
            "dispatch": {"sent": 1, "failed": 0, "pending": 0}, **changes}


@unittest.skipUnless(BASH, "Bash is required for the real handoff regression")
class TradingPushHandoffTests(unittest.TestCase):
    def _run(self, body, *, code="200", sha=SHA, engines="daily", token=None, api=None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args_file = root / "curl-args.jsonl"
            stub = root / "curl_stub.py"
            stub.write_text("""import json, os, sys
from pathlib import Path
args=sys.argv[1:]
Path(args[args.index('-o')+1]).write_text(os.environ['TEST_BODY'],encoding='utf-8')
with Path(os.environ['TEST_ARGS']).open('a',encoding='utf-8') as f:
    f.write(json.dumps(args)+'\\n')
print(os.environ['TEST_CODE'],end='')
""", encoding="utf-8", newline="\n")
            # Git Bash rewrites inherited Windows PATH. Functions loaded via
            # BASH_ENV take precedence over every real HTTP executable, so a
            # failed fixture can never fall through to production networking.
            bash_env = root / "bash-env.sh"
            bash_env.write_text('''python() { "$HANDOFF_TEST_PYTHON" "$@"; }
curl() { "$HANDOFF_TEST_PYTHON" "$HANDOFF_TEST_CURL" "$@"; }
sleep() { return 0; }
''', encoding="utf-8", newline="\n")
            env = dict(os.environ)
            env.pop("GH_TOKEN", None)
            env.pop("PUSH_API", None)
            env.update(TEST_BODY=json.dumps(body) if not isinstance(body, str) else body,
                       TEST_CODE=code, TEST_ARGS=str(args_file).replace("\\", "/"),
                       RUNNER_TEMP=str(root).replace("\\", "/"),
                       BASH_ENV=str(bash_env).replace("\\", "/"),
                       HANDOFF_TEST_PYTHON=str(Path(sys.executable)).replace("\\", "/"),
                       HANDOFF_TEST_CURL=str(stub).replace("\\", "/"))
            if token is not None:
                env["GH_TOKEN"] = token
            if api is not None:
                env["PUSH_API"] = api
            result = subprocess.run([BASH, str(SCRIPT).replace("\\", "/"), sha, engines],
                                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
            calls = [json.loads(row) for row in args_file.read_text().splitlines()] if args_file.exists() else []
            leftovers = list(root.glob("trading-push-commit-sync.*"))
            return result, calls, leftovers

    def test_exact_terminal_receipt_succeeds_without_network_or_retry(self):
        result, calls, leftovers = self._run(receipt())
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, len(calls))
        self.assertEqual([], leftovers)
        self.assertIn("--connect-timeout", calls[0])
        self.assertEqual("5", calls[0][calls[0].index("--connect-timeout") + 1])
        self.assertIn("--max-time", calls[0])
        self.assertEqual("25", calls[0][calls[0].index("--max-time") + 1])
        self.assertEqual({"sha": SHA, "engines": ["daily"]}, json.loads(calls[0][calls[0].index("--data") + 1]))

    def test_durable_202_acceptance_needs_a_retry_when_provider_is_pending(self):
        queued = receipt(ok=False, durable_accepted=True, retry_scheduled_at="2026-10-21T12:00:01Z",
                         dispatch={"sent": 0, "failed": 1, "pending": 1})
        result, calls, _ = self._run(queued, code="202")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, len(calls))
        queued.pop("retry_scheduled_at")
        result, calls, _ = self._run(queued, code="202")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(4, len(calls))

    def test_receipt_for_another_commit_or_channel_is_not_success(self):
        for wrong in (receipt(sha="b" * 40), receipt(engines=["stock"])):
            with self.subTest(receipt=wrong):
                result, calls, _ = self._run(wrong)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(4, len(calls))

    def test_malformed_receipts_and_transport_errors_have_bounded_retries(self):
        for code, body in (("200", "{invalid"), ("500", receipt()), ("000", "")):
            with self.subTest(code=code):
                result, calls, leftovers = self._run(body, code=code)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(4, len(calls))
                self.assertEqual([], leftovers)

    def test_ephemeral_token_only_goes_to_our_worker_and_is_never_logged(self):
        token = "test-only-ephemeral-token"
        result, calls, _ = self._run(receipt(), token=token)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Authorization: Bearer " + token, calls[0])
        self.assertNotIn(token, result.stdout + result.stderr)
        result, calls, _ = self._run(receipt(), token=token, api="https://untrusted.example")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], calls)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_invalid_arguments_never_attempt_http(self):
        for sha, engines in (("bad", "daily"), (SHA, "invalid"), (SHA, "")):
            with self.subTest(sha=sha, engines=engines):
                result, calls, _ = self._run(receipt(), sha=sha, engines=engines)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual([], calls)


class TradingPushWorkflowBoundaryTests(unittest.TestCase):
    def test_canonical_sync_steps_forward_request_scoped_github_token(self):
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            source = path.read_text(encoding="utf-8")
            for step in re.split(r"\n      - ", source):
                if not re.search(r"\bbash scripts/sync_trading_push_commit\.sh", step):
                    continue
                with self.subTest(workflow=path.name):
                    self.assertIn("GH_TOKEN: ${{ github.token }}", step)

    def test_handoff_code_push_cannot_execute_touched_trading_writers(self):
        owners = {
            "wes-live-execution.yml": ["execute"],
            "weekly-live-prices.yml": ["update-weekly-live-prices"],
            "investments-weekly-freshness-watchdog.yml": ["freshness-watchdog"],
            "us-daily-stock-position-monitor.yml": ["monitor"],
            "stock-trading-portfolio.yml": ["portfolio"],
            "stock-trading-v2-production.yml": ["champion"],
            "investment-event-intelligence-production.yml": ["weekly-overlay", "stock-overlay"],
        }
        for name, jobs in owners.items():
            source = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            for job in jobs:
                block = source.split("\n  " + job + ":", 1)[1].split("\n  ", 1)[0]
                # Check the job-level guard, before runs-on/steps.
                header = source.split("\n  " + job + ":", 1)[1].split("    runs-on:", 1)[0]
                with self.subTest(workflow=name, job=job):
                    self.assertIn("github.event_name != 'push'", header)

    def test_deploy_never_broadcasts_test_alerts_or_rotates_partial_vapid_pair(self):
        source = (ROOT / ".github/workflows/deploy-trading-push-backend.yml").read_text(encoding="utf-8")
        self.assertNotIn("/sync-trading", source)
        self.assertNotIn("/sync-daily", source)
        self.assertIn("missing.length === 1", source)
        self.assertIn('if [ "$missing_count" -eq 2 ]', source)
        self.assertIn("commit-bound-durable-v3", source)
        self.assertIn("durable_alarm_retry", source)

    def test_source_push_validation_cannot_cascade_into_trading_dispatch(self):
        for name in ("belief-core-shadow-live.yml", "stock-trading-v2-liveness-watchdog.yml",
                     "investments-weekly-freshness-watchdog.yml"):
            source = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                self.assertIn("github.event.workflow_run.event != 'push'", source)
        source = (ROOT / ".github/workflows/stock-trading-v2-liveness-watchdog.yml").read_text(encoding="utf-8")
        header = source.split("\n  watchdog:", 1)[1].split("    runs-on:", 1)[0]
        self.assertIn("github.event_name != 'push'", header)


if __name__ == "__main__":
    unittest.main()
