from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.belief_market_data_adapter import Bar
from scripts import daily_eurusd_experiment_v12 as abc
from scripts.daily_eurusd_abc_settler import settle_state

UTC = timezone.utc
OBSERVED = datetime(2026, 8, 20, 18, 30, tzinfo=UTC)


def series_ending(end: datetime, *, start: float, drift: float, n: int, step: timedelta) -> list[Bar]:
    price = start
    rows: list[Bar] = []
    first = end - step * (n - 1)
    for i in range(n):
        price *= 1.0 + drift
        rows.append(Bar(
            timestamp=first + step * i,
            open=price * 0.9995,
            high=price * 1.0015,
            low=price * 0.9985,
            close=price,
            volume=1000 + i,
        ))
    return rows


def market_sets() -> tuple[list[Bar], list[Bar], list[Bar]]:
    rows_30m = series_ending(OBSERVED, start=1.15, drift=0.00008, n=90, step=timedelta(minutes=30))
    h1 = series_ending(OBSERVED - timedelta(minutes=30), start=1.12, drift=0.00005, n=260, step=timedelta(hours=1))
    d1 = series_ending(OBSERVED.replace(hour=0, minute=0), start=1.05, drift=0.00035, n=260, step=timedelta(days=1))
    return rows_30m, h1, d1


def belief_payload() -> dict:
    updated = OBSERVED - timedelta(minutes=15)
    values = {
        "eurusd.trend.bullish": (0.66, 0.60),
        "eurusd.usd_environment.supportive": (0.61, 0.55),
        "eurusd.us_rates_pressure.supportive": (0.56, 0.50),
    }
    return {
        "beliefs": [
            {
                "belief_id": belief_id,
                "probability": probability,
                "confidence": confidence,
                "last_updated": updated.isoformat().replace("+00:00", "Z"),
            }
            for belief_id, (probability, confidence) in values.items()
        ]
    }


class FakeClient:
    def __init__(self, rows: list[Bar]):
        self.rows = rows

    def bars(self, symbol: str, period: str, interval: str) -> list[Bar]:
        self.assert_contract(symbol, period, interval)
        return list(self.rows)

    @staticmethod
    def assert_contract(symbol: str, period: str, interval: str) -> None:
        if (symbol, period, interval) != ("EURUSD=X", "10d", "30m"):
            raise AssertionError((symbol, period, interval))


def write_private_state(root: Path) -> tuple[dict, list[Bar]]:
    rows_30m, h1, d1 = market_sets()
    capture = abc.build_capture(
        rows_30m,
        belief_payload(),
        hourly_rows=h1,
        daily_rows=d1,
        captured_at=OBSERVED + timedelta(minutes=1),
    )
    state = abc.append_capture(abc.empty_state(OBSERVED), capture)
    report = abc.build_report(state)
    (root / "EURUSD_DAILY_ABC_STATE.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    (root / "EURUSD_DAILY_ABC_REPORT.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return capture, rows_30m


class DailyEURUSDABCSettlerTests(unittest.TestCase):
    def test_settles_matured_outcome_without_creating_or_rewriting_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture, rows = write_private_state(root)
            price = float(rows[-1].close) * 1.0005
            future = list(rows) + [Bar(
                timestamp=OBSERVED + timedelta(minutes=30),
                open=price,
                high=price * 1.0002,
                low=price * 0.9998,
                close=price,
                volume=3000,
            )]

            changed, delta = settle_state(root, client=FakeClient(future))
            self.assertTrue(changed)
            self.assertEqual(delta, 1)

            state = json.loads((root / "EURUSD_DAILY_ABC_STATE.json").read_text(encoding="utf-8"))
            self.assertEqual(len(state["captures"]), 1)
            self.assertEqual(state["captures"][0]["capture_id"], capture["capture_id"])
            self.assertEqual(state["captures"][0]["decision_sha256"], capture["decision_sha256"])
            self.assertIsNotNone(state["captures"][0]["horizons"]["30m"]["outcome"])
            self.assertIsNone(state["captures"][0]["horizons"]["60m"]["outcome"])

            report = json.loads((root / "EURUSD_DAILY_ABC_REPORT.json").read_text(encoding="utf-8"))
            self.assertEqual(report["performance"]["A"]["30m"]["matured_captures"], 1)
            abc.validate_files(root)

    def test_no_matured_horizon_leaves_private_files_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, rows = write_private_state(root)
            state_path = root / "EURUSD_DAILY_ABC_STATE.json"
            report_path = root / "EURUSD_DAILY_ABC_REPORT.json"
            before_state = state_path.read_bytes()
            before_report = report_path.read_bytes()

            changed, delta = settle_state(root, client=FakeClient(rows))
            self.assertFalse(changed)
            self.assertEqual(delta, 0)
            self.assertEqual(state_path.read_bytes(), before_state)
            self.assertEqual(report_path.read_bytes(), before_report)


if __name__ == "__main__":
    unittest.main()
