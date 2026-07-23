from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_historical_accelerator as accelerator


def lesson(code: str, correct: bool, credit: float = 1.0) -> accelerator.Lesson:
    observed = pd.Timestamp("2020-01-03")
    return accelerator.Lesson(
        code=code,
        symbol="TEST",
        observed_at=observed,
        outcome_at=observed + pd.Timedelta(days=28),
        horizon_weeks=4,
        direction=1,
        strength=1.0,
        quality=1.0,
        excess_return=0.05 if correct else -0.05,
        correct=correct,
        credit=credit,
    )


def test_reliable_signal_gets_multiplier_above_one():
    rows = [lesson("price_vs_ma200", True) for _ in range(20)]
    rows += [lesson("price_vs_ma200", False) for _ in range(4)]
    stats = accelerator.fit_reliability(rows, pd.Timestamp("2021-01-01"), 8.0, 0.20)
    assert stats["price_vs_ma200"]["active"] is True
    assert 1.0 < stats["price_vs_ma200"]["multiplier"] <= 1.20


def test_unreliable_signal_gets_multiplier_below_one():
    rows = [lesson("relative_strength_6m", False) for _ in range(20)]
    rows += [lesson("relative_strength_6m", True) for _ in range(4)]
    stats = accelerator.fit_reliability(rows, pd.Timestamp("2021-01-01"), 8.0, 0.20)
    assert stats["relative_strength_6m"]["active"] is True
    assert 0.80 <= stats["relative_strength_6m"]["multiplier"] < 1.0


def test_small_sample_remains_neutral():
    rows = [lesson("drawdown_52w", True) for _ in range(4)]
    stats = accelerator.fit_reliability(rows, pd.Timestamp("2021-01-01"), 8.0, 0.20)
    assert stats["drawdown_52w"]["active"] is False
    assert stats["drawdown_52w"]["multiplier"] == 1.0


def test_historical_seed_credit_is_bounded():
    stats = {
        "price_vs_ma200": {
            "success_credit": 80.0,
            "failure_credit": 20.0,
            "effective_samples": 100.0,
            "active": True,
            "multiplier": 1.1,
        }
    }
    events = accelerator.seed_events(stats, "unit-test")
    total_credit = sum(
        event["evidence_attribution"][0]["credit"]
        for event in events
    )
    assert round(total_credit, 6) == accelerator.HISTORICAL_CREDIT_CAP
    assert all(event["historical_seed"] for event in events)


def test_progress_requires_untouched_test_improvement_and_risk_control():
    validation = {"objective": 0.01}
    good_test = {"cagr": 0.004, "sharpe": 0.0, "max_drawdown": -0.005, "objective": 0.003, "calmar": 0.03}
    bad_risk = {**good_test, "max_drawdown": -0.02}
    no_progress = {"cagr": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "objective": 0.0, "calmar": 0.0}
    assert accelerator.significant_progress(validation, good_test) is True
    assert accelerator.significant_progress(validation, bad_risk) is False
    assert accelerator.significant_progress(validation, no_progress) is False
