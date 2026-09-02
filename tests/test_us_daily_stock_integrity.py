from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import us_daily_stock_integrity as integrity
from scripts import us_daily_stock_position_lifecycle as lifecycle

NY = ZoneInfo("America/New_York")


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def trade_payload(day: str, *, symbol: str = "CRM") -> dict:
    return {
        "schema_version": "us-daily-stock-v1",
        "policy_version": "test",
        "date": day,
        "generated_at": f"{day}T10:00:00-04:00",
        "timezone": "America/New_York",
        "decision": "TRADE",
        "reason": "test",
        "locked": True,
        "selection": {
            "symbol": symbol,
            "ticker": symbol,
            "name": "Salesforce",
            "sector": "technology",
            "score": 70.0,
            "reference_price": 250.0,
            "entry_zone": [249.0, 251.0],
            "stop": 240.0,
            "target": 268.0,
            "reward_risk": 1.8,
            "valid_until": "2026-09-02",
            "market_snapshot": {"date": day, "last": 250.0, "high": 251.0, "low": 249.0},
        },
        "outcome": {"status": "PENDING", "activated": None},
        "metrics": {},
        "methodology": {},
    }


class UsDailyPositionIntegrityTests(unittest.TestCase):
    def test_final_session_of_week_uses_friday(self):
        cfg = {"non_session_dates": []}
        with patch.object(integrity.us, "is_session_day", side_effect=lambda d, _cfg: d.weekday() < 5):
            self.assertEqual(integrity.final_session_of_week(date(2026, 8, 31), cfg), date(2026, 9, 4))
            self.assertEqual(integrity.final_session_of_week(date(2026, 9, 4), cfg), date(2026, 9, 4))

    def test_final_session_of_week_steps_back_for_friday_holiday(self):
        cfg = {"non_session_dates": []}
        holiday = date(2026, 9, 4)
        with patch.object(integrity.us, "is_session_day", side_effect=lambda d, _cfg: d.weekday() < 5 and d != holiday):
            self.assertEqual(integrity.final_session_of_week(date(2026, 8, 31), cfg), date(2026, 9, 3))

    def test_repair_marks_canonical_trade_open_extends_week_and_rebuilds_index(self):
        now = datetime(2026, 9, 2, 5, 0, tzinfo=NY)
        cfg = {"non_session_dates": []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "history"
            book_path = root / "book.json"
            public_path = root / "public.json"
            canonical = trade_payload("2026-08-31")
            write(history / "2026-08-31.json", canonical)
            book = lifecycle.open_from_payload(lifecycle.empty_book(now), canonical)
            write(book_path, book)
            write(public_path, {"date": "2026-09-01", "decision": "DATA_ERROR", "selection": None})

            with patch.object(integrity.us, "load_config", return_value=cfg), \
                 patch.object(integrity.us, "is_session_day", side_effect=lambda d, _cfg: d.weekday() < 5):
                result = integrity.repair(
                    now=now, book_path=book_path, history_dir=history, public_path=public_path
                )
                verified = integrity.verify(
                    now=now, book_path=book_path, history_dir=history, public_path=public_path
                )

            self.assertEqual(result["symbol"], "CRM")
            self.assertEqual(result["valid_until"], "2026-09-04")
            self.assertEqual(result["indexed_open_trades"], 1)
            self.assertEqual(verified["index_schema"], "us-daily-stock-history-index-v3")

            repaired = json.loads((history / "2026-08-31.json").read_text())
            self.assertEqual(repaired["outcome"]["status"], "OPEN")
            self.assertTrue(repaired["outcome"]["activated"])
            self.assertEqual(repaired["selection"]["valid_until"], "2026-09-04")
            self.assertEqual(repaired["selection"]["holding_policy"], "END_OF_TRADING_WEEK")
            self.assertIn("Trzymaj do końca tygodnia", repaired["selection"]["time_stop_pl"])

            repaired_book = json.loads(book_path.read_text())
            self.assertEqual(repaired_book["open_position"]["valid_until"], "2026-09-04")
            public = json.loads(public_path.read_text())
            self.assertEqual(public["decision"], "TRADE")
            self.assertEqual(public["position_action"], "HOLD")
            self.assertEqual(public["position"]["status"], "OPEN")
            self.assertEqual(public["selection"]["symbol"], "CRM")
            self.assertEqual(public["date"], "2026-09-02")

            index = json.loads((history / "index.json").read_text())
            self.assertEqual(index["schema_version"], "us-daily-stock-history-index-v3")
            self.assertEqual(index["open_trades"], 1)
            crm = index["trades"][0]
            self.assertEqual(crm["symbol"], "CRM")
            self.assertEqual(crm["valid_until"], "2026-09-04")
            self.assertEqual(crm["outcome"]["status"], "OPEN")
            self.assertTrue(crm["outcome"]["activated"])

    def test_verify_rejects_dangling_open_position(self):
        now = datetime(2026, 9, 2, 5, 0, tzinfo=NY)
        cfg = {"non_session_dates": []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "history"
            book_path = root / "book.json"
            public_path = root / "public.json"
            canonical = trade_payload("2026-08-31")
            write(history / "2026-08-31.json", canonical)
            book = lifecycle.open_from_payload(lifecycle.empty_book(now), canonical)
            book["open_position"]["valid_until"] = "2026-09-04"
            write(book_path, book)
            write(public_path, canonical)
            with patch.object(integrity.us, "load_config", return_value=cfg), \
                 patch.object(integrity.us, "is_session_day", side_effect=lambda d, _cfg: d.weekday() < 5):
                with self.assertRaises(integrity.us.PublicationError):
                    integrity.verify(now=now, book_path=book_path, history_dir=history, public_path=public_path)


if __name__ == "__main__":
    unittest.main()
