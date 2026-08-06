from __future__ import annotations

import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

sys.modules.setdefault("yfinance", types.SimpleNamespace(Ticker=None))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = spec_from_file_location(
    "portfolio_10k_material_reports_enrichment",
    ROOT / "scripts" / "portfolio_10k_material_reports_enrichment.py",
)
module = module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def position():
    return {
        "id": "novo",
        "broker_symbol": "NOVOB.DK",
        "currency": "DKK",
        "status": "active",
        "current_price": 306.5,
        "current_price_updated_at": "2026-08-06T08:00:00Z",
        "current_price_source": "Yahoo Finance:NOVO-B.CO",
        "quantity": 2.5,
        "entry_price": 331.2,
        "pnl_percent": -0.08,
        "risk_signals": [
            "negative_six_month_momentum",
            "drawdown_above_twenty_percent",
        ],
    }


def material_item(title, *, source="Reuters", risk=None):
    return {
        "title": title,
        "source": source,
        "published": "Thu, 06 Aug 2026 08:00:00 GMT",
        "link": "https://example.com/verified",
        "risk_keywords": risk or [],
        "positive_keywords": [],
    }


def test_source_backed_trial_headline_becomes_material_report():
    report = module.headline_report(
        position(),
        material_item(
            "Novo Nordisk phase 3 clinical trial failed to meet its endpoint - Reuters",
            risk=["failed"],
        ),
    )
    assert report is not None
    assert report["type"] == "OPERATIONS"
    assert report["impact"] == "NEGATIVE"
    assert report["model_action"] == "THESIS_REVIEW"
    assert report["methodology_version"] == "analysis-news-v2"


def test_buy_sell_opinion_does_not_become_material_report():
    report = module.headline_report(
        position(),
        material_item(
            "After Earnings, Is Novo Nordisk Stock a Buy or a Sell?",
            source="Morningstar",
        ),
    )
    assert report is None


def test_unverified_headline_is_rejected():
    assert (
        module.headline_report(
            position(),
            {
                "title": "Novo Nordisk trial failed",
                "source": "",
                "published": "bad",
                "link": "http://example.com",
            },
        )
        is None
    )


def test_verified_manual_report_is_merged_once():
    verified = {
        "reports": [
            {
                "id": "verified-1",
                "position_id": "novo",
                "published_at": "2026-08-06T08:00:00Z",
                "sources": [
                    {"label": "Reuters", "url": "https://example.com/report"}
                ],
            }
        ]
    }
    result = module.enrich({"positions": []}, {"reports": []}, verified)
    assert [row["id"] for row in result["reports"]] == ["verified-1"]


def test_old_automatic_headline_report_is_removed():
    payload = {
        "reports": [
            {
                "id": "old-auto",
                "position_id": "novo",
                "published_at": "2026-07-30T08:00:00Z",
                "category": "VERIFIED_SOURCE_HEADLINE",
                "sources": [
                    {"label": "Morningstar", "url": "https://example.com/opinion"}
                ],
            }
        ]
    }
    result = module.enrich({"positions": []}, payload, {"reports": []})
    assert result["reports"] == []
