import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import brace_spx_generation_research as generation
import brace_spx_research as base


def test_excess_sharpe_uses_risk_free_rate():
    index = pd.date_range("2020-01-31", periods=24, freq="ME")
    returns = pd.Series([0.01, 0.02, -0.005, 0.015] * 6, index=index)
    turnover = pd.Series(0.1, index=index)
    zero = generation.excess_metrics(returns, pd.Series(0.0, index=index), turnover)
    positive_rf = generation.excess_metrics(returns, pd.Series(0.004, index=index), turnover)
    assert positive_rf["sharpe_excess"] < zero["sharpe_excess"]


def test_dsr_penalizes_more_trials():
    index = pd.date_range("2010-01-31", periods=120, freq="ME")
    returns = pd.Series([0.012, 0.008, -0.004, 0.01, 0.006] * 24, index=index)
    few = generation.deflated_sharpe_ratio(1.0, returns, trials=5, sharpe_std=0.25)
    many = generation.deflated_sharpe_ratio(1.0, returns, trials=500, sharpe_std=0.25)
    assert many["expected_max_sharpe"] > few["expected_max_sharpe"]
    assert many["probability"] < few["probability"]


def test_pbo_detects_available_matrix():
    rng = np.random.default_rng(17)
    index = pd.date_range("2000-01-31", periods=96, freq="ME")
    matrix = pd.DataFrame(
        {
            "a": rng.normal(0.005, 0.03, len(index)),
            "b": rng.normal(0.004, 0.03, len(index)),
            "c": rng.normal(0.003, 0.03, len(index)),
        },
        index=index,
    )
    result = generation.probability_backtest_overfitting(matrix, blocks=8)
    assert result["available"] is True
    assert 0.0 <= result["probability"] <= 1.0
    assert result["splits"] > 0


def test_generation_signature_is_order_sensitive_and_stable():
    pool = base.candidate_pool()
    first = generation.generation_signature(pool)
    second = generation.generation_signature(pool)
    reversed_signature = generation.generation_signature(list(reversed(pool)))
    assert first == second
    assert first != reversed_signature


def test_source_has_no_automatic_holdout_evaluation():
    source = (SCRIPTS / "brace_spx_generation_research.py").read_text(encoding="utf-8")
    assert "train_and_evaluate_holdout" not in source
    assert '"workflow_can_open_holdout": False' in source
    assert '"holdout_opened_by_workflow": False' in source


def test_manifest_rejects_mutation(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"
    monkeypatch.setattr(generation, "MANIFEST_PATH", manifest_path)
    pool = base.candidate_pool()
    manifest = generation.ensure_manifest(pool, "2021-12-31", "2022-01-31", "2025-12-31")
    assert manifest["holdout"]["accessed"] is False
    mutated = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutated["candidate_signature"] = "bad"
    manifest_path.write_text(json.dumps(mutated), encoding="utf-8")
    try:
        generation.ensure_manifest(pool, "2021-12-31", "2022-01-31", "2025-12-31")
    except RuntimeError as exc:
        assert "manifest mismatch" in str(exc).lower()
    else:
        raise AssertionError("Mutated manifest should be rejected")
