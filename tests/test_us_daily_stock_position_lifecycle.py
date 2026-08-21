from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import us_daily_stock_position_lifecycle as lifecycle
from scripts import us_daily_stock_runtime as runtime

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 21, 10, 30, tzinfo=NY)


def trade_payload(day: str, *, symbol: str = "MRK", score: float = 80.0, ref: float = 151.30,
                  stop: float = 147.48, target: float = 158.17, mark: float | None = None,
                  outcome_status: str = "PENDING") -> dict:
    mark = ref if mark is None else mark
    return {
        "date": day,
        "generated_at": f"{day}T10:10:00-04:00",
        "decision": "TRADE",
        "selection": {
            "symbol": symbol,
            "ticker": symbol,
            "name": symbol,
            "sector": "healthcare",
            "score": score,
            "reference_price": ref,
            "entry_zone": [round(ref * .997, 2), round(ref * 1.006, 2)],
            "stop": stop,
            "target": target,
            "reward_risk": 1.8,
            "valid_until": "2026-08-24",
            "market_snapshot": {
                "date": day,
                "last": mark,
                "high": mark + 1.0,
                "low": mark - 1.0,
            },
        },
        "outcome": {"status": outcome_status, "activated": None},
    }


def write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class USDailyStockPositionLifecycleTests(unittest.TestCase):
    def test_bootstrap_collapses_three_mrk_daily_signals_into_one_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp)
            write(history / "2026-08-19.json", trade_payload("2026-08-19", score=80.14, ref=151.30, stop=147.48, target=158.17))
            write(history / "2026-08-20.json", trade_payload("2026-08-20", score=84.15, ref=149.65, stop=145.18, target=157.70))
            write(history / "2026-08-21.json", trade_payload("2026-08-21", score=78.89, ref=154.34, stop=149.53, target=162.99))

            book = lifecycle.bootstrap_book(history, now=NOW, persist_suppression=True)
            pos = book["open_position"]
            self.assertEqual(pos["symbol"], "MRK")
            self.assertEqual(pos["source_history_date"], "2026-08-19")
            self.assertEqual(pos["entry"], 151.30)
            self.assertEqual(pos["stop"], 147.48)
            self.assertEqual(pos["target"], 158.17)
            self.assertEqual(len(book["suppressed_signals"]), 2)

            for day in ("2026-08-20", "2026-08-21"):
                payload = json.loads((history / f"{day}.json").read_text())
                self.assertEqual(payload["outcome"]["status"], "SUPPRESSED")
                self.assertFalse(payload["outcome"]["activated"])
                self.assertEqual(payload["position_lifecycle"]["canonical_source_history_date"], "2026-08-19")

            included, suppressed = lifecycle.canonical_history_payloads(history)
            self.assertEqual([row["date"] for row in included], ["2026-08-19"])
            self.assertEqual(len(suppressed), 2)

    def test_suppressed_legacy_signals_never_reappear_after_canonical_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp)
            write(history / "2026-08-19.json", trade_payload("2026-08-19"))
            write(history / "2026-08-20.json", trade_payload("2026-08-20", ref=152.0, stop=148.0, target=159.2))
            book = lifecycle.bootstrap_book(history, now=NOW, persist_suppression=True)
            book, closure = lifecycle.reconcile_open_position(
                book, {"high": 159.0, "low": 150.0, "last": 158.5}, now=NOW
            )
            self.assertIsNotNone(closure)
            lifecycle.apply_closure_to_history(history, closure)
            included, suppressed = lifecycle.canonical_history_payloads(history)
            self.assertEqual([row["date"] for row in included], ["2026-08-19"])
            self.assertEqual([row["date"] for row in suppressed], ["2026-08-20"])

    def test_same_symbol_is_hold_not_new_position(self):
        position = lifecycle.position_from_trade(trade_payload("2026-08-19", score=80.0))
        position["current_r"] = 0.8
        candidate = trade_payload("2026-08-21", symbol="MRK", score=99.0)
        rotate, reason = lifecycle.should_rotate(position, candidate)
        self.assertFalse(rotate)
        self.assertEqual(reason, "same_symbol_hold")

    def test_rotation_requires_both_material_score_edge_and_existing_profit(self):
        position = lifecycle.position_from_trade(trade_payload("2026-08-19", score=80.0))
        candidate = trade_payload("2026-08-21", symbol="NVDA", score=92.0, ref=180.0, stop=175.0, target=189.0)

        position["current_r"] = 0.10
        self.assertEqual(lifecycle.should_rotate(position, candidate), (False, "current_trade_not_profitable_enough_to_rotate"))

        position["current_r"] = 0.50
        weak = trade_payload("2026-08-21", symbol="NVDA", score=87.0, ref=180.0, stop=175.0, target=189.0)
        self.assertEqual(lifecycle.should_rotate(position, weak), (False, "score_edge_too_small"))
        self.assertEqual(lifecycle.should_rotate(position, candidate), (True, "stronger_setup_and_current_trade_profitable"))

    def test_target_stop_and_same_bar_resolution(self):
        base_book = lifecycle.empty_book(NOW)
        base_book = lifecycle.open_from_payload(base_book, trade_payload("2026-08-19"))

        _, tp = lifecycle.reconcile_open_position(
            base_book, {"high": 158.50, "low": 150.0, "last": 158.2}, now=NOW
        )
        self.assertEqual(tp["exit_reason"], "target")
        self.assertGreater(tp["r_multiple"], 0)

        _, sl = lifecycle.reconcile_open_position(
            base_book, {"high": 153.0, "low": 147.0, "last": 147.2}, now=NOW
        )
        self.assertEqual(sl["exit_reason"], "stop")
        self.assertLess(sl["r_multiple"], 0)

        _, ambiguous = lifecycle.reconcile_open_position(
            base_book, {"high": 159.0, "low": 147.0, "last": 154.0}, now=NOW
        )
        self.assertEqual(ambiguous["exit_reason"], "stop")
        self.assertTrue(ambiguous["conservative_same_bar"])

    def test_horizon_exit_only_when_monitor_allows_end_of_horizon(self):
        payload = trade_payload("2026-08-19")
        payload["selection"]["valid_until"] = "2026-08-21"
        book = lifecycle.open_from_payload(lifecycle.empty_book(NOW), payload)
        marked, closure = lifecycle.reconcile_open_position(
            book, {"high": 154.0, "low": 150.0, "last": 153.0}, now=NOW, horizon_exit_allowed=False
        )
        self.assertIsNone(closure)
        self.assertIsNotNone(marked["open_position"])
        _, closure = lifecycle.reconcile_open_position(
            book, {"high": 154.0, "low": 150.0, "last": 153.0}, now=NOW, horizon_exit_allowed=True
        )
        self.assertEqual(closure["exit_reason"], "horizon")
        self.assertEqual(closure["exit_price"], 153.0)

    def test_apply_closure_updates_only_canonical_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp)
            original = trade_payload("2026-08-19")
            write(history / "2026-08-19.json", original)
            book = lifecycle.open_from_payload(lifecycle.empty_book(NOW), original)
            _, closure = lifecycle.reconcile_open_position(
                book, {"high": 158.3, "low": 150.0, "last": 158.2}, now=NOW
            )
            path = lifecycle.apply_closure_to_history(history, closure)
            self.assertEqual(path.name, "2026-08-19.json")
            updated = json.loads(path.read_text())
            self.assertEqual(updated["outcome"]["status"], "RESOLVED")
            self.assertTrue(updated["outcome"]["activated"])
            self.assertEqual(updated["outcome"]["exit_reason"], "target")

    def test_hold_payload_preserves_original_trade_geometry(self):
        canonical = trade_payload("2026-08-19", ref=151.30, stop=147.48, target=158.17)
        book = lifecycle.open_from_payload(lifecycle.empty_book(NOW), canonical)
        book["open_position"]["last_mark"] = 154.34
        book["open_position"]["unrealized_percent"] = 2.009
        book["open_position"]["current_r"] = 0.796
        hold = lifecycle.hold_payload(canonical, book, now=NOW, candidate_watch=trade_payload("2026-08-21", score=78.9))
        self.assertEqual(hold["position_action"], "HOLD")
        self.assertEqual(hold["selection"]["reference_price"], 151.30)
        self.assertEqual(hold["selection"]["stop"], 147.48)
        self.assertEqual(hold["selection"]["target"], 158.17)
        self.assertEqual(hold["position"]["mark"], 154.34)
        self.assertTrue(hold["candidate_watch"]["same_as_open_position"])

    def test_position_snapshot_excludes_pre_entry_low_on_same_day(self):
        pre = datetime(2026, 8, 21, 9, 35, tzinfo=NY)
        post1 = datetime(2026, 8, 21, 10, 11, tzinfo=NY)
        post2 = datetime(2026, 8, 21, 10, 12, tzinfo=NY)
        yahoo = {
            "chart": {"result": [{
                "timestamp": [int(pre.timestamp()), int(post1.timestamp()), int(post2.timestamp())],
                "indicators": {"quote": [{
                    "open": [150.0, 151.2, 151.5],
                    "high": [151.0, 152.0, 152.4],
                    "low": [145.0, 150.9, 151.2],
                    "close": [150.5, 151.6, 152.0],
                    "volume": [1000, 1200, 900],
                }]},
            }]},
        }
        raw = json.dumps(yahoo).encode("utf-8")
        with patch.object(runtime.us, "request_bytes", return_value=raw):
            snap = runtime.position_snapshot(
                "MRK",
                opened_at="2026-08-21T10:10:00-04:00",
                now=NOW,
            )
        self.assertEqual(snap["path_start_at"], "2026-08-21T10:11:00-04:00")
        self.assertEqual(snap["low"], 150.9)
        self.assertNotEqual(snap["low"], 145.0)
        self.assertEqual(snap["last"], 152.0)


if __name__ == "__main__":
    unittest.main()
