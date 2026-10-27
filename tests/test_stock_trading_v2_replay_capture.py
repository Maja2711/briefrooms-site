import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import stock_trading_portfolio as portfolio
from scripts import stock_trading_v2_production_bridge as bridge
from scripts import stock_trading_v2_production_replay as replay
from scripts import stock_trading_v2_replay_capture as capture


class SessionCloseCaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.now = datetime(2026, 9, 17, 20, 5, tzinfo=timezone.utc)
        self.snapshots = {
            "policy": {"original_policy": {"preserved": True}},
            "state": {"original_closed_history": [{"arbitrary": "must remain raw"}]},
            "runtime": {"phase": "FULL", "existing": "raw"},
            "manifest": {"original_manifest": 1},
            "config": {"original_config": 2},
        }
        self.paths = {}
        self.original = {}
        for key, value in self.snapshots.items():
            path = self.root / f"{key}.json"
            path.write_text(json.dumps(value, indent=3), encoding="utf-8")
            self.paths[f"{key}_path"] = path
            self.original[key] = path.read_bytes()
        self.benchmark = {"US": {"symbol": "SPY", "price": 500, "observed_at": "2026-09-17T19:59:59Z", "received_at": self.now.isoformat(), "provider": "Yahoo", "status": "OBSERVED"}}
        self.sessions = {"US": {"date": "2026-09-17", "regular_start": "2026-09-17T13:30:00Z", "regular_end": "2026-09-17T20:00:00Z",
                               "state": "POST", "closed": True, "provider": "Yahoo", "received_at": self.now.isoformat(), "source": "PROVIDER_REGULAR_PERIOD"}}

    def collect(self):
        return capture.capture_close(root=self.root / "frames", now=self.now, **self.paths)

    def test_actual_provider_close_freezes_raw_snapshots_without_any_trading_call(self):
        with patch.object(bridge, "_capture_benchmarks", return_value=(self.benchmark, self.sessions)) as provider, \
             patch.object(portfolio, "load_state", side_effect=AssertionError("normalization forbidden")), \
             patch.object(portfolio, "review_market", side_effect=AssertionError("lifecycle forbidden")), \
             patch.object(portfolio, "admit_candidate", side_effect=AssertionError("admission forbidden")), \
             patch.object(portfolio, "save_state", side_effect=AssertionError("portfolio persistence forbidden")), \
             patch.object(bridge, "process_market", side_effect=AssertionError("production admission forbidden")):
            result = self.collect()
        self.assertEqual("CAPTURED", result["status"])
        self.assertFalse(result["production_mutated"])
        provider.assert_called_once_with(self.now)
        frame = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        self.assertEqual("SESSION_CLOSE", frame["kind"])
        self.assertEqual({}, frame["observations"])
        self.assertEqual({}, frame["opportunities"])
        self.assertEqual({}, frame["quotes"])
        self.assertEqual(self.snapshots["state"], frame["initial_state"])
        self.assertEqual(self.snapshots["policy"], frame["policy"])
        self.assertEqual(self.snapshots["runtime"], frame["runtime"])
        self.assertEqual(self.snapshots["manifest"], frame["manifest"])
        self.assertEqual(self.snapshots["config"], frame["bridge_config"])
        self.assertEqual(self.sessions, frame["market_sessions"])
        self.assertEqual([], replay._validate_frame(frame, replay.production_code_sha256()))
        for key, original in self.original.items():
            self.assertEqual(original, self.paths[f"{key}_path"].read_bytes())

    def test_no_sourced_close_is_pending_and_does_not_create_frame(self):
        with patch.object(bridge, "_capture_benchmarks", return_value=({}, {})):
            result = self.collect()
        self.assertEqual("PENDING", result["status"])
        self.assertFalse((self.root / "frames").exists())

    def test_regular_future_or_unsourced_session_cannot_become_close_evidence(self):
        mutations = (
            {"state": "REGULAR", "closed": False},
            {"source": ""},
            {"regular_end": (self.now + timedelta(hours=1)).isoformat()},
            {"received_at": (self.now + timedelta(seconds=1)).isoformat()},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                sessions = {"US": {**self.sessions["US"], **mutation}}
                with patch.object(bridge, "_capture_benchmarks", return_value=(self.benchmark, sessions)):
                    result = self.collect()
                self.assertEqual("PENDING", result["status"])
        self.assertFalse((self.root / "frames").exists())


if __name__ == "__main__":
    unittest.main()
