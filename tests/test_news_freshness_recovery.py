"""Offline regression tests for the recovery workflow embedded Python script."""
import ast
import base64
import io
import json
import pathlib
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

WORKFLOW = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/news-freshness-recovery.yml"


def embedded_code():
    text = WORKFLOW.read_text()
    start = text.index("          python - <<'PY'\n") + len("          python - <<'PY'\n")
    end = text.index("          PY\n", start)
    return "\n".join(line[10:] for line in text[start:end].splitlines())


class FakeResponse:
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self):
        return self.data.encode() if isinstance(self.data, str) else json.dumps(self.data).encode()


class RecoveryTests(unittest.TestCase):
    def test_python_compiles(self):
        ast.parse(embedded_code())

    def execute(self, age, *, status="healthy", active=False, cooldown=False, mismatch=False, failed=False, production_mismatch=False):
        timestamp = (datetime.now(timezone.utc) - timedelta(minutes=age)).isoformat()
        marker = "test-1"
        data = {"marker": marker, "generated_at": timestamp, "status": status}
        calls = []
        def fake_open(request, timeout):
            url = request.full_url
            if "/actions/workflows/" in url:
                runs = []
                if active:
                    runs.append({"status": "in_progress", "event": "schedule", "created_at": timestamp})
                if cooldown:
                    runs.append({"status": "completed", "event": "workflow_dispatch", "created_at": datetime.now(timezone.utc).isoformat()})
                if failed:
                    runs.append({"status": "completed", "conclusion": "failure", "event": "schedule", "created_at": timestamp})
                return FakeResponse({"workflow_runs": runs})
            if "briefrooms.com" in url:
                if url.endswith(".html") or ".html?" in url:
                    return FakeResponse("<html>briefrooms-live-news-marker "+("old" if production_mismatch else marker)+"</html>")
                return FakeResponse({"marker": "old" if production_mismatch else marker})
            payload = dict(data)
            if mismatch and url.endswith("/en.json"):
                payload["marker"] = "different"
            return FakeResponse({"content": base64.b64encode(json.dumps(payload).encode()).decode()})
        def fake_run(*args, **kwargs):
            calls.append((args, kwargs))
        with patch.dict("os.environ", {"GH_REPO": "Maja2711/briefrooms-site", "GH_TOKEN": "offline-test"}), patch("urllib.request.urlopen", side_effect=fake_open), patch("subprocess.run", side_effect=fake_run), redirect_stdout(io.StringIO()):
            try:
                exec(compile(embedded_code(), str(WORKFLOW), "exec"), {"__name__": "__main__"})
            except SystemExit as exc:
                if exc.code not in (0, None):
                    raise
        return calls

    def test_fresh_degraded_does_not_dispatch(self):
        self.assertEqual(self.execute(20, status="degraded"), [])
    def test_failed_publisher_dispatches(self):
        self.assertEqual(len(self.execute(20, failed=True)), 1)
    def test_production_mismatch_dispatches(self):
        self.assertEqual(len(self.execute(20, production_mismatch=True)), 1)
    def test_stale_dispatches(self):
        self.assertEqual(len(self.execute(100)), 1)
    def test_active_publisher_prevents_duplicate(self):
        self.assertEqual(self.execute(100, active=True), [])
    def test_cooldown_prevents_dispatch(self):
        self.assertEqual(self.execute(100, cooldown=True), [])
    def test_marker_mismatch_triggers_recovery(self):
        self.assertEqual(len(self.execute(10, mismatch=True)), 1)


if __name__ == "__main__":
    unittest.main()
