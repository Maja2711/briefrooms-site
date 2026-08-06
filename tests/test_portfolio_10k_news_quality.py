from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

sys.modules.setdefault("yfinance", types.SimpleNamespace(Ticker=None))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "portfolio_10k_news_quality", ROOT / "scripts" / "portfolio_10k_news_quality.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

NOW = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)


def item(
    title,
    *,
    source="Reuters",
    link="https://example.com/story",
    published="Thu, 06 Aug 2026 08:00:00 GMT",
    risk=None,
):
    return {
        "title": title,
        "source": source,
        "link": link,
        "published": published,
        "risk_keywords": risk or [],
        "positive_keywords": [],
    }


def test_deduplicates_syndicated_story_by_event_similarity():
    items = [
        item("Google urges EU court to scrap antitrust fine - Reuters", risk=["antitrust"]),
        item(
            "Google urges EU court to scrap its antitrust fine - Yahoo Finance",
            source="Yahoo Finance",
            risk=["antitrust"],
        ),
    ]
    cleaned = MODULE.clean_news("googl", items, now=NOW)
    assert len(cleaned) == 1


def test_removes_generic_unrelated_etf_story():
    items = [
        item(
            "Vanguard Small-Cap Value ETF Outshines State Street on Fees",
            source="AOL.com",
        )
    ]
    assert MODULE.clean_news("zprv", items, now=NOW) == []


def test_removes_buy_sell_opinion_even_when_entity_matches():
    items = [
        item(
            "After Earnings, Is Alphabet Stock a Buy, a Sell, or Fairly Valued?",
            source="Morningstar",
        )
    ]
    assert MODULE.clean_news("googl", items, now=NOW) == []


def test_ranks_confirmed_regulatory_event_above_context():
    items = [
        item("Alphabet expands a Google Cloud partnership", source="Business Insider"),
        item(
            "Google hit with EU antitrust fine after regulator ruling",
            source="Reuters",
            risk=["antitrust"],
        ),
    ]
    cleaned = MODULE.clean_news("googl", items, now=NOW)
    assert cleaned[0]["event_type"] == "REGULATORY"
    assert cleaned[0]["material_candidate"] is True
    assert cleaned[0]["quality_score"] >= 70


def test_one_negative_material_story_does_not_force_urgent_review():
    position = {
        "id": "googl",
        "market_symbol": "GOOGL",
        "currency": "USD",
        "current_price": 350.0,
        "ma50": 340.0,
        "ma200": 300.0,
        "return_6m": 0.10,
        "drawdown_52w": -0.10,
        "volatility_20d": 0.30,
        "current_weight": 0.15,
        "target_weight": 0.15,
        "recent_news": [
            item(
                "Google faces a new antitrust investigation - Reuters",
                risk=["antitrust"],
            )
        ],
    }
    MODULE.refresh_position(position)
    assert position["review_flag"] == "HOLD"
    assert "material_news_headline_requires_review" in position["risk_signals"]
