#!/usr/bin/env python3
"""BRACE-SPX focused sealed generation 3.

Generation 3 is a smaller, homogeneous refinement derived only from development
 evidence produced by generation 2. It deliberately narrows the search to the
 regularized linear family and the two feature groups that remained stable across
 chronological folds. The final 48-month holdout is never inspected here.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable, List

import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

import brace_spx_generation_research as engine
import brace_spx_research as base

ROOT = Path(__file__).resolve().parents[1]
GENERATION_ID = "spx-focused-v3"
EXPECTED_CANDIDATES = 48

ORIGINAL_FEATURE_COLUMNS = base.feature_columns
ORIGINAL_BUILD_MODEL = base.build_model
ORIGINAL_EXPOSURE = base.probabilities_to_exposure


def feature_columns(frame: pd.DataFrame, feature_set: str) -> List[str]:
    """Return the two focused, predeclared feature groups."""
    available = set(frame.columns)
    groups = {
        "breadth_credit_focus": [
            "breadth_above_ma50",
            "breadth_above_ma200",
            "sector_momentum_mean_63",
            "credit_ratio_63",
            "equal_weight_relative_63",
            "vix_change_21",
        ],
        "cross_asset_focus": [
            "vix_level",
            "vix_change_21",
            "tnx_change_63",
            "tlt_momentum_63",
            "credit_ratio_63",
            "dollar_momentum_63",
            "equal_weight_relative_63",
            "spy_momentum_126",
            "spy_vol_20",
        ],
    }
    if feature_set in groups:
        return [name for name in groups[feature_set] if name in available]
    return ORIGINAL_FEATURE_COLUMNS(frame, feature_set)


def build_model(candidate: base.Candidate, seed: int) -> Pipeline:
    """Build only the predeclared elastic-logistic family."""
    if candidate.family != "elastic_logistic_focus":
        return ORIGINAL_BUILD_MODEL(candidate, seed)
    params = dict(candidate.params)
    model = LogisticRegression(
        C=float(params["C"]),
        penalty="elasticnet",
        l1_ratio=float(params["l1_ratio"]),
        solver="saga",
        max_iter=6000,
        class_weight=None,
        random_state=seed,
    )
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
        ("scaler", RobustScaler()),
        ("model", model),
    ])


def probabilities_to_exposure(
    probabilities: pd.Series,
    realized_vol: pd.Series,
    candidate: base.Candidate,
) -> pd.Series:
    """Map probabilities to a bounded 0-100% SPY exposure."""
    mode = str(candidate.params.get("exposure_mode", "binary"))
    p = probabilities.astype(float)
    if mode == "binary":
        raw = (p >= candidate.threshold_high).astype(float) * candidate.max_exposure
    elif mode == "conviction":
        spread = max(candidate.threshold_high - candidate.threshold_low, 1e-6)
        raw = ((p - candidate.threshold_low) / spread).clip(0.0, 1.0) * candidate.max_exposure
    else:
        return ORIGINAL_EXPOSURE(probabilities, realized_vol, candidate)
    vol = realized_vol.reindex(p.index).astype(float)
    scale = (candidate.volatility_target / vol).replace([float("inf"), float("-inf")], pd.NA)
    scale = pd.to_numeric(scale, errors="coerce").clip(lower=0.25, upper=1.0).fillna(0.5)
    return (raw * scale).clip(lower=0.0, upper=1.0)


def candidate_pool() -> List[base.Candidate]:
    """Return exactly 48 unique, predeclared and genuinely new candidates."""
    candidates: List[base.Candidate] = []
    feature_sets = ["breadth_credit_focus", "cross_asset_focus"]
    thresholds = [(0.57, 0.495), (0.61, 0.51)]
    volatility_targets = [0.12, 0.16]
    exposure_modes = ["binary", "conviction"]
    model_specs: list[dict[str, Any]] = [
        {"C": 0.04, "l1_ratio": 0.15},
        {"C": 0.12, "l1_ratio": 0.35},
        {"C": 0.24, "l1_ratio": 0.55},
    ]
    for feature_set in feature_sets:
        for high, low in thresholds:
            for volatility_target in volatility_targets:
                for exposure_mode in exposure_modes:
                    for model_params in model_specs:
                        candidates.append(base.Candidate(
                            family="elastic_logistic_focus",
                            feature_set=feature_set,
                            threshold_high=high,
                            threshold_low=low,
                            max_exposure=1.0,
                            volatility_target=volatility_target,
                            params={
                                **model_params,
                                "exposure_mode": exposure_mode,
                                "generation": GENERATION_ID,
                            },
                        ))
    ids = [candidate.candidate_id() for candidate in candidates]
    if len(candidates) != EXPECTED_CANDIDATES or len(set(ids)) != EXPECTED_CANDIDATES:
        raise RuntimeError("Generation 3 universe must contain exactly 48 unique candidates")
    return candidates


def development_baselines(prices: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Compute strong baselines on the same chronological validation months."""
    frame = base.monthly_dataset(prices)
    development, _sealed_holdout = base.holdout_split(frame)
    validation_parts = [valid for _train, valid in base.chronological_folds(development.index)]
    validation_index = pd.DatetimeIndex(sorted(set().union(*(set(part) for part in validation_parts))))
    valid = development.loc[validation_index]
    risk_free = engine.monthly_risk_free(prices, valid.index)

    buy_exposure = pd.Series(1.0, index=valid.index)
    buy_returns, buy_turnover = base.strategy_returns(valid["asset_return"], buy_exposure)

    trend_exposure = (valid["spy_ma_gap_200"].fillna(-1.0) > 0.0).astype(float)
    trend_returns, trend_turnover = base.strategy_returns(valid["asset_return"], trend_exposure)

    return {
        "buy_and_hold": engine.excess_metrics(buy_returns, risk_free, buy_turnover),
        "trend_200d": engine.excess_metrics(trend_returns, risk_free, trend_turnover),
    }


def configure_engine() -> None:
    engine.GENERATION_ID = GENERATION_ID
    engine.OUTPUT_PATH = ROOT / "data/research/brace_spx_generation3_research.json"
    engine.LEDGER_PATH = ROOT / "data/research/brace_spx_generation3_experiments.json"
    engine.MANIFEST_PATH = ROOT / "data/research/brace_spx_generation3_manifest.json"
    base.feature_columns = feature_columns
    base.build_model = build_model
    base.probabilities_to_exposure = probabilities_to_exposure
    base.candidate_pool = candidate_pool


def run(prices: pd.DataFrame, budget: int, seed: int) -> dict[str, Any]:
    configure_engine()
    report = engine.run(prices, budget, seed)
    report["design"] = {
        "scope": "focused_refinement",
        "derived_from_generation": "spx-sealed-v2",
        "candidate_space_size": EXPECTED_CANDIDATES,
        "model_families": 1,
        "feature_groups": 2,
        "holdout_used_for_design": False,
    }
    report["development_baselines"] = development_baselines(prices)
    engine.write_json(engine.OUTPUT_PATH, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=base.DEFAULT_START)
    parser.add_argument("--budget", type=int, default=12)
    parser.add_argument("--seed", type=int, default=base.RANDOM_SEED + 3000)
    args = parser.parse_args()
    symbols: Iterable[str] = [*base.RICH_SYMBOLS.values(), *base.SECTOR_SYMBOLS, engine.RISK_FREE_SYMBOL]
    prices = base.download_prices(symbols, args.start)
    report = run(prices, args.budget, args.seed)
    print(
        f"BRACE-SPX v3: status={report['status']} "
        f"experiments={report['experiments_total']}/{report['candidate_space_size']}"
    )


if __name__ == "__main__":
    main()
