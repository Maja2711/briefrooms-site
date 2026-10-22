from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from scripts import daily_eurusd_bollinger_d1 as d1

START = datetime(2026, 7, 1, tzinfo=timezone.utc)

def bars(prices):
    return [SimpleNamespace(timestamp=START+timedelta(days=i), close=p) for i,p in enumerate(prices)]

def test_d1_uptrend_and_middle_band():
    data = bars([1.10+i*0.001 for i in range(40)])
    result = d1.evaluate(data, START+timedelta(days=40))
    assert result["available"] and result["trend"] == "UP"
    assert result["close"] > result["middle"]
    assert result["admission_veto_allowed"] is False
    assert result["direction_mutation_allowed"] is False

def test_d1_downtrend():
    data = bars([1.20-i*0.001 for i in range(40)])
    assert d1.evaluate(data, START+timedelta(days=40))["trend"] == "DOWN"

def test_d1_ignores_incomplete_current_candle():
    data = bars([1.10+i*0.001 for i in range(40)])
    data.append(SimpleNamespace(timestamp=START+timedelta(days=40), close=9.0))
    result = d1.evaluate(data, START+timedelta(days=40, hours=12))
    assert result["close"] == data[-2].close

def test_d1_missing_data_is_non_authoritative():
    result = d1.evaluate(bars([1.1]*5), START+timedelta(days=5))
    assert result["available"] is False
    assert result["role"] == "AUXILIARY_TECHNICAL"
