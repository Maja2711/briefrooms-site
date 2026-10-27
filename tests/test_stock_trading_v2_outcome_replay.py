from __future__ import annotations

import unittest
import json
import os
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts import stock_trading_v2_contracts as contracts
from scripts import stock_trading_v2_discovery as discovery
from scripts import stock_trading_v2_outcome_replay as replay
from scripts import stock_trading_v2_experience_store as experience


def event(*, selected: bool = False, skip_above: float = 105.0):
    return contracts.make_experience_event(
        market="US",
        symbol="TEST",
        decision_at="2026-09-10T15:00:00-04:00",
        session_date="2026-09-10",
        selected=selected,
        source_engine="test",
        source_schema_version="test-v1",
        source_policy_version="test-policy",
        source_payload_sha256="abc",
        candidate_state={
            "risk_plan": {
                "reference_price": 100.0,
                "entry_zone": [99.0, 102.0],
                "skip_above": skip_above,
                "stop": 95.0,
                "target": 110.0,
                "risk_percent": 0.05,
                "reward_risk": 2.0,
            },
            "decision_path": {"first_blocking_gate": "score"},
        },
        recorded_at="2026-09-10T19:00:01Z",
    )


def bar(day: str, open_: float, high: float, low: float, close: float):
    return discovery.Bar(day=day, open=open_, high=high, low=low, close=close, volume=100000)


class StockTradingV2OutcomeReplayTests(unittest.TestCase):
    def _store(self, base: Path, *, horizons=(1,), version="test"):
        source_root = base / "experience"
        source_root.mkdir()
        source = event()
        (source_root / "event.json").write_text(json.dumps(source), encoding="utf-8")
        config_path = base / "config.json"
        config_path.write_text(json.dumps({"schema_version": "stock-trading-v2-learning-config-v1", "horizons_sessions": list(horizons),
            "replay": {"version": version}, "cost_stress_percent": {"US": 0.1}, "governance": {"production_decision_influence": False}}), encoding="utf-8")
        return source, {"experience_root": source_root, "admission_root": base / "admission", "outcome_root": base / "outcomes", "config_path": config_path}

    def test_actual_1499_historical_results_are_reused_across_two_runs(self):
        fixture_data = Path(os.environ.get("STOCK_TRADING_LEGACY_FIXTURE_ROOT", replay.DEFAULT_ROOT.parent))
        original_root = fixture_data / replay.DEFAULT_ROOT.name
        originals = list(original_root.rglob("*.json"))
        if len(originals) < 1499:
            self.skipTest("Full legacy 1499-outcome snapshot unavailable; set STOCK_TRADING_LEGACY_FIXTURE_ROOT to the read-only research data/investments snapshot")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "outcomes"
            shutil.copytree(original_root, root)
            before = {root / path.relative_to(original_root): path.read_bytes() for path in originals}
            source_roots = {"experience_root": fixture_data / experience.DEFAULT_STORE_ROOT.name,
                            "admission_root": fixture_data / replay.admission.DEFAULT_ROOT.name}
            config_path = Path(tmp) / "config.json"
            config = replay.load_config()
            config["replay"]["version"] = "revised-methodology-must-not-reinterpret-history"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            # Revisions would change old results to NOT_ACTIVATED. They must
            # only settle horizons that have never had a terminal result.
            # A synthetic day after every source decision also matures the
            # newest fixtures, so the second run has no unfinished horizons.
            revised = [bar("2099-12-31", 1e12, 1e12 + 10, 1e12 - 10, 1e12)]
            with patch.object(replay.discovery, "fetch_yahoo_history", return_value=(revised, {})) as fetch:
                first = replay.settle_store(outcome_root=root, config_path=config_path, **source_roots)
                self.assertGreater(fetch.call_count, 0)
            self.assertEqual(first["immutable_outcomes_reused"], len(originals))
            self.assertGreater(first["outcomes_written"], 0)
            after_first = {path: path.read_bytes() for path in root.rglob("*.json")}
            self.assertTrue(all(path.read_bytes() == raw for path, raw in before.items()))
            with patch.object(replay.discovery, "fetch_yahoo_history", side_effect=AssertionError("network must not run")) as fetch:
                second = replay.settle_store(outcome_root=root, config_path=config_path, **source_roots)
                fetch.assert_not_called()
            self.assertEqual(second["outcomes_written"], 0)
            self.assertEqual(second["immutable_outcomes_reused"], len(after_first))
            self.assertEqual({path: path.read_bytes() for path in after_first}, after_first)
            self.assertEqual(replay.verify(root)["count"], len(after_first))

    def test_unfinished_history_can_mature_but_settled_history_cannot_be_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, kwargs = self._store(Path(tmp), horizons=(2,))
            partial = [bar("2026-09-11", 100, 104, 98, 103)]
            with patch.object(replay.discovery, "fetch_yahoo_history", return_value=(partial, {})):
                first = replay.settle_store(**kwargs)
            self.assertEqual(first["outcomes_written"], 0)
            self.assertEqual(first["unresolved_horizons"], 1)
            self.assertEqual(list(kwargs["outcome_root"].rglob("*.json")), [])
            complete = partial + [bar("2026-09-14", 103, 106, 101, 105)]
            with patch.object(replay.discovery, "fetch_yahoo_history", return_value=(complete, {})):
                second = replay.settle_store(**kwargs)
            self.assertEqual(second["outcomes_written"], 1)
            path = next(kwargs["outcome_root"].rglob("*.json"))
            immutable = path.read_bytes()
            # Even a broken provider response is irrelevant after settlement.
            with patch.object(replay.discovery, "fetch_yahoo_history", return_value=([{"day": "2026-09-11", "open": None}], {})) as fetch:
                third = replay.settle_store(**kwargs)
                fetch.assert_not_called()
            self.assertEqual(third["outcomes_existing"], 1)
            self.assertEqual(path.read_bytes(), immutable)

    def test_new_replay_version_does_not_create_another_answer_for_settled_horizon(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, kwargs = self._store(Path(tmp))
            bars = [bar("2026-09-11", 100, 104, 98, 103)]
            with patch.object(replay.discovery, "fetch_yahoo_history", return_value=(bars, {})):
                replay.settle_store(**kwargs)
            path = next(kwargs["outcome_root"].rglob("*.json"))
            immutable = path.read_bytes()
            config = json.loads(kwargs["config_path"].read_text(encoding="utf-8"))
            config["replay"]["version"] = "another-methodology"
            kwargs["config_path"].write_text(json.dumps(config), encoding="utf-8")
            with patch.object(replay.discovery, "fetch_yahoo_history") as fetch:
                result = replay.settle_store(**kwargs)
                fetch.assert_not_called()
            self.assertEqual(result["outcomes_written"], 0)
            self.assertEqual(path.read_bytes(), immutable)
            self.assertEqual(len(list(kwargs["outcome_root"].rglob("*.json"))), 1)

    def test_actual_source_change_or_corrupt_terminal_evidence_fails_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, kwargs = self._store(Path(tmp))
            replay_body = replay.replay_horizon(source, [bar("2026-09-11", 100, 104, 98, 103)], horizon_sessions=1, cost_stress_percent=0.1, replay_version="test")
            stored = replay.make_outcome(source, replay_body, replay_version="test")
            replay.persist(kwargs["outcome_root"], stored)
            path = replay.outcome_path(kwargs["outcome_root"], stored)
            immutable = path.read_bytes()
            changed = deepcopy(source)
            changed["candidate_state"]["risk_plan"]["stop"] = 94
            changed.pop("event_sha256")
            changed["event_sha256"] = contracts.payload_sha256(changed)
            (kwargs["experience_root"] / "event.json").write_text(json.dumps(changed), encoding="utf-8")
            with patch.object(replay.discovery, "fetch_yahoo_history") as fetch:
                with self.assertRaisesRegex(contracts.ContractError, "source binding"):
                    replay.settle_store(**kwargs)
                fetch.assert_not_called()
            self.assertEqual(path.read_bytes(), immutable)
            (kwargs["experience_root"] / "event.json").write_text(json.dumps(source), encoding="utf-8")
            invalid = deepcopy(stored)
            invalid["replay"]["net_r"] = 100
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with patch.object(replay.discovery, "fetch_yahoo_history") as fetch:
                with self.assertRaisesRegex(contracts.ContractError, "hash mismatch"):
                    replay.settle_store(**kwargs)
                fetch.assert_not_called()

    def test_target_hit_settles_before_full_horizon(self):
        rows = [
            bar("2026-09-11", 100, 104, 98, 103),
            bar("2026-09-12", 103, 111, 102, 109),
        ]
        result = replay.replay_horizon(
            event(),
            rows,
            horizon_sessions=5,
            cost_stress_percent=0.1,
            replay_version="test",
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["status"], "SETTLED")
        self.assertEqual(result["exit_reason"], "TARGET")
        self.assertEqual(result["sessions_held"], 2)

    def test_same_bar_stop_and_target_is_stop_first(self):
        rows = [bar("2026-09-11", 100, 112, 94, 101)]
        result = replay.replay_horizon(
            event(), rows, horizon_sessions=1, cost_stress_percent=0.0, replay_version="test"
        )
        assert result is not None
        self.assertEqual(result["exit_reason"], "STOP_FIRST")
        self.assertLess(result["net_r"], 0)

    def test_unresolved_horizon_returns_none(self):
        rows = [bar("2026-09-11", 100, 104, 98, 103)]
        result = replay.replay_horizon(
            event(), rows, horizon_sessions=5, cost_stress_percent=0.0, replay_version="test"
        )
        self.assertIsNone(result)

    def test_skip_above_prevents_counterfactual_activation(self):
        rows = [bar("2026-09-11", 106, 108, 104, 107)]
        result = replay.replay_horizon(
            event(skip_above=105), rows, horizon_sessions=1, cost_stress_percent=0.0, replay_version="test"
        )
        assert result is not None
        self.assertEqual(result["status"], "NOT_ACTIVATED")
        self.assertEqual(result["reason"], "next_session_open_above_frozen_skip")

    def test_gap_stop_uses_worse_open_after_activation(self):
        rows = [
            bar("2026-09-11", 100, 104, 98, 103),
            bar("2026-09-12", 90, 92, 88, 91),
        ]
        result = replay.replay_horizon(
            event(), rows, horizon_sessions=2, cost_stress_percent=0.0, replay_version="test"
        )
        assert result is not None
        self.assertEqual(result["exit_reason"], "GAP_STOP")
        self.assertEqual(result["exit_price"], 90.0)

    def test_outcome_hash_detects_tampering(self):
        rows = [bar("2026-09-11", 100, 103, 98, 102)]
        result = replay.replay_horizon(
            event(), rows, horizon_sessions=1, cost_stress_percent=0.1, replay_version="test"
        )
        assert result is not None
        payload = replay.make_outcome(event(), result, replay_version="test", settled_at="2026-09-12T00:00:00Z")
        replay.validate_outcome(payload)
        payload["replay"]["net_r"] = 99
        with self.assertRaises(Exception):
            replay.validate_outcome(payload)


if __name__ == "__main__":
    unittest.main()
