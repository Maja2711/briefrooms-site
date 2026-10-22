from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from scripts import daily_eurusd_bollinger_h1 as bb

START = datetime(2026, 10, 19, tzinfo=timezone.utc)

def bars(prices):
    result = []
    for i, p in enumerate(prices):
        for minute in (0, 30):
            result.append(SimpleNamespace(
                timestamp=START + timedelta(hours=i, minutes=minute),
                open=p, high=p+0.0001, low=p-0.0001, close=p,
            ))
    return result

def test_belief_flat_cannot_become_trade():
    data = bars([1.10+i*0.0001 for i in range(40)])
    assert not bb.evaluate(data, "FLAT", START+timedelta(hours=40))["allowed"]

def test_confirmed_uptrend_allows_long_but_not_short():
    data = bars([1.10+i*0.0001 for i in range(40)])
    now = START+timedelta(hours=40)
    assert bb.evaluate(data, "LONG", now)["allowed"]
    assert not bb.evaluate(data, "SHORT", now)["allowed"]

def test_downtrend_allows_short_but_not_long():
    data = bars([1.15-i*0.0001 for i in range(40)])
    now = START+timedelta(hours=40)
    assert bb.evaluate(data, "SHORT", now)["allowed"]
    assert not bb.evaluate(data, "LONG", now)["allowed"]

def test_missing_history_fails_closed():
    assert bb.evaluate(bars([1.1]*12), "LONG", START+timedelta(hours=12))["reason"] == "insufficient_closed_h1_bars"

def test_incomplete_hour_never_used():
    data = bars([1.10+i*0.0001 for i in range(40)])
    now = START+timedelta(hours=39, minutes=30)
    assert bb.evaluate(data, "LONG", now)["last_closed_h1"] == (START+timedelta(hours=39)).isoformat()

def test_stale_data_fails_closed():
    data = bars([1.10+i*0.0001 for i in range(40)])
    assert bb.evaluate(data, "LONG", START+timedelta(hours=43))["reason"] == "stale_h1_bollinger"
