import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ai_tournament_engine as engine
import install_ai_tournament_ui as installer


class TournamentTests(unittest.TestCase):
    def rules(self):
        return {
            "max_positions": 3,
            "max_position_weight": 0.30,
            "min_cash_weight": 0.05,
            "max_daily_turnover": 0.40,
            "transaction_cost_pct": 0.001,
            "slippage_pct": 0.0005,
            "min_trade_pln": 10.0,
        }

    def state(self):
        return {
            "agent_id": "Test",
            "cash_pln": 10000.0,
            "positions": {},
            "pending_target": None,
            "nav_history": [],
            "closed_trades": [],
            "total_costs_pln": 0.0,
        }

    def test_normalization_enforces_positions_caps_cash_and_turnover(self):
        targets, adjustments = engine.normalize_target_weights(
            {"AAPL": .8, "MSFT": .3, "NVDA": .2, "BAD": .5},
            ["AAPL", "MSFT", "NVDA"], self.rules(), {"AAPL": .1}
        )
        self.assertLessEqual(len(targets), 3)
        self.assertLessEqual(max(targets.values()), .30)
        self.assertLessEqual(sum(targets.values()), .95 + 1e-8)
        turnover = .5 * sum(abs(targets.get(t, 0)-({"AAPL":.1}.get(t,0))) for t in set(targets)|{"AAPL"})
        self.assertLessEqual(turnover, .40 + 1e-8)
        self.assertTrue(adjustments)

    def test_pending_decision_executes_only_on_later_session(self):
        state = self.state()
        state["pending_target"] = {"decision_id":"d1", "created_session":"2026-08-03", "target_weights":{"AAPL":.30}}
        same = engine.execute_pending_target(state, {"AAPL":100}, 4.0, self.rules(), "2026-08-03")
        self.assertIsNone(same)
        execution = engine.execute_pending_target(state, {"AAPL":100}, 4.0, self.rules(), "2026-08-04")
        self.assertIsNotNone(execution)
        self.assertGreater(state["positions"]["AAPL"]["shares"], 0)
        self.assertIsNone(state["pending_target"])

    def test_ledger_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            engine.append_ledger(path, {"event_type":"TEST", "value":1})
            engine.verify_ledger(path)
            row = json.loads(path.read_text(encoding="utf-8"))
            row["value"] = 2
            path.write_text(json.dumps(row)+"\n", encoding="utf-8")
            with self.assertRaises(engine.ValidationError):
                engine.verify_ledger(path)

    def test_ranking_uses_return_then_drawdown(self):
        rows = [
            {"agent_id":"B", "metrics":{"return_pct":.05,"max_drawdown_pct":-.03,"sharpe":1}},
            {"agent_id":"A", "metrics":{"return_pct":.05,"max_drawdown_pct":-.01,"sharpe":.5}},
            {"agent_id":"C", "metrics":{"return_pct":.02,"max_drawdown_pct":0,"sharpe":2}},
        ]
        ranked = engine.rank_rows(rows)
        self.assertEqual([r["agent_id"] for r in ranked], ["A","B","C"])

    def test_public_validation_rejects_rank_gap(self):
        public = {"schema_version":engine.SCHEMA_VERSION,"leaderboard":[{"rank":2,"metrics":{"portfolio_value_pln":10000}}]}
        with self.assertRaises(engine.ValidationError):
            engine.validate_public(public)

    def test_ui_installer_is_idempotent(self):
        source = '<html><body><script src="/scripts/ai-tournament-public.js?v=old" defer></script></body></html>'
        once = installer.patch_text(source)
        twice = installer.patch_text(once)
        self.assertEqual(once, twice)
        self.assertEqual(len(installer.PATTERN.findall(once)), 1)
        self.assertIn(installer.SCRIPT, once)


if __name__ == "__main__":
    unittest.main()
