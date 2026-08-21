from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.belief_market_data_adapter import Bar
from scripts import daily_eurusd_experiment_v12 as v12
from scripts import daily_eurusd_experiment_v13 as abc
from scripts.daily_eurusd_abc_settler import settle_state

UTC = timezone.utc
OBSERVED = datetime(2026, 8, 20, 18, 30, tzinfo=UTC)


def series(end: datetime, start: float, drift: float, n: int, step: timedelta) -> list[Bar]:
    price = start
    first = end - step * (n - 1)
    rows = []
    for i in range(n):
        price *= 1 + drift
        rows.append(Bar(first + step * i, price, open=price * .9995, high=price * 1.0015, low=price * .9985, volume=1000+i))
    return rows


def markets():
    return (
        series(OBSERVED, 1.15, .00008, 90, timedelta(minutes=30)),
        series(OBSERVED - timedelta(minutes=30), 1.12, .00005, 260, timedelta(hours=1)),
        series(OBSERVED.replace(hour=0, minute=0), 1.05, .00035, 260, timedelta(days=1)),
    )


def beliefs():
    stamp = (OBSERVED - timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
    return {"beliefs": [
        {"belief_id":"eurusd.trend.bullish","probability":.66,"confidence":.60,"last_updated":stamp},
        {"belief_id":"eurusd.usd_environment.supportive","probability":.61,"confidence":.55,"last_updated":stamp},
        {"belief_id":"eurusd.us_rates_pressure.supportive","probability":.56,"confidence":.50,"last_updated":stamp},
    ]}


def build_state():
    rows30, h1, d1 = markets()
    capture = abc.build_capture(rows30, beliefs(), hourly_rows=h1, daily_rows=d1, captured_at=OBSERVED + timedelta(minutes=1))
    return abc.append_capture(abc.empty_state(OBSERVED), capture), rows30


class FakeClient:
    def __init__(self, rows30, rows1):
        self.rows30, self.rows1 = rows30, rows1

    def bars(self, symbol, range_="10d", interval="30m"):
        if symbol != "EURUSD=X":
            raise AssertionError(symbol)
        if (range_, interval) == ("10d", "30m"):
            return list(self.rows30)
        if (range_, interval) == ("5d", "1m"):
            return list(self.rows1)
        raise AssertionError((range_, interval))


class PR24IsolationAndSettlerTests(unittest.TestCase):
    def test_v13_build_does_not_mutate_v12_engine_version(self):
        self.assertEqual(v12.ENGINE_VERSION, "eurusd-daily-abc-v1.2.0")
        self.assertEqual(abc.base.ENGINE_VERSION, "eurusd-daily-abc-v1.2.0")
        state, _ = build_state()
        self.assertEqual(state["captures"][-1]["engine_version"], "eurusd-daily-abc-v1.3.0")
        self.assertEqual(abc.base.ENGINE_VERSION, "eurusd-daily-abc-v1.2.0")

    def test_open_excursion_does_not_create_checkpoint(self):
        state, rows30 = build_state()
        capture = state["captures"][0]
        tracked = [k for k,v in capture["trade_plan"]["arms"].items() if v["status"] == "TRACKED"]
        self.assertTrue(tracked)
        entry = float(capture["trade_plan"]["arms"][tracked[0]]["entry_price"])
        rows1 = [Bar(OBSERVED + timedelta(minutes=i), entry, open=entry, high=entry*1.0002, low=entry*.9998, volume=1) for i in range(2,6)]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            sp=root/"EURUSD_DAILY_ABC_STATE.json"; rp=root/"EURUSD_DAILY_ABC_REPORT.json"
            sp.write_text(json.dumps(state, indent=2)+"\n"); rp.write_text(json.dumps(abc.build_report(state), indent=2)+"\n")
            before_s, before_r = sp.read_bytes(), rp.read_bytes()
            changed, delta = settle_state(root, client=FakeClient(rows30, rows1), as_of=OBSERVED+timedelta(minutes=6))
            self.assertFalse(changed); self.assertEqual(delta, 0)
            self.assertEqual(sp.read_bytes(), before_s); self.assertEqual(rp.read_bytes(), before_r)

    def test_terminal_tp_is_persisted_with_final_excursions(self):
        state, rows30 = build_state()
        capture = state["captures"][0]
        tracked = [k for k,v in capture["trade_plan"]["arms"].items() if v["status"] == "TRACKED"]
        self.assertTrue(tracked)
        chosen=tracked[0]; p=capture["trade_plan"]["arms"][chosen]
        entry=float(p["entry_price"]); target=float(p["target_price"]); stop=float(p["stop_price"])
        if p["direction"] == "LONG":
            hit=Bar(OBSERVED+timedelta(minutes=2), target, open=entry, high=target*1.0001, low=(entry+stop)/2, volume=1)
        else:
            hit=Bar(OBSERVED+timedelta(minutes=2), target, open=entry, high=(entry+stop)/2, low=target*.9999, volume=1)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"EURUSD_DAILY_ABC_STATE.json").write_text(json.dumps(state, indent=2)+"\n")
            (root/"EURUSD_DAILY_ABC_REPORT.json").write_text(json.dumps(abc.build_report(state), indent=2)+"\n")
            changed, delta = settle_state(root, client=FakeClient(rows30,[hit]), as_of=OBSERVED+timedelta(minutes=3))
            self.assertTrue(changed); self.assertEqual(delta,0)
            persisted=json.loads((root/"EURUSD_DAILY_ABC_STATE.json").read_text())
            row=persisted["captures"][0]["trade_path"]["arms"][chosen]
            self.assertEqual(row["status"],"CLOSED"); self.assertEqual(row["exit_reason"],"TAKE_PROFIT")
            self.assertIsNotNone(row["mfe_bps"]); self.assertIsNotNone(row["mae_bps"]); self.assertGreater(row["realized_bps"],0)


if __name__ == "__main__":
    unittest.main()
