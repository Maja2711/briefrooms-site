from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts import daily_stock_trade_timestamp_normalizer as timestamps


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class DailyStockTradeTimestampTests(unittest.TestCase):
    def test_us_backfills_only_from_trustworthy_market_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "history"
            book = root / "book.json"
            payload = {
                "date": "2026-08-19",
                "decision": "TRADE",
                "selection": {
                    "reference_price": 151.30,
                    "entry_zone": [150.85, 152.21],
                    "market_snapshot": {
                        "observed_at": "2026-08-19T10:10:36-04:00",
                        "last": 151.30,
                    },
                },
                "outcome": {
                    "status": "RESOLVED",
                    "activated": True,
                    "entry_price": 151.30,
                    "exit_price": 156.51,
                    "closed_at": "2026-08-25T15:59:59-04:00",
                },
            }
            write(history / "2026-08-19.json", payload)
            write(book, {
                "open_position": None,
                "closed_positions": [{
                    "source_history_date": "2026-08-19",
                    "opened_at": "2026-08-19T10:10:25-04:00",
                    "closed_at": "2026-08-25T15:59:59-04:00",
                    "entry": 151.30,
                    "exit_price": 156.51,
                }],
            })

            result = timestamps.normalize_us_history(history, book, enforce_from=date(2026, 8, 26))
            updated = json.loads((history / "2026-08-19.json").read_text())
            updated_book = json.loads(book.read_text())
            self.assertEqual(result["changed_history_files"], 1)
            self.assertEqual(updated["outcome"]["activated_at"], "2026-08-19T10:10:36-04:00")
            self.assertEqual(updated["outcome"]["closed_at"], "2026-08-25T15:59:59-04:00")
            self.assertEqual(updated_book["closed_positions"][0]["opened_at"], "2026-08-19T10:10:36-04:00")

    def test_legacy_us_missing_timestamp_evidence_stays_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "history"
            book = root / "book.json"
            write(history / "2026-08-19.json", {
                "date": "2026-08-19",
                "decision": "TRADE",
                "selection": {"entry_zone": [100, 102], "reference_price": 101},
                "outcome": {"status": "RESOLVED", "activated": True, "entry_price": 101, "exit_price": 103},
            })
            write(book, {"open_position": None, "closed_positions": [{
                "source_history_date": "2026-08-19", "entry": 101, "exit_price": 103,
            }]})
            timestamps.normalize_us_history(history, book, enforce_from=date(2026, 8, 26))
            updated = json.loads((history / "2026-08-19.json").read_text())
            self.assertNotIn("activated_at", updated["outcome"])
            self.assertNotIn("closed_at", updated["outcome"])

    def test_future_us_missing_entry_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "history"
            book = root / "book.json"
            write(history / "2026-08-26.json", {
                "date": "2026-08-26",
                "decision": "TRADE",
                "selection": {"entry_zone": [100, 102], "reference_price": 101},
                "outcome": {"status": "PENDING", "activated": True},
            })
            write(book, {"open_position": {
                "source_history_date": "2026-08-26", "entry": 101, "status": "OPEN",
            }, "closed_positions": []})
            with self.assertRaisesRegex(ValueError, "lacks trustworthy entry snapshot evidence"):
                timestamps.normalize_us_history(history, book, enforce_from=date(2026, 8, 26))

    def test_future_gpw_requires_entry_and_exit_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp)
            write(history / "2026-08-26.json", {
                "date": "2026-08-26",
                "decision": "TRANSAKCJA",
                "outcome": {
                    "status": "RESOLVED",
                    "activated": True,
                    "activated_at": "2026-08-26T09:20:00+02:00",
                    "entry_price": 50.0,
                    "exit_price": 52.0,
                },
            })
            with self.assertRaisesRegex(ValueError, "missing exit timestamp"):
                timestamps.validate_gpw_history(history, enforce_from=date(2026, 8, 26))

            payload = json.loads((history / "2026-08-26.json").read_text())
            payload["outcome"]["exit_bar_at"] = "2026-08-26T14:30:00+02:00"
            write(history / "2026-08-26.json", payload)
            self.assertEqual(
                timestamps.validate_gpw_history(history, enforce_from=date(2026, 8, 26))["violations"],
                0,
            )


if __name__ == "__main__":
    unittest.main()
