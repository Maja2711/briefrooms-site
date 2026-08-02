import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_brace_manual_submission as brace


class BraceManualSelectionTests(unittest.TestCase):
    def synthetic_market(self):
        benchmark = [100.0 * (1.0005 ** day) for day in range(100)]
        series = {}
        universe = list(brace.COMPANY_BY_TICKER)
        for index, ticker in enumerate(universe):
            drift = 0.0002 + index * 0.000025
            series[ticker] = [
                100.0 * ((1.0 + drift) ** day) * (1.0 + 0.0015 * math.sin(day / 6.0 + index))
                for day in range(100)
            ]
        return universe, series, benchmark

    def config(self, universe):
        return {
            "schema_version": "ai-tournament-config-v1",
            "tournament_id": "briefrooms-ai-tournament-2026-01",
            "title_pl": "AI Tournament",
            "title_en": "AI Tournament",
            "start_date": "2026-08-03",
            "end_date": "2026-11-03",
            "base_currency": "PLN",
            "starting_capital_pln": 10000.0,
            "benchmark_ticker": "SPY",
            "fx_ticker": "USDPLN=X",
            "universe_version": "briefrooms-ai-55-v1",
            "universe": universe,
            "rules": {
                "long_only": True,
                "buy_and_hold": True,
                "fractional_shares": True,
                "max_positions": 6,
                "min_position_weight": 0.05,
                "max_position_weight": 0.30,
                "min_cash_weight": 0.0,
                "max_cash_weight": 0.20,
                "max_daily_turnover": 1.0,
                "transaction_cost_pct": 0.0,
                "slippage_pct": 0.0,
                "min_trade_pln": 0.0,
            },
            "agents": [{"id": "BRACE", "provider": "brace", "default_model": "BRACE-v1"}],
            "data_dir": "data/ai_tournament",
        }

    def test_selection_produces_valid_locked_portfolio(self):
        universe, series, benchmark = self.synthetic_market()
        ranked, rejected = brace.rank_universe(series, benchmark, universe)
        self.assertFalse(rejected)
        selected = brace.choose_stocks(ranked)
        submission = brace.build_submission(selected, brace.cash_weight_pct(benchmark))
        brace.validate_submission(submission)
        self.assertEqual(len(submission["allocations"]), 6)
        self.assertEqual(
            sum(row["weight_pct"] for row in submission["allocations"]) + submission["cash_weight_pct"],
            100,
        )
        categories = [brace.CATEGORY_BY_TICKER[row["ticker"]] for row in submission["allocations"]]
        self.assertLessEqual(categories.count("semiconductors"), 2)
        self.assertLessEqual(categories.count("mega_platform"), 2)

    def test_cash_regimes_are_deterministic(self):
        risk_on = [100.0 * (1.001 ** day) for day in range(80)]
        neutral = [100.0] * 60 + [100.0 - 0.03 * day for day in range(20)]
        risk_off = [100.0 * (0.998 ** day) for day in range(80)]
        self.assertEqual(brace.cash_weight_pct(risk_on), 5)
        self.assertEqual(brace.cash_weight_pct(neutral), 10)
        self.assertEqual(brace.cash_weight_pct(risk_off), 20)

    def test_generate_writes_submission_commitment_and_audit_once(self):
        universe, series, benchmark = self.synthetic_market()
        config = self.config(universe)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            submission_path = root / "submissions" / "brace.json"
            commitment_path = root / "commitments" / "brace.json"
            audit_path = root / "brace_selection_audit.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            manifest = {
                "provider": "synthetic",
                "market_data_cutoff": brace.FREEZE_DATE,
                "series_lengths": {ticker: len(values) for ticker, values in series.items()},
            }
            with mock.patch.object(brace, "fetch_frozen_series", return_value=(series, benchmark, manifest)):
                submission = brace.generate(config_path, submission_path, commitment_path, audit_path)
            self.assertTrue(submission_path.exists())
            self.assertTrue(commitment_path.exists())
            self.assertTrue(audit_path.exists())
            brace.validate_locked(submission_path, commitment_path, audit_path)
            self.assertEqual(submission["agent_name"], "BRACE")
            with mock.patch.object(brace, "fetch_frozen_series", return_value=(series, benchmark, manifest)):
                with self.assertRaises(brace.BraceSelectionError):
                    brace.generate(config_path, submission_path, commitment_path, audit_path)

    def test_locked_hash_detects_tampering(self):
        universe, series, benchmark = self.synthetic_market()
        config = self.config(universe)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            submission_path = root / "submissions" / "brace.json"
            commitment_path = root / "commitments" / "brace.json"
            audit_path = root / "brace_selection_audit.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with mock.patch.object(
                brace,
                "fetch_frozen_series",
                return_value=(series, benchmark, {"provider": "synthetic"}),
            ):
                brace.generate(config_path, submission_path, commitment_path, audit_path)
            payload = json.loads(submission_path.read_text(encoding="utf-8"))
            payload["allocations"][0]["weight_pct"] -= 1
            submission_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(brace.BraceSelectionError):
                brace.validate_locked(submission_path, commitment_path, audit_path)


if __name__ == "__main__":
    unittest.main()
