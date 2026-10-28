"""Synthetic prospective Daily audit publication; no live HTTP or trading writes."""
import base64
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eurusd_cb_shadow_observe as observer
import publish_eurusd_cb_wire as publisher
from tests.test_eurusd_central_bank_wire import Client, NOW, collected


class FakeGit:
    def __init__(self, state_bytes):
        self.state_bytes = state_bytes
        self.calls = []
        self.prior_risk = None

    def __call__(self, repo, token, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "git/ref/heads/main":
            return {"object": {"sha": "head"}}
        if path == "git/commits/head":
            return {"tree": {"sha": "canonical-base-tree"}}
        if path.startswith("contents/data/investments/eurusd_daily_spot.json?"):
            return {"content": base64.b64encode(self.state_bytes).decode()}
        if path.startswith("contents/data/investments/eurusd_daily_cb_event_risk.json?") and self.prior_risk is not None:
            return {"content": base64.b64encode(json.dumps(self.prior_risk).encode()).decode()}
        if path.startswith("contents/"):
            raise HTTPError("https://fixture.invalid", 404, "fixture missing", {}, None)
        if path == "git/blobs":
            return {"sha": hashlib.sha256(body["content"].encode()).hexdigest()}
        if path == "git/trees":
            return {"sha": "new-tree"}
        if path == "git/commits":
            return {"sha": "new-commit"}
        if path == "git/refs/heads/main":
            assert body.get("force") is False
            return {}
        raise AssertionError(path)

    def published_paths(self):
        return {entry["path"] for method, path, body in self.calls if path == "git/trees" for entry in body["tree"]}


class DailyPublicationSafetyTests(unittest.TestCase):
    def setup_case(self, root):
        state = {"instrument": "EUR/USD", "status": "NO_TRADE", "metadata": {"position": None}}
        directory = root / "data/investments"
        directory.mkdir(parents=True)
        raw = json.dumps(state, sort_keys=True).encode()
        (directory / "eurusd_daily_spot.json").write_bytes(raw)
        snapshot = collected(Client())
        assessment = observer.observe_daily(root=root, now=NOW, wire=snapshot)
        return snapshot, assessment, FakeGit(raw)

    def publish_with_wire_fallback(self, snapshot, assessment, request):
        # Rejecting an unsafe optional audit is valid. The independent wire
        # remains publishable without that consumer, as the workflow must do.
        try:
            return publisher.publish(snapshot, "repo", "token", request, daily_assessment=assessment)
        except ValueError:
            return publisher.publish(snapshot, "repo", "token", request)

    def test_valid_daily_audit_is_atomic_and_scope_limited(self):
        with tempfile.TemporaryDirectory() as td:
            snapshot, assessment, request = self.setup_case(Path(td))
            self.assertEqual(publisher.publish(snapshot, "repo", "token", request, daily_assessment=assessment), "new-commit")
            paths = request.published_paths()
            self.assertEqual(len(paths), 4)
            self.assertIn(publisher.SNAPSHOT, paths)
            self.assertIn("data/investments/eurusd_daily_cb_event_risk.json", paths)
            self.assertTrue(any(path.startswith("data/investments/eurusd_cb_audit/daily/") for path in paths))
            self.assertTrue(all("stock_trading" not in path and "weekly/" not in path for path in paths))
            self.assertEqual(sum(path == "git/refs/heads/main" for _, path, _ in request.calls), 1)

    def test_position_state_change_omits_optional_audit_without_losing_wire(self):
        with tempfile.TemporaryDirectory() as td:
            snapshot, assessment, request = self.setup_case(Path(td))
            request.state_bytes += b"\n"
            publisher.publish(snapshot, "repo", "token", request, daily_assessment=assessment)
            self.assertEqual(len(request.published_paths()), 2)

    def test_other_snapshot_audit_is_not_attached_to_current_wire(self):
        with tempfile.TemporaryDirectory() as td:
            snapshot, assessment, request = self.setup_case(Path(td))
            assessment["audit"]["snapshot_sha256"] = "0" * 64
            self.publish_with_wire_fallback(snapshot, assessment, request)
            self.assertEqual(len(request.published_paths()), 2)

    def test_future_assessment_is_never_published(self):
        with tempfile.TemporaryDirectory() as td:
            snapshot, assessment, request = self.setup_case(Path(td))
            assessment["evaluated_at"] = (NOW + timedelta(days=20000)).isoformat()
            self.publish_with_wire_fallback(snapshot, assessment, request)
            self.assertEqual(len(request.published_paths()), 2)

    def test_execution_claim_is_never_published_as_shadow(self):
        with tempfile.TemporaryDirectory() as td:
            snapshot, assessment, request = self.setup_case(Path(td))
            assessment["execution_authority"] = True
            assessment["executed_at"] = NOW.isoformat()
            self.publish_with_wire_fallback(snapshot, assessment, request)
            self.assertEqual(len(request.published_paths()), 2)

    def test_corrupt_optional_latest_audit_cannot_block_wire_publication(self):
        with tempfile.TemporaryDirectory() as td:
            snapshot, assessment, request = self.setup_case(Path(td))
            request.prior_risk = {"evaluated_at": "not-a-timestamp"}
            self.publish_with_wire_fallback(snapshot, assessment, request)
            self.assertIn(publisher.SNAPSHOT, request.published_paths())

    def test_observer_rejects_private_saxo_position_before_public_projection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            snapshot, _, _ = self.setup_case(root)
            state_path = root / "data/investments/eurusd_daily_spot.json"
            state = {"instrument": "EUR/USD", "metadata": {"position": {"status": "OPEN",
                "trade_id": "synthetic-private", "direction": "LONG", "entry": 1.1, "stop": 1.09,
                "target": 1.12, "opened_at": (NOW - timedelta(hours=1)).isoformat(),
                "execution_price_engine": {"private_saxo_primary": True}}}}
            state_path.write_text(json.dumps(state), encoding="utf-8")
            before = state_path.read_bytes()
            with self.assertRaises(ValueError):
                observer.observe_daily(root=root, now=NOW, wire=snapshot)
            self.assertEqual(before, state_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
