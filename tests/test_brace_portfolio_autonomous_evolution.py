from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_portfolio_autonomous_evolution as evolution
from brace_portfolio_config import (
    DEFAULT_CONFIG_PATH,
    IMMUTABLE_SAFETY_FIELDS,
    PORTFOLIO_EVOLUTION_FIELDS,
    load_config,
)
from brace_portfolio_counterfactual_lab import (
    canonical_sha256,
    freeze_counterfactual_snapshot,
    settle_counterfactual_snapshot,
)
from brace_portfolio_optimizer import build_rebalance_plan, optimize


@pytest.fixture()
def config():
    return load_config(DEFAULT_CONFIG_PATH)[0]


def analyses():
    rows = []
    for index, instrument_id in enumerate(("a", "b", "c", "d", "e", "f")):
        rows.append(
            {
                "instrument_id": instrument_id,
                "asset_type": "STOCK",
                "sector": f"S{index // 2}",
                "currency": "USD" if index < 3 else "EUR",
                "region": "US" if index < 3 else "Europe",
                "expected_return_base": 0.16 - index * 0.01,
                "expected_drawdown": 0.12,
                "final_score": 90 - index * 3,
                "confidence_score": 0.85 - index * 0.03,
                "risk": {"volatility": 0.20 + index * 0.02},
                "current_price": 100.0 + index,
            }
        )
    return rows


def test_optimizer_can_reconstruct_portfolio_with_dynamic_cash_and_position_count(config):
    governed = replace(
        config,
        portfolio_cash_floor=0.20,
        portfolio_max_active_positions=3,
    )
    result = optimize(
        {"a": 0.18, "b": 0.18, "c": 0.18, "d": 0.18, "e": 0.18, "CASH": 0.10},
        analyses(),
        governed,
    )

    weights = result["target_weights"]
    active = [key for key, value in weights.items() if key != "CASH" and value > 0]
    assert len(active) <= 3
    assert weights["CASH"] >= 0.20 - 1e-8
    assert abs(sum(weights.values()) - 1.0) < 1e-6
    assert result["no_leverage"] is True
    assert result["no_short_positions"] is True
    assert result["safety_kernel_snapshot"]["real_broker_integration_enabled"] is False


def test_rebalance_plan_has_canonical_portfolio_actions(config):
    plan = build_rebalance_plan(
        {"a": 0.20, "b": 0.20, "c": 0.20, "e": 0.10, "CASH": 0.30},
        {"a": 0.20, "b": 0.25, "c": 0.10, "d": 0.20, "CASH": 0.25},
        config,
    )
    actions = {row["instrument_id"]: row["action"] for row in plan}
    assert actions == {
        "a": "KEEP",
        "b": "ADD",
        "c": "TRIM",
        "d": "OPEN",
        "e": "EXIT",
        "CASH": "CASH",
    }


def _counterfactual_analysis():
    return {
        "generated_at": "2026-01-01T12:00:00+00:00",
        "positions": [
            {"instrument_id": "a", "current_price": 100.0},
            {"instrument_id": "b", "current_price": 100.0},
        ],
        "candidates": [],
        "optimization": {
            "selected": "conviction_growth",
            "comparisons": [
                {
                    "name": "current",
                    "weights": {"a": 1.0},
                    "metrics": {"turnover": 0.0},
                },
                {
                    "name": "conviction_growth",
                    "weights": {"a": 0.5, "b": 0.5},
                    "metrics": {"turnover": 0.5},
                },
                {
                    "name": "minimum_variance",
                    "weights": {"b": 1.0},
                    "metrics": {"turnover": 0.5},
                },
            ],
            "portfolio_policy_snapshot": {},
            "safety_kernel_snapshot": {"real_broker_integration_enabled": False},
        },
    }


def test_counterfactual_freeze_rejects_lookahead(config):
    analysis = _counterfactual_analysis()
    analysis["optimization"]["realized_return"] = 0.50
    with pytest.raises(ValueError, match="Look-ahead"):
        freeze_counterfactual_snapshot(analysis, config)


def test_counterfactual_is_frozen_then_scored_only_with_future_market_data(config):
    snapshot = freeze_counterfactual_snapshot(_counterfactual_analysis(), config)
    assert snapshot["prospective_only"] is True
    assert snapshot["historical_backfill"] is False
    assert snapshot["real_broker_integration"] is False

    market = {
        "instruments": {
            "a": {"history": [{"date": "2026-01-08", "close": 101.0}]},
            "b": {"history": [{"date": "2026-01-08", "close": 110.0}]},
        }
    }
    outcome = settle_counterfactual_snapshot(
        snapshot,
        market,
        7,
        as_of=datetime(2026, 1, 9, tzinfo=timezone.utc),
    )
    assert outcome is not None
    assert outcome["winner"] == "minimum_variance"
    assert outcome["regret"] > 0
    assert outcome["selected_vs_current"] > 0


def test_portfolio_evolution_policy_is_separate_and_bounded(tmp_path, config):
    seed = json.loads(
        (ROOT / "data" / "portfolio10k" / "portfolio_evolution_policy.json").read_text(
            encoding="utf-8"
        )
    )
    seed["active_overrides"] = {"portfolio_cash_floor": 0.15}
    seed["status"] = "ACTIVE_PORTFOLIO_POLICY"
    seed.pop("content_sha256", None)
    seed["content_sha256"] = canonical_sha256(seed)
    policy_path = tmp_path / "portfolio_policy.json"
    policy_path.write_text(json.dumps(seed), encoding="utf-8")

    loaded, metadata = load_config(
        DEFAULT_CONFIG_PATH,
        portfolio_evolution_path=policy_path,
    )
    assert loaded.portfolio_cash_floor == pytest.approx(0.15)
    assert loaded.real_broker_integration_enabled is False
    assert metadata["portfolio_evolution_runtime"]["applied"] is True


def test_autonomous_mutation_uses_only_whitelisted_fields(tmp_path, monkeypatch, config):
    attribution_path = tmp_path / "attributions.jsonl"
    rows = []
    with attribution_path.open("w", encoding="utf-8") as handle:
        for index in range(8):
            outcome_id = f"outcome-{index}"
            rows.append(
                {
                    "outcome_id": outcome_id,
                    "snapshot_id": f"snapshot-{index}",
                    "generated_at": f"2026-01-{index + 1:02d}T00:00:00+00:00",
                    "horizon_days": 7,
                    "winner": "current",
                    "regret": 0.01,
                    "selected_vs_current": -0.01,
                }
            )
            handle.write(
                json.dumps(
                    {
                        "outcome_id": outcome_id,
                        "material_regret": True,
                        "primary_attribution": {
                            "parameter": "portfolio_turnover_penalty",
                            "direction": "UP",
                            "strength": 0.8,
                        },
                    }
                )
                + "\n"
            )
    monkeypatch.setattr(evolution, "ATTRIBUTIONS_PATH", attribution_path)

    mutation = evolution.propose_mutation(config, rows)
    assert mutation is not None
    assert mutation["parameter"] == "portfolio_turnover_penalty"
    assert mutation["parameter"] in PORTFOLIO_EVOLUTION_FIELDS
    assert mutation["parameter"] not in IMMUTABLE_SAFETY_FIELDS
    assert mutation["to"] > mutation["from"]


def test_safety_kernel_cannot_be_mutated(config):
    policy = evolution._default_policy(datetime(2026, 1, 1, tzinfo=timezone.utc))
    policy["active_overrides"] = {"real_broker_integration_enabled": 1.0}
    policy.pop("content_sha256", None)
    policy["content_sha256"] = canonical_sha256(policy)
    with pytest.raises(ValueError):
        evolution._validate_policy(policy)
