from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

sys.modules.setdefault("yfinance", types.SimpleNamespace(Ticker=None))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("portfolio_10k_news_quality", ROOT / "scripts" / "portfolio_10k_news_quality.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def item(title, risk=None):
    return {"title": title, "risk_keywords": risk or [], "positive_keywords": []}


def test_deduplicates_syndicated_story_by_base_title():
    items = [
        item("Google urges EU court to scrap antitrust fine - Reuters", ["antitrust"]),
        item("Google urges EU court to scrap antitrust fine - Yahoo Finance", ["antitrust"]),
    ]
    cleaned = MODULE.clean_news("googl", items)
    assert len(cleaned) == 1


def test_removes_generic_unrelated_etf_story():
    items = [item("ETFs Investing in Taiwan Secom Co., Ltd. Stocks - TradingView")]
    assert MODULE.clean_news("fwia", items) == []


def test_one_material_story_does_not_force_urgent_thesis_review():
    position = {
        "id": "googl", "market_symbol": "GOOGL", "currency": "USD",
        "current_price": 350.0, "ma50": 340.0, "ma200": 300.0,
        "return_6m": 0.10, "drawdown_52w": -0.10, "volatility_20d": 0.30,
        "current_weight": 0.15, "target_weight": 0.15,
        "recent_news": [item("Google faces antitrust hearing - Reuters", ["antitrust"])],
    }
    MODULE.refresh_position(position)
    assert position["review_flag"] == "HOLD"
    assert "material_news_headline_requires_review" in position["risk_signals"]
