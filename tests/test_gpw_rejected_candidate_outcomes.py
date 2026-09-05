from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts import daily_stock_rejected_candidate_freeze as freeze
from scripts import gpw_rejected_candidate_outcomes as outcomes

WARSAW = ZoneInfo("Europe/Warsaw")


def gate(name: str, passed: bool, *, hard: bool) -> dict:
    return {"name": name, "passed": passed, "hard": hard, "stage": "test", "reason": name}


def candidate(symbol: str, *, entry: float, stop: float, target: float, hard_blocked: bool = False) -> dict:
    gates = [gate("market_data", True, hard=True)]
    if hard_blocked:
        gates.append(gate("liquidity", False, hard=True))
    gates.append(gate("final_selection_rank", False, hard=False))
    row = {
        "candidate_id": f"gpw:2026-09-07:{symbol}:LONG",
        "market": "gpw",
        "symbol": symbol,
        "name": symbol,
        "sector": "test",
        "action": "LONG",
        "selected": False,
        "decision_at": "2026-09-07T10:00:00+02:00",
        "expected_session": "2026-09-04",
        "first_blocking_gate": gates[1] if hard_blocked else gates[-1],
        "decision_path": {"gates": gates, "explicit_engine_rejection": None, "producer_decision": "TRANSAKCJA", "producer_reason": "test"},
        "score_state": {"rank": 2 if not hard_blocked else 3, "shortlist_limit": 8, "quant_pre_score": 70.0, "composite_score": None, "scores": {}, "returns": {}},
        "market_state": {"last_session": "2026-09-04", "reference_price": entry, "median_turnover": 10_000_000, "turnover_threshold": 1_000_000, "risk_percent": 0.05},
        "risk_plan": {
            "reference_price": entry,
            "entry_zone": [entry * 0.99, entry * 1.01],
            "stop": stop,
            "target": target,
            "reward_risk": 2.0,
            "risk_percent": (entry - stop) / entry,
            "atr": 1.0,
            "plan_source": "engine_quant_candidate",
            "activation_rule": "diagnostic_reference_plan_only; future settler must respect frozen entry-zone activation",
        },
        "settlement_eligibility": {"eligible": True, "mode": "risk_plan", "reason": "prospectively_frozen_risk_plan"},
        "governance": {"decision_influence": False, "ranking_writeback": False, "gate_writeback": False, "historical_backfill": False, "news_or_llm_rerun": False},
    }
    row["state_sha256"] = freeze._sha(row)
    return row


def make_freeze() -> dict:
    body = {
        "schema_version": freeze.SCHEMA_VERSION,
        "market": "gpw",
        "date": "2026-09-07",
        "decision_at": "2026-09-07T10:00:00+02:00",
        "frozen_at": "2026-09-07T08:01:00Z",
        "source_payload_sha256": "source",
        "status": "frozen",
        "selected_symbol": "AAA.WA",
        "candidate_count": 2,
        "economically_evaluable_count": 2,
        "candidates": [
            candidate("BBB.WA", entry=50.0, stop=45.0, target=60.0),
            candidate("CCC.WA", entry=20.0, stop=18.0, target=40.0, hard_blocked=True),
        ],
        "contract": {
            "prospective_only": True,
            "preserve_existing_same_decision": True,
            "news_or_llm_rerun": False,
            "source_engine_writeback": False,
            "decision_influence": False,
            "future_outcome_requires_preexisting_freeze": True,
        },
    }
    body["freeze_sha256"] = freeze._sha(body)
    return body


def payload() -> dict:
    return {
        "date": "2026-09-07",
        "generated_at": "2026-09-07T10:00:00+02:00",
        "decision": "TRANSAKCJA",
        "reason": "test selection",
        "selection": {
            "symbol": "AAA.WA",
            "ticker": "AAA",
            "name": "AAA",
            "sector": "test",
            "entry_zone": [99.0, 101.0],
            "stop": 95.0,
            "target": 110.0,
            "reward_risk": 2.0,
            "reference_price": 100.0,
        },
        freeze.FIELD: make_freeze(),
    }


def config() -> dict:
    return {"non_session_dates": []}


def bar(ts: str, *, open_: float, high: float, low: float, close: float) -> dict:
    return {"timestamp": datetime.fromisoformat(ts), "open": open_, "high": high, "low": low, "close": close}


def daily(day: str, close: float) -> dict:
    return {"day": day, "close": close}


class RejectedCandidateOutcomeTests(unittest.TestCase):
    def test_capture_requires_preexisting_prospective_freeze(self):
        p = payload()
        p.pop(freeze.FIELD)
        with self.assertRaisesRegex(ValueError, "historical reconstruction is forbidden"):
            outcomes.build_record(p, config=config())

    def test_source_snapshot_is_hash_protected_and_zero_authority(self):
        record = outcomes.build_record(payload(), config=config(), captured_at=datetime(2026, 9, 7, 8, 2, tzinfo=ZoneInfo("UTC")))
        outcomes.verify_record(record)
        mutated = copy.deepcopy(record)
        mutated["source_snapshot"]["selected_plan"]["stop"] = 1.0
        with self.assertRaisesRegex(ValueError, "immutable source snapshot hash mismatch"):
            outcomes.verify_record(mutated)
        governance = record["source_snapshot"]["governance"]
        self.assertFalse(governance["decision_influence"])
        self.assertFalse(governance["automatic_learning_writeback"])
        self.assertFalse(governance["historical_backfill"])

    def test_capture_is_idempotent_and_refuses_source_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "gpw.json"
            source_path.write_text(__import__("json").dumps(payload()), encoding="utf-8")
            store = Path(tmp) / "store"
            first_path, changed = outcomes.capture_current(payload_path=source_path, store_dir=store, now=datetime(2026, 9, 7, 8, 2, tzinfo=ZoneInfo("UTC")))
            self.assertTrue(changed)
            second_path, changed = outcomes.capture_current(payload_path=source_path, store_dir=store, now=datetime(2026, 9, 7, 8, 3, tzinfo=ZoneInfo("UTC")))
            self.assertFalse(changed)
            self.assertEqual(first_path, second_path)

    def test_t1_t2_settlement_and_opportunity_cost_use_only_legal_alternatives(self):
        record = outcomes.build_record(payload(), config=config())
        intraday = {
            "AAA.WA": [bar("2026-09-07T10:05:00+02:00", open_=100.0, high=101.0, low=99.5, close=100.5)],
            "BBB.WA": [bar("2026-09-07T10:05:00+02:00", open_=50.0, high=51.0, low=49.5, close=50.5)],
            "CCC.WA": [bar("2026-09-07T10:05:00+02:00", open_=20.0, high=21.0, low=19.5, close=20.5)],
        }
        daily_rows = {
            "AAA.WA": [daily("2026-09-08", 101.0), daily("2026-09-09", 102.0)],
            "BBB.WA": [daily("2026-09-08", 52.0), daily("2026-09-09", 55.0)],
            "CCC.WA": [daily("2026-09-08", 25.0), daily("2026-09-09", 30.0)],
        }
        settled = outcomes.settle_record(
            record,
            config=config(),
            now=datetime(2026, 9, 9, 18, 0, tzinfo=WARSAW),
            intraday_fetcher=lambda symbol: intraday[symbol],
            daily_fetcher=lambda symbol: daily_rows[symbol],
        )
        summary = settled["settlement"]["summary"]
        self.assertEqual(summary["status"], "RESOLVED")
        self.assertEqual(summary["best_rejected_symbol"], "BBB.WA")
        self.assertAlmostEqual(summary["selected_return_percent"], 1.62, places=2)
        self.assertAlmostEqual(summary["best_rejected_return_percent"], 9.62, places=2)
        self.assertAlmostEqual(summary["opportunity_cost_percent"], 8.0, places=2)
        rejected = {row["symbol"]: row for row in settled["settlement"]["rejected_candidates"]}
        self.assertTrue(rejected["BBB.WA"]["opportunity_candidate"])
        self.assertFalse(rejected["CCC.WA"]["opportunity_candidate"])
        self.assertTrue(rejected["CCC.WA"]["hard_blocked"])
        self.assertAlmostEqual(outcomes._complete_return(rejected["CCC.WA"], 2), 49.62, places=2)

    def test_missing_legal_candidate_market_data_is_data_gap_not_zero(self):
        record = outcomes.build_record(payload(), config=config())
        intraday = {
            "AAA.WA": [bar("2026-09-07T10:05:00+02:00", open_=100.0, high=101.0, low=99.5, close=100.5)],
            "CCC.WA": [bar("2026-09-07T10:05:00+02:00", open_=20.0, high=21.0, low=19.5, close=20.5)],
        }
        daily_rows = {
            "AAA.WA": [daily("2026-09-08", 101.0), daily("2026-09-09", 102.0)],
            "CCC.WA": [daily("2026-09-08", 25.0), daily("2026-09-09", 30.0)],
        }

        def intraday_fetcher(symbol: str):
            if symbol == "BBB.WA":
                raise RuntimeError("provider unavailable")
            return intraday[symbol]

        settled = outcomes.settle_record(
            record,
            config=config(),
            now=datetime(2026, 9, 9, 18, 0, tzinfo=WARSAW),
            intraday_fetcher=intraday_fetcher,
            daily_fetcher=lambda symbol: daily_rows[symbol],
        )
        self.assertEqual(settled["settlement"]["status"], "DATA_GAP")
        self.assertEqual(settled["settlement"]["summary"]["status"], "DATA_GAP")
        self.assertIn("BBB.WA", settled["settlement"]["summary"]["missing_symbols"])

    def test_before_t2_layer_stays_pending(self):
        record = outcomes.build_record(payload(), config=config())
        intraday = {symbol: [bar("2026-09-07T10:05:00+02:00", open_=price, high=price + 0.5, low=price - 0.5, close=price)] for symbol, price in {"AAA.WA": 100.0, "BBB.WA": 50.0, "CCC.WA": 20.0}.items()}
        daily_rows = {symbol: [daily("2026-09-08", price)] for symbol, price in {"AAA.WA": 101.0, "BBB.WA": 52.0, "CCC.WA": 25.0}.items()}
        settled = outcomes.settle_record(
            record,
            config=config(),
            now=datetime(2026, 9, 8, 18, 0, tzinfo=WARSAW),
            intraday_fetcher=lambda symbol: intraday[symbol],
            daily_fetcher=lambda symbol: daily_rows[symbol],
        )
        self.assertEqual(settled["settlement"]["status"], "PENDING")
        self.assertEqual(settled["settlement"]["summary"]["reason"], "t_plus_2_not_complete")


if __name__ == "__main__":
    unittest.main()
