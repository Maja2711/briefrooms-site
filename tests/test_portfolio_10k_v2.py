from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pandas as pd

sys.modules.setdefault("yfinance", types.SimpleNamespace(Ticker=None))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SPEC = importlib.util.spec_from_file_location("portfolio_10k_weekly_v2", ROOT / "scripts" / "portfolio_10k_weekly_v2.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
BASE = MODULE.base


def record(currency="USD"):
    history = pd.DataFrame({"Close": [100.0], "Dividends": [0.0]}, index=pd.to_datetime(["2026-07-16"]))
    return BASE.MarketRecord(
        symbol="TEST", price=100.0, market_date="2026-07-16", currency=currency,
        ma50=None, ma200=None, return_6m=None, drawdown_52w=0.0,
        volatility_20d=None, history=history, next_earnings_date=None,
    )


def test_initialization_applies_fx_cost_without_exceeding_capital(monkeypatch):
    data = {
        "starting_capital_pln": 10000.0,
        "positions": [{"id": "fwia", "currency": "USD", "target_weight": 1.0}],
        "benchmark": {"currency": "USD"},
    }
    monkeypatch.setattr(BASE, "fx_rate", lambda currency, cache: (4.0, "2026-07-16"))
    MODULE.initialize_portfolio(data, {"fwia": record()}, {})
    position = data["positions"][0]
    assert position["entry_fee_pln"] > 0
    assert position["entry_value_pln"] <= 10000.01
    assert data["base_cash_pln"] >= -0.01
    assert position["quantity"] < 25.0


def test_current_value_includes_dividend_cash_once(monkeypatch):
    data = {
        "starting_capital_pln": 1000.0,
        "base_cash_pln": 0.0,
        "cash_pln": 20.0,
        "positions": [{
            "id": "fwia", "status": "active", "currency": "PLN", "quantity": 10.0,
            "entry_value_pln": 1000.0, "target_weight": 1.0, "news_query": "test"
        }],
        "benchmark": {"currency": "PLN", "units": 10.0},
    }
    monkeypatch.setattr(BASE, "fx_rate", lambda currency, cache: (1.0, "2026-07-16"))
    monkeypatch.setattr(BASE, "dividends_in_pln", lambda position, market, cache: 20.0)
    monkeypatch.setattr(BASE, "rss_news", lambda query, limit=3: [])
    summary = MODULE.update_current_state(data, {"fwia": record("PLN")}, {})
    assert summary["cash_pln"] == 20.0
    assert summary["total_value_pln"] == 1020.0
    assert summary["total_return_pln"] == 20.0
