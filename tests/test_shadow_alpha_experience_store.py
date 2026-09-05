import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.experience_store import build_experiences, materialize, read_experiences
from scripts.learning_ledger import append_event
from scripts.shadow_alpha_evaluator import evaluate, evaluate_group


class ExperienceStoreTests(unittest.TestCase):
    def test_pairs_only_later_outcome_with_decision(self):
        decision = {
            "event_id": "d1",
            "event_type": "decision",
            "subject_id": "trade-1",
            "occurred_at": "2026-09-01T08:00:00Z",
            "source_ref": "us-daily://2026-09-01",
            "payload": {"market": "us", "symbol": "ABC", "decision": "TRADE", "conviction": 0.7},
        }
        outcome = {
            "event_id": "o1",
            "event_type": "outcome",
            "subject_id": "trade-1",
            "occurred_at": "2026-09-02T08:00:00Z",
            "source_ref": "us-daily://outcome/trade-1",
            "payload": {
                "return_percent": 1.5,
                "gross_return_percent": 1.7,
                "entry_at": "2026-09-01T09:00:00Z",
                "exit_at": "2026-09-01T15:00:00Z",
                "turnover_fraction": 2.0,
            },
        }
        rows = build_experiences([decision, outcome])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "SETTLED")
        self.assertEqual(rows[0]["engine"], "us")
        self.assertEqual(rows[0]["action"], "LONG")
        self.assertAlmostEqual(rows[0]["outcome"]["net_return_fraction"], 0.015)
        self.assertAlmostEqual(rows[0]["outcome"]["cost_fraction"], 0.002)
        self.assertEqual(rows[0]["outcome"]["entry_at"], "2026-09-01T09:00:00Z")
        self.assertEqual(rows[0]["outcome"]["exit_at"], "2026-09-01T15:00:00Z")
        self.assertEqual(rows[0]["outcome"]["turnover_fraction"], 2.0)

    def test_same_time_outcome_is_not_bound(self):
        events = [
            {
                "event_id": "d1", "event_type": "decision", "subject_id": "x",
                "occurred_at": "2026-09-01T08:00:00Z", "source_ref": "test://x",
                "payload": {"direction": "LONG"},
            },
            {
                "event_id": "o1", "event_type": "outcome", "subject_id": "x",
                "occurred_at": "2026-09-01T08:00:00Z", "source_ref": "test://x",
                "payload": {"return_fraction": 0.5},
            },
        ]
        row = build_experiences(events)[0]
        self.assertEqual(row["status"], "PENDING")
        self.assertIsNone(row["outcome"])

    def test_materialization_is_deterministic_and_ledger_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "learning.jsonl"
            store = root / "experiences.jsonl"
            status = root / "status.json"
            t0 = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
            append_event(
                ledger, event_type="decision", occurred_at=t0.isoformat(), subject_id="x",
                source_ref="eurusd-daily://trade/x",
                payload={"instrument": "EUR/USD", "direction": "LONG", "engine_version": "v1"},
            )
            append_event(
                ledger, event_type="outcome", occurred_at=(t0 + timedelta(days=1)).isoformat(), subject_id="x",
                source_ref="eurusd-daily://outcome/x", payload={"return_fraction": 0.01},
            )
            first = materialize(ledger, store, status)
            first_text = store.read_text(encoding="utf-8")
            second = materialize(ledger, store, status)
            self.assertEqual(first_text, store.read_text(encoding="utf-8"))
            self.assertEqual(first["experience_count"], second["experience_count"])
            self.assertEqual(len(read_experiences(store)), 1)


class ShadowAlphaEvaluatorTests(unittest.TestCase):
    @staticmethod
    def experiences(returns, benchmark=None):
        rows = []
        for i, value in enumerate(returns):
            outcome = {"net_return_fraction": value}
            if benchmark is not None:
                outcome["benchmark_return_fraction"] = benchmark
            rows.append({
                "experience_id": f"e{i}",
                "engine": "brace",
                "instrument": "TEST",
                "action": "LONG",
                "status": "SETTLED",
                "outcome": outcome,
            })
        return rows

    def test_minimum_sample_gate_blocks_early_alpha_claim(self):
        result = evaluate_group(self.experiences([0.01] * 10), minimum=30)
        self.assertEqual(result["raw_edge"]["status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["formal_alpha"]["status"], "NOT_MEASURABLE")

    def test_positive_raw_edge_is_not_called_formal_alpha_without_benchmark(self):
        result = evaluate_group(self.experiences([0.01] * 40), minimum=30)
        self.assertEqual(result["raw_edge"]["status"], "POSITIVE_EVIDENCE")
        self.assertFalse(result["formal_alpha"]["available"])
        self.assertEqual(result["assessment_basis"], "raw_edge_only_no_complete_benchmark")

    def test_benchmark_adjusted_alpha_can_be_measured(self):
        result = evaluate_group(self.experiences([0.012] * 40, benchmark=0.002), minimum=30)
        self.assertTrue(result["formal_alpha"]["available"])
        self.assertEqual(result["formal_alpha"]["status"], "POSITIVE_EVIDENCE")
        self.assertEqual(result["assessment_basis"], "benchmark_adjusted_return")

    def test_report_separates_engines_and_instruments(self):
        rows = self.experiences([0.01, -0.005])
        report = evaluate(rows, minimum=2)
        self.assertIn("brace", report["by_engine"])
        self.assertIn("brace:TEST", report["by_engine_instrument"])
        self.assertTrue(report["zero_authority"])

    def test_trading_performance_adds_risk_adjusted_r_cost_exposure_and_turnover_metrics(self):
        rows = [
            {
                "engine": "eurusd", "instrument": "EUR/USD", "action": "LONG", "status": "SETTLED",
                "outcome": {
                    "net_return_fraction": 0.02, "r_multiple": 1.5, "cost_fraction": 0.001,
                    "entry_at": "2026-09-01T08:00:00Z", "exit_at": "2026-09-01T10:00:00Z",
                    "turnover_fraction": 2.0,
                },
            },
            {
                "engine": "eurusd", "instrument": "EUR/USD", "action": "SHORT", "status": "SETTLED",
                "outcome": {
                    "net_return_fraction": -0.01, "r_multiple": -0.5, "cost_fraction": 0.002,
                    "entry_at": "2026-09-01T12:00:00Z", "exit_at": "2026-09-01T14:00:00Z",
                    "turnover_fraction": 2.0,
                },
            },
            {
                "engine": "eurusd", "instrument": "EUR/USD", "action": "LONG", "status": "SETTLED",
                "outcome": {
                    "net_return_fraction": 0.01, "r_multiple": 1.0, "cost_fraction": 0.0015,
                    "entry_at": "2026-09-01T13:00:00Z", "exit_at": "2026-09-01T16:00:00Z",
                    "turnover_fraction": 2.0,
                },
            },
            {
                "engine": "eurusd", "instrument": "EUR/USD", "action": "FLAT", "status": "SETTLED",
                "outcome": {"net_return_fraction": 0.5, "r_multiple": 99.0},
            },
        ]
        perf = evaluate_group(rows, minimum=2)["trading_performance"]
        self.assertEqual(perf["n_trades"], 3)
        self.assertEqual(perf["settled_with_return"], 3)
        self.assertAlmostEqual(perf["expectancy_return_fraction"], (0.02 - 0.01 + 0.01) / 3)
        self.assertAlmostEqual(perf["hit_rate"], 2 / 3)
        self.assertAlmostEqual(perf["average_r_multiple"], (1.5 - 0.5 + 1.0) / 3)
        self.assertAlmostEqual(perf["mean_cost_fraction"], 0.0015)
        self.assertAlmostEqual(perf["cumulative_turnover_fraction"], 6.0)
        self.assertAlmostEqual(perf["turnover_coverage_fraction"], 1.0)
        self.assertAlmostEqual(perf["exposure_interval_coverage_fraction"], 1.0)
        self.assertAlmostEqual(perf["time_in_market_fraction"], 5 / 8)
        self.assertIsNotNone(perf["sharpe_per_trade"])
        self.assertIsNotNone(perf["sortino_per_trade"])
        self.assertEqual(perf["risk_adjusted_basis"], "per_trade_non_annualized_zero_rf_mar")

    def test_exposure_and_turnover_are_not_fabricated_when_source_metadata_missing(self):
        perf = evaluate_group(self.experiences([0.01, -0.005]), minimum=2)["trading_performance"]
        self.assertIsNone(perf["time_in_market_fraction"])
        self.assertEqual(perf["exposure_interval_coverage_fraction"], 0.0)
        self.assertIsNone(perf["cumulative_turnover_fraction"])
        self.assertEqual(perf["turnover_coverage_fraction"], 0.0)


if __name__ == "__main__":
    unittest.main()
