from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import portfolio_10k_brace_promotion as promotion


def payload(brace_cagr=0.18, baseline_cagr=0.17, brace_sharpe=1.10, baseline_sharpe=1.05, brace_dd=-0.25, baseline_dd=-0.24, weeks=400, stable=True):
    return {
        "metrics": {
            "baseline": {
                "cagr": baseline_cagr,
                "sharpe_zero_rf": baseline_sharpe,
                "max_drawdown": baseline_dd,
            },
            "brace_standard": {
                "cagr": brace_cagr,
                "sharpe_zero_rf": brace_sharpe,
                "max_drawdown": brace_dd,
                "weeks": weeks,
            },
        },
        "robustness": {"stable_within_five_percentage_points": stable},
    }


def test_promotion_requires_all_predefined_checks():
    result = promotion.evaluate(payload())
    assert result["status"] == "eligible_for_live_confirmation"
    assert all(result["checks"].values())
    assert result["failed_checks"] == []


def test_lower_cagr_and_sharpe_keep_baseline_as_champion():
    result = promotion.evaluate(payload(brace_cagr=0.16, brace_sharpe=0.95))
    assert result["status"] == "not_promoted"
    assert result["champion"] == "baseline"
    assert "cagr_advantage" in result["failed_checks"]
    assert "sharpe_advantage" in result["failed_checks"]


def test_short_history_cannot_promote_even_with_good_returns():
    result = promotion.evaluate(payload(weeks=100))
    assert result["status"] == "not_promoted"
    assert result["checks"]["minimum_history"] is False


def test_excessive_drawdown_disadvantage_blocks_promotion():
    result = promotion.evaluate(payload(brace_dd=-0.30, baseline_dd=-0.24))
    assert result["status"] == "not_promoted"
    assert result["checks"]["drawdown_control"] is False
