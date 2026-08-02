from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location("enrichment", ROOT / "scripts/portfolio_10k_material_reports_enrichment.py")
module = module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def position():
    return {
        "id": "novo", "broker_symbol": "NOVOB.DK", "currency": "DKK",
        "current_price": 306.5, "current_price_updated_at": "2026-07-31T12:00:00Z",
        "quantity": 2.5, "entry_price": 331.2, "pnl_percent": -0.08,
        "risk_signals": ["negative_six_month_momentum", "drawdown_above_twenty_percent"],
    }


def test_source_backed_trial_headline_becomes_material_report():
    report = module.headline_report(position(), {
        "title": "Novo phase 3 clinical trial failed to meet its endpoint - Reuters",
        "source": "Reuters", "published": "Fri, 31 Jul 2026 12:00:00 GMT",
        "link": "https://example.com/verified", "risk_keywords": ["failed"],
    })
    assert report is not None
    assert report["type"] == "OPERATIONS"
    assert report["impact"] == "NEGATIVE"
    assert report["model_action"] == "THESIS_REVIEW"


def test_unverified_headline_is_rejected():
    assert module.headline_report(position(), {
        "title": "Trial failed", "source": "", "published": "bad", "link": "http://example.com"
    }) is None


def test_verified_manual_report_is_merged_once():
    verified = {"reports": [{
        "id": "verified-1", "position_id": "novo", "published_at": "2026-07-31T12:00:00Z",
        "sources": [{"label": "Reuters", "url": "https://example.com/report"}],
    }]}
    result = module.enrich({"positions": []}, {"reports": []}, verified)
    assert [row["id"] for row in result["reports"]] == ["verified-1"]
