from __future__ import annotations

import copy
import io
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_portfolio_data
from brace_portfolio_backtest import run_walk_forward
from brace_portfolio_config import load_config
from brace_portfolio_data import (
    assert_baseline_unchanged,
    baseline_invariants,
    canonical_sha256,
    data_freshness_report,
    YFinanceProvider,
)
from brace_portfolio_decision import build_pending_decisions
from brace_portfolio_execution import (
    execute_order,
    initialize_paper_portfolio,
    prepare_orders,
)
from brace_portfolio_engine import _merge_market_refresh, _record_baseline_validation
from brace_portfolio_optimizer import optimize
from brace_portfolio_promotion_controller import _code_sha, evaluate_and_apply
from brace_portfolio_publish import build_public_snapshot


def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def market_item(rows: int, marker: str) -> dict:
    return {
        "history": [
            {"date": f"2026-01-{(index % 28) + 1:02d}", "close": 100 + index}
            for index in range(rows)
        ],
        "fundamentals": {"marker": marker},
        "errors": [],
    }


@pytest.fixture()
def config():
    return load_config(ROOT / "data" / "portfolio10k" / "config.json")[0]


def passing_validation() -> dict:
    return {
        "oos_return_after_costs": 0.12,
        "oos_excess_vs_baseline": 0.03,
        "no_lookahead_audit": True,
        "costs_and_fx_included": True,
        "regime_stability": True,
        "observations": 500,
        "parameter_neighborhood_stable": True,
        "not_single_instrument_dependent": True,
        "reproducible_run": True,
        "full_manifest": True,
        "maximum_drawdown": -0.16,
        "drawdown_disadvantage": 0.0,
        "concentration_within_limits": True,
        "no_leverage": True,
        "no_short_sales": True,
        "no_cfds": True,
        "annual_turnover": 0.8,
        "expected_shortfall": -0.12,
        "downside_volatility": 0.18,
        "validation_window": {"out_of_sample": {"from": "2024-01-01", "to": "2026-01-01"}},
    }


def passing_shadow() -> dict:
    return {
        "decisions": 24,
        "completed_trades": 9,
        "shadow_return": 0.08,
        "baseline_return": 0.05,
        "shadow_risk_adjusted_return": 0.9,
        "baseline_risk_adjusted_return": 0.6,
        "shadow_max_drawdown": -0.12,
        "shadow_turnover": 0.7,
        "excess_return_ci_low": 0.002,
    }


def passing_operational() -> dict:
    return {
        "stale_data": False,
        "consecutive_workflow_failures": 0,
        "price_fx_inconsistent": False,
        "history_integrity_failed": False,
        "unexplained_parameter_change": False,
        "missing_rationale": False,
        "risk_data_missing": False,
        "live_validation_divergence": False,
        "critical_data_errors": 0,
        "decisions_reproducible": True,
        "entry_history_unchanged": True,
        "timestamps_complete": True,
        "workflow_stable": True,
        "public_internal_consistent": True,
        "integrity_tests_pass": True,
    }


def passing_probation() -> dict:
    return {
        "max_rotations_per_day": 1,
        "max_position_changes_per_week": 2,
        "maximum_weekly_turnover": 0.10,
        "maximum_new_position_weight": 0.08,
        "minimum_confidence": 0.75,
        "full_portfolio_replacement": False,
        "transaction_cost_buffer_applied": True,
        "cooldown_applied": True,
        "material_advantage_required": True,
        "fallback_trigger": False,
    }


def test_registry_preserves_authorised_paper_control_while_fail_safe_is_active():
    registry = load("data/portfolio10k/methodology_registry.json")
    assert registry["controller_state"] == "FALLBACK_BASELINE"
    assert registry["champion_methodology_id"] == "portfolio-10k-baseline"
    methods = {item["methodology_id"]: item for item in registry["methodologies"]}
    assert methods["portfolio-10k-baseline"]["status"] == "ACTIVE_BASELINE"
    assert methods["brace-portfolio-engine"]["status"] == "FALLBACK_BASELINE"
    authorisation = methods["brace-portfolio-engine"]["validation_results"]["user_authorized_paper_control"]
    assert authorisation["paper_only"] is True
    assert authorisation["remaining_automatic_promotion_gates_preserved"] is True
    assert methods["brace-portfolio-engine"]["parameters"]["real_broker_access"] is False
    for item in methods.values():
        for key in (
            "methodology_id",
            "version",
            "status",
            "activated_at",
            "retired_at",
            "description",
            "parameters",
            "benchmark",
            "validation_results",
        ):
            assert key in item


def test_audit_code_sha_resolves_to_a_real_commit():
    value = _code_sha()
    assert len(value) == 40
    int(value, 16)


def test_baseline_entries_and_history_are_immutable():
    baseline = load("data/investments/portfolio_10k.json")
    changed = copy.deepcopy(baseline)
    changed["positions"][0]["entry_price"] += 1
    with pytest.raises(ValueError):
        assert_baseline_unchanged(baseline, changed)
    changed = copy.deepcopy(baseline)
    changed["positions"][0]["current_price"] += 1
    assert_baseline_unchanged(baseline, changed)
    assert baseline_invariants(baseline)["positions"][0]["entry_price"] > 0


def test_registry_records_separate_source_and_immutable_history_hashes():
    baseline = load("data/investments/portfolio_10k.json")
    registry = load("data/portfolio10k/methodology_registry.json")
    _record_baseline_validation(
        registry,
        baseline,
        datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    item = next(
        row
        for row in registry["methodologies"]
        if row["methodology_id"] == "portfolio-10k-baseline"
    )
    results = item["validation_results"]

    assert len(results["entry_history_sha256"]) == 64
    assert len(results["source_snapshot_sha256"]) == 64
    changed = copy.deepcopy(baseline)
    changed["positions"][0]["current_price"] += 1
    assert results["entry_history_sha256"] == canonical_sha256(
        baseline_invariants(changed)
    )


def test_closed_market_quote_is_not_falsely_stale(config):
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    portfolio = {
        "benchmark": {},
        "positions": [
            {
                "id": "us",
                "currency": "USD",
                "current_price": 100,
                "current_fx_to_pln": 3.8,
                "current_price_updated_at": (now - timedelta(hours=16)).isoformat(),
                "current_fx_updated_at": (now - timedelta(hours=1)).isoformat(),
                "market_status": "closed",
            }
        ],
    }
    report = data_freshness_report(portfolio, config, now, "monitor")
    assert report["safe_mode"] is True  # Missing benchmark remains a hard error.
    assert "PRICE_STALE" not in report["instruments"][0]["reasons"]


def test_optimizer_enforces_caps_no_short_and_no_leverage(config):
    analyses = []
    for index, instrument_id in enumerate(("a", "b", "c", "d", "e", "f")):
        analyses.append(
            {
                "instrument_id": instrument_id,
                "asset_type": "STOCK",
                "sector": "Tech" if index < 2 else f"S{index}",
                "currency": "USD" if index < 3 else "EUR",
                "region": "US" if index < 3 else "Europe",
                "expected_return_base": 0.11 - index * 0.005,
                "expected_drawdown": 0.18,
                "final_score": 75 - index,
                "risk": {"volatility": 0.2 + index * 0.01},
            }
        )
    result = optimize({"a": 1.0}, analyses, config)
    weights = result["target_weights"]
    assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)
    assert all(value >= 0 for value in weights.values())
    assert all(value <= config.max_single_stock_weight + 1e-9 for key, value in weights.items() if key != "CASH")
    assert result["no_leverage"]
    assert result["no_short_positions"]


def test_optimizer_falls_back_to_cash_when_no_instrument_has_usable_data(config):
    result = optimize({"missing-instrument": 1.0}, [], config)
    selected = next(
        item for item in result["comparisons"] if item["name"] == result["selected"]
    )

    assert result["target_weights"] == {"CASH": 1.0}
    assert selected["metrics"]["turnover"] == pytest.approx(0.5)


def test_market_refresh_preserves_last_good_history_when_provider_is_empty():
    cached = {
        "generated_at": "2026-07-30T00:00:00+00:00",
        "instruments": {"fwia": market_item(120, "cached")},
    }
    fresh = {
        "generated_at": "2026-07-31T00:00:00+00:00",
        "source_metadata": {"provider": "EmptyProvider"},
        "instruments": {"fwia": market_item(0, "fresh")},
    }

    merged = _merge_market_refresh(fresh, cached)

    assert len(merged["instruments"]["fwia"]["history"]) == 120
    assert merged["instruments"]["fwia"]["fundamentals"]["marker"] == "cached"
    assert merged["data_freshness"] == "partial_last_good_fallback"
    assert merged["source_metadata"]["preserved_from_last_good_cache"] == 1
    assert "NETWORK_REFRESH_INCOMPLETE_USING_LAST_GOOD_CACHE" in (
        merged["instruments"]["fwia"]["errors"]
    )


def test_market_refresh_rejects_empty_provider_without_last_good_cache():
    fresh = {
        "generated_at": "2026-07-31T00:00:00+00:00",
        "instruments": {"fwia": market_item(0, "fresh")},
    }
    with pytest.raises(ValueError, match="no usable histories"):
        _merge_market_refresh(fresh, {})


def test_lightweight_chart_adapter_parses_adjusted_history(monkeypatch):
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1722297600, 1722384000],
                    "indicators": {
                        "adjclose": [{"adjclose": [101.25, 102.5]}],
                        "quote": [{"close": [100.0, 101.0]}],
                    },
                }
            ]
        }
    }

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(
        brace_portfolio_data,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(json.dumps(payload).encode("utf-8")),
    )

    rows = YFinanceProvider._chart_history("TEST", "10y", "1d")

    assert [row["close"] for row in rows] == [101.25, 102.5]
    assert rows[0]["date"] < rows[1]["date"]


def test_safe_mode_never_generates_rotation(config):
    position = {
        "instrument_id": "old",
        "broker_symbol": "OLD",
        "entry_date": "2025-01-01",
        "current_weight": 0.10,
        "target_weight": 0.10,
        "final_score": 20,
        "risk_adjusted_score": -1,
        "risk_score": 20,
        "confidence_score": 0.9,
    }
    candidate = {
        "instrument_id": "new",
        "broker_symbol": "NEW",
        "asset_type": "STOCK",
        "eligible_for_rotation": True,
        "final_score": 90,
        "risk_adjusted_score": 2,
        "expected_return_base": 0.2,
        "expected_drawdown": 0.1,
        "confidence_score": 0.9,
    }
    pending = build_pending_decisions(
        [position],
        [candidate],
        {"target_weights": {"old": 0.1}, "rules_passed": True},
        config,
        datetime(2026, 7, 29, tzinfo=timezone.utc),
        "3.0.0",
        "2026-07-29T00:00:00+00:00",
        True,
    )
    assert pending["safe_mode"] is True
    assert pending["decisions"][0]["action"] == "NO_ACTION"


def test_deterministic_shadow_to_probation_to_active_paper(config):
    registry = load("data/portfolio10k/methodology_registry.json")
    challenger = next(
        item for item in registry["methodologies"] if item["methodology_id"] == "brace-portfolio-engine"
    )
    challenger["status"] = "SHADOW"
    challenger["validation_results"]["shadow_started_at"] = "2026-01-01T00:00:00+00:00"
    first_at = datetime(2026, 3, 5, tzinfo=timezone.utc)
    promoted, history, record = evaluate_and_apply(
        registry,
        passing_validation(),
        passing_shadow(),
        {},
        passing_operational(),
        {"maximum_drawdown": -0.1, "current_drawdown": -0.03, "annual_turnover": 0.5},
        config,
        first_at,
    )
    challenger = next(
        item for item in promoted["methodologies"] if item["methodology_id"] == "brace-portfolio-engine"
    )
    assert challenger["status"] == "PROBATIONARY_CONTROL"
    assert promoted["controller_state"] == "PROBATIONARY_CONTROL"
    assert record and record["all_conditions_passed"]

    second_at = first_at + timedelta(days=31)
    active, history, record = evaluate_and_apply(
        promoted,
        passing_validation(),
        passing_shadow(),
        passing_probation(),
        passing_operational(),
        {"maximum_drawdown": -0.1, "current_drawdown": -0.03, "annual_turnover": 0.5},
        config,
        second_at,
        history,
    )
    challenger = next(
        item for item in active["methodologies"] if item["methodology_id"] == "brace-portfolio-engine"
    )
    baseline = next(
        item for item in active["methodologies"] if item["methodology_id"] == "portfolio-10k-baseline"
    )
    assert challenger["status"] == "ACTIVE_PAPER_CONTROL"
    assert active["controller_state"] == "ACTIVE_PAPER_CONTROL"
    assert baseline["status"] == "FALLBACK_BASELINE"
    assert record and record["previous_status"] == "PROBATIONARY_CONTROL"
    assert len(history["records"]) == 2


def test_active_paper_automatically_falls_back_on_stale_data(config):
    registry = load("data/portfolio10k/methodology_registry.json")
    registry["controller_state"] = "ACTIVE_PAPER_CONTROL"
    registry["champion_methodology_id"] = "brace-portfolio-engine"
    for item in registry["methodologies"]:
        if item["methodology_id"] == "brace-portfolio-engine":
            item["status"] = "ACTIVE_PAPER_CONTROL"
        else:
            item["status"] = "FALLBACK_BASELINE"
    operational = passing_operational()
    operational["stale_data"] = True
    fallback, history, record = evaluate_and_apply(
        registry,
        passing_validation(),
        passing_shadow(),
        passing_probation(),
        operational,
        {"maximum_drawdown": -0.1, "current_drawdown": -0.03, "annual_turnover": 0.5},
        config,
        datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    assert fallback["controller_state"] == "FALLBACK_BASELINE"
    assert fallback["champion_methodology_id"] == "portfolio-10k-baseline"
    assert record and "STALE_DATA" in record["reason"]
    assert history["records"][-1]["new_status"] == "FALLBACK_BASELINE"


class FixedQuotes:
    def __init__(self, now: datetime, market_open: bool = True):
        self.now = now
        self.market_open = market_open

    def quote(self, market_symbol: str, currency: str) -> dict:
        return {
            "price": 100.0 if market_symbol == "MSFT" else 8.0,
            "fx_to_pln": 3.8 if currency == "USD" else 4.3,
            "completed_at": (self.now - timedelta(minutes=5)).isoformat(),
            "market_open": self.market_open,
        }


def test_paper_execution_uses_post_signal_quote_and_never_mutates_baseline(config):
    now = datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)
    baseline = load("data/investments/portfolio_10k.json")
    baseline_before = copy.deepcopy(baseline)
    paper = initialize_paper_portfolio(baseline, now)
    universe = load("data/portfolio10k/universe.json")
    order = {
        "order_id": "paper-order-test",
        "decision_id": "decision-test",
        "action": "REPLACE",
        "sell_instrument": "fwia",
        "buy_instrument": "msft",
        "target_weight": 0.10,
        "confidence": 0.8,
        "rationale_pl": "Test kontrolowany.",
        "rationale_en": "Controlled test.",
        "signal_at": (now - timedelta(minutes=20)).isoformat(),
        "status": "QUEUED",
    }
    updated, result = execute_order(
        paper,
        order,
        universe,
        FixedQuotes(now),
        config,
        "ACTIVE_PAPER_CONTROL",
        now,
    )
    assert result["status"] == "PAPER_EXECUTED"
    assert result["execution"]["buy_completed_candle_at"] > order["signal_at"]
    assert len(updated["transactions"]) == 2
    assert all(item["paper_only"] for item in updated["transactions"])
    assert not updated["real_broker_connected"]
    assert_baseline_unchanged(baseline_before, baseline)


def test_paper_order_waits_for_market_and_is_idempotently_queued(config):
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    baseline = load("data/investments/portfolio_10k.json")
    paper = initialize_paper_portfolio(baseline, now)
    universe = load("data/portfolio10k/universe.json")
    pending = {
        "methodology_version": "3.0.0",
        "data_freshness": "current",
        "decisions": [
            {
                "decision_id": "d1",
                "action": "REPLACE",
                "instrument": "fwia",
                "replacement_instrument": "msft",
                "generated_at": (now - timedelta(minutes=10)).isoformat(),
                "confidence": 0.8,
            }
        ],
    }
    first = prepare_orders(pending, "ACTIVE_PAPER_CONTROL", now)
    second = prepare_orders(pending, "ACTIVE_PAPER_CONTROL", now, first)
    assert first["orders"][0]["order_id"] == second["orders"][0]["order_id"]
    _, result = execute_order(
        paper,
        first["orders"][0],
        universe,
        FixedQuotes(now, market_open=False),
        config,
        "ACTIVE_PAPER_CONTROL",
        now,
    )
    assert result["status"] == "WAITING_FOR_MARKET"


def test_paper_execution_supports_reduce_exit_and_add_with_cash(config):
    now = datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)
    baseline = load("data/investments/portfolio_10k.json")
    paper = initialize_paper_portfolio(baseline, now)
    universe = load("data/portfolio10k/universe.json")
    reduce_order = {
        "order_id": "reduce-test",
        "decision_id": "reduce-decision",
        "action": "REDUCE",
        "sell_instrument": "fwia",
        "buy_instrument": None,
        "target_weight": 0.10,
        "confidence": 0.8,
        "signal_at": (now - timedelta(minutes=20)).isoformat(),
        "status": "QUEUED",
    }
    reduced, result = execute_order(
        paper,
        reduce_order,
        universe,
        FixedQuotes(now),
        config,
        "ACTIVE_PAPER_CONTROL",
        now,
    )
    assert result["status"] == "PAPER_EXECUTED"
    assert result["execution"]["side"] == "SELL"
    assert reduced["cash_pln"] > 0
    assert len(reduced["transactions"]) == 1

    add_order = {
        "order_id": "add-test",
        "decision_id": "add-decision",
        "action": "ADD",
        "sell_instrument": None,
        "buy_instrument": "msft",
        "target_weight": 0.05,
        "confidence": 0.8,
        "signal_at": (now - timedelta(minutes=20)).isoformat(),
        "status": "QUEUED",
    }
    added, result = execute_order(
        reduced,
        add_order,
        universe,
        FixedQuotes(now),
        config,
        "ACTIVE_PAPER_CONTROL",
        now,
    )
    assert result["status"] == "PAPER_EXECUTED"
    assert result["execution"]["side"] == "BUY"
    assert any(item["id"] == "msft" for item in added["positions"])
    assert len(added["transactions"]) == 2


def synthetic_histories(days: int = 520) -> dict:
    histories = {}
    for index, symbol in enumerate(("a", "b", "c", "d")):
        price = 100.0
        rows = []
        for day in range(days):
            price *= 1.0 + 0.0003 + index * 0.00005 + 0.002 * math.sin(day / (13 + index))
            rows.append({"date": (datetime(2023, 1, 1) + timedelta(days=day)).date().isoformat(), "close_pln": price})
        histories[symbol] = rows
    return histories


def test_walk_forward_is_reproducible_costed_and_delayed():
    histories = synthetic_histories()
    kwargs = {
        "risk_free_rate": 0.02,
        "transaction_cost_bps": 20,
        "fx_cost_bps": 15,
        "generated_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
    }
    first = run_walk_forward(histories, {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}, **kwargs)
    second = run_walk_forward(histories, {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}, **kwargs)
    assert first == second
    assert first["manifest"]["signal_delay_sessions"] == 1
    assert first["costs_and_fx_included"]
    assert first["no_lookahead_audit"]
    assert first["models"]["brace"]["observations"] >= 30


def test_public_snapshot_is_sanitized_and_baseline_selected_initially(config):
    registry = load("data/portfolio10k/methodology_registry.json")
    challenger = next(
        item for item in registry["methodologies"] if item["methodology_id"] == "brace-portfolio-engine"
    )
    challenger["status"] = "SHADOW"
    registry["controller_state"] = "ACTIVE_BASELINE"
    public = build_public_snapshot(
        registry,
        {"expected_annual_return": 0.06, "required_risk_to_target": 0.2},
        {"decisions": []},
        {"statistics": {"calendar_days": 0, "decisions": 0, "completed_trades": 0}},
        {"records": []},
        {"data_freshness": "current", "safe_mode": False},
        config,
        datetime(2026, 7, 29, tzinfo=timezone.utc),
        paper_portfolio_available=True,
    )
    encoded = json.dumps(public)
    assert public["display_status"] == "ACTIVE_BASELINE + BRACE_SHADOW"
    assert public["portfolio_data_path"] == "/data/investments/portfolio_10k.json"
    assert public["source_metadata"]["real_broker_connected"] is False
    assert "api_key" not in encoded.lower()
    assert "secret" not in encoded.lower()


def test_public_pages_share_control_panel_and_guard_paper_data_path():
    for path in (
        ROOT / "pl" / "inwestycje" / "portfel-10k.html",
        ROOT / "en" / "investing" / "portfolio-10k.html",
    ):
        html = path.read_text(encoding="utf-8")
        assert 'id="brace-control-root"' in html
        assert "/scripts/portfolio-10k-control-public.js?v=" in html
    script = (ROOT / "scripts" / "portfolio-10k-public.js").read_text(encoding="utf-8")
    assert "PROBATIONARY_CONTROL" in script
    assert "ACTIVE_PAPER_CONTROL" in script
    assert "requestedPath==='/data/portfolio10k/paper_portfolio.json'" in script


def test_workflows_separate_monitor_learning_and_research():
    monitor = (ROOT / ".github" / "workflows" / "brace-portfolio-monitor.yml").read_text(encoding="utf-8")
    daily = (ROOT / ".github" / "workflows" / "brace-portfolio-daily.yml").read_text(encoding="utf-8")
    research = (ROOT / ".github" / "workflows" / "portfolio-10k-brace.yml").read_text(encoding="utf-8")
    assert "--mode monitor" in monitor
    assert "brace_portfolio_execution.py --network" in monitor
    assert "--mode daily --network" in daily
    assert "--mode weekly --network" in research
    assert "--mode research" in research
    assert 'cron: "15 10 1 * *"' in research
    assert "brace-portfolio-research" in monitor
    assert "brace-portfolio-research" in daily
    assert "brace-portfolio-research" in research
