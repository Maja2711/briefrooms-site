from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GENERATOR = load_module("portfolio_10k_material_reports_test", "portfolio_10k_material_reports.py")
VALIDATOR = load_module("validate_portfolio_10k_material_reports_test", "validate_portfolio_10k_material_reports.py")


def position(status="active"):
    payload = {
        "id": "googl", "broker_symbol": "GOOGL.US", "market_symbol": "GOOGL",
        "currency": "USD", "target_weight": 0.5, "status": status,
        "news_query": "Alphabet Google earnings", "report_monitoring": {
            "enabled": True,
            "price_alerts": {"below": None, "above": None, "daily_move_percent": 0.07},
        },
    }
    if status == "active":
        payload.update({
            "quantity": 2.0, "entry_price": 100.0, "entry_fx_to_pln": 4.0,
            "entry_notional_pln": 800.0, "entry_fee_pln": 4.0, "entry_value_pln": 804.0,
        })
    return payload


def portfolio():
    return {
        "portfolio_id": "briefrooms-xtb-10k", "starting_capital_pln": 10000.0,
        "positions": [position(), {
            "id": "fwia", "broker_symbol": "FWIA.DE", "market_symbol": "FWIA.DE",
            "currency": "EUR", "target_weight": 0.5, "status": "pending",
            "report_monitoring": {"enabled": True, "price_alerts": {"daily_move_percent": 0.07}},
        }],
    }


def market(last=107.0):
    history = pd.DataFrame(
        {"Close": [100.0, last]},
        index=pd.to_datetime(["2026-07-21T20:00:00Z", "2026-07-22T20:00:00Z"]),
    )
    return GENERATOR.base.MarketRecord(
        symbol="GOOGL", price=last, market_date="2026-07-22", currency="USD",
        ma50=None, ma200=None, return_6m=None, drawdown_52w=None,
        volatility_20d=None, history=history, next_earnings_date=None,
    )


def valid_report(report_id="googl-2026-07-22-event"):
    return {
        "id": report_id, "position_id": "googl", "symbol": "GOOGL.US",
        "published_at": "2026-07-22T20:15:00Z", "event_date": "2026-07-22",
        "type": "EARNINGS", "category": "RESULTS", "severity": "HIGH",
        "impact": "POSITIVE", "impact_score": 4,
        "title_pl": "Alphabet opublikował wyniki", "title_en": "Alphabet reported results",
        "summary_pl": "Przychody wzrosły.", "summary_en": "Revenue increased.",
        "thesis_effect_pl": None, "thesis_effect_en": None, "model_action": "HOLD",
        "quote": None, "position_snapshot": None,
        "sources": [{"label": "Alphabet IR", "url": "https://abc.xyz/investor/"}],
    }


def payload(reports=None):
    return {
        "schema_version": "1.0.0", "portfolio_id": "briefrooms-xtb-10k",
        "last_updated_at": None, "reports": reports or [],
    }


def test_empty_report_store_is_valid():
    assert VALIDATOR.validate_payload(portfolio(), payload()) == []


def test_active_position_generates_exact_seven_percent_price_alert():
    report = GENERATOR.create_price_alert(position(), market(107.0), 4.1)
    assert report is not None
    assert report["type"] == "PRICE_ALERT"
    assert report["quote"]["kind"] == "CLOSE"


def test_move_below_seven_percent_does_not_generate_report():
    assert GENERATOR.create_price_alert(position(), market(106.999), 4.1) is None


def test_unconfirmed_headline_never_becomes_material_report():
    item = {"title": "Alphabet reports quarterly earnings", "link": "https://example.com/news"}
    assert GENERATOR.create_confirmed_news_report(position(), item, market(), 4.1) is None


def test_snapshot_separates_instrument_and_fx_effect():
    snapshot = GENERATOR.position_snapshot(position(), 107.0, 4.1)
    assert snapshot["instrument_effect_pln"] == 56.0
    assert snapshot["fx_effect_pln"] == 21.4
    assert snapshot["unrealized_pnl_pln"] == 73.4
    assert snapshot["instrument_effect_pln"] + snapshot["fx_effect_pln"] - snapshot["entry_fee_pln"] == pytest.approx(snapshot["unrealized_pnl_pln"])


def test_generator_skips_pending_and_is_idempotent(tmp_path, monkeypatch):
    portfolio_path = tmp_path / "portfolio.json"
    reports_path = tmp_path / "reports.json"
    portfolio_path.write_text(json.dumps(portfolio()), encoding="utf-8")
    reports_path.write_text(json.dumps(payload()), encoding="utf-8")
    calls = []

    def fetcher(current, _cache):
        calls.append(current["id"])
        return market(), 4.1, []

    monkeypatch.setattr(GENERATOR.base, "validate_config", lambda data: None)
    assert GENERATOR.run(portfolio_path, reports_path, fetcher) == 1
    first = json.loads(reports_path.read_text(encoding="utf-8"))
    assert calls == ["googl"]
    assert len(first["reports"]) == 1
    calls.clear()
    assert GENERATOR.run(portfolio_path, reports_path, fetcher) == 0
    second = json.loads(reports_path.read_text(encoding="utf-8"))
    assert calls == ["googl"]
    assert second == first


def test_existing_report_order_is_append_only():
    existing = [valid_report("older"), valid_report("newer")]
    candidate = valid_report("appended")
    candidate["event_date"] = "2026-07-23"
    candidate["published_at"] = "2026-07-23T20:00:00Z"
    candidate["sources"] = [{"label": "New source", "url": "https://example.com/new"}]
    accepted = GENERATOR.deduplicate_new_reports(existing, [candidate])
    store = copy.deepcopy(existing)
    store.extend(accepted)
    assert [item["id"] for item in store] == ["older", "newer", "appended"]


@pytest.mark.parametrize("kind", ["BID", "ASK", "LAST", "CLOSE", "INDICATIVE"])
def test_validator_accepts_all_quote_kinds(kind):
    report = valid_report()
    report["quote"] = {
        "value": 107.0, "currency": "USD", "kind": kind, "market": None,
        "quoted_at": "2026-07-22T20:00:00Z", "source": "verified source",
    }
    assert VALIDATOR.validate_payload(portfolio(), payload([report])) == []


def test_validator_accepts_missing_quote_and_snapshot():
    assert VALIDATOR.validate_payload(portfolio(), payload([valid_report()])) == []


def test_validator_accepts_consistent_full_snapshot():
    report = valid_report()
    report["position_snapshot"] = GENERATOR.position_snapshot(position(), 107.0, 4.1)
    assert VALIDATOR.validate_payload(portfolio(), payload([report])) == []


def test_validator_rejects_invalid_enum():
    report = valid_report()
    report["severity"] = "EXTREME"
    assert any("severity" in error for error in VALIDATOR.validate_payload(portfolio(), payload([report])))


def test_validator_rejects_duplicate_id():
    report = valid_report()
    assert any("duplicate id" in error for error in VALIDATOR.validate_payload(portfolio(), payload([report, copy.deepcopy(report)])))


def test_validator_rejects_unknown_position():
    report = valid_report()
    report["position_id"] = "kspi"
    assert any("position_id" in error for error in VALIDATOR.validate_payload(portfolio(), payload([report])))


def test_validator_rejects_impact_score_outside_range():
    report = valid_report()
    report["impact_score"] = 6
    assert any("impact_score" in error for error in VALIDATOR.validate_payload(portfolio(), payload([report])))


def test_validator_rejects_unsafe_source_url():
    report = valid_report()
    report["sources"][0]["url"] = "javascript:alert(1)"
    assert any("sources[0].url" in error for error in VALIDATOR.validate_payload(portfolio(), payload([report])))


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), "107.0"])
def test_validator_rejects_non_finite_or_textual_snapshot_numbers(bad_value):
    report = valid_report()
    report["position_snapshot"] = GENERATOR.position_snapshot(position(), 107.0, 4.1)
    report["position_snapshot"]["market_value_local"] = bad_value
    errors = VALIDATOR.validate_payload(portfolio(), payload([report]))
    assert any("position_snapshot.market_value_local" in error for error in errors)
