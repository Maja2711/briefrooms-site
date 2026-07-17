from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pandas as pd

sys.modules.setdefault("yfinance", types.SimpleNamespace(Ticker=None))

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("portfolio_10k_weekly", ROOT / "scripts" / "portfolio_10k_weekly.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_target_weights_sum_to_one():
    data = json.loads((ROOT / "data" / "investments" / "portfolio_10k.json").read_text(encoding="utf-8"))
    assert abs(sum(item["target_weight"] for item in data["positions"]) - 1.0) < 1e-9
    MODULE.validate_config(data)


def test_review_flag_is_not_an_automatic_trade_signal():
    position = {"target_weight": 0.10}
    assert MODULE.review_flag(position, 55, 0.10, 0) == "HOLD"
    assert MODULE.review_flag(position, 25, 0.10, 0) == "THESIS_REVIEW"
    assert MODULE.review_flag(position, 80, 0.04, 0) == "ADD_REVIEW"
    assert MODULE.review_flag(position, 60, 0.17, 0) == "TRIM_REVIEW"


def test_technical_score_rewards_confirmed_uptrend():
    idx = pd.date_range("2025-01-01", periods=220, freq="B")
    history = pd.DataFrame({"Close": range(100, 320)}, index=idx)
    record = MODULE.MarketRecord(
        symbol="TEST",
        price=319.0,
        market_date="2026-01-01",
        currency="USD",
        ma50=294.5,
        ma200=219.5,
        return_6m=0.30,
        drawdown_52w=0.0,
        volatility_20d=0.18,
        history=history,
        next_earnings_date=None,
    )
    score, positives, risks = MODULE.technical_score(record, [])
    assert score >= 80
    assert "price_above_ma200" in positives
    assert not risks


def test_snapshot_upsert_preserves_other_dates():
    data = {"positions": [], "snapshots": [{"date": "2026-07-10", "total_value_pln": 9900}]}
    MODULE.upsert_snapshot(data, {"total_value_pln": 10100}, "2026-07-17")
    assert [item["date"] for item in data["snapshots"]] == ["2026-07-10", "2026-07-17"]
    MODULE.upsert_snapshot(data, {"total_value_pln": 10200}, "2026-07-17")
    assert len(data["snapshots"]) == 2
    assert data["snapshots"][-1]["total_value_pln"] == 10200
