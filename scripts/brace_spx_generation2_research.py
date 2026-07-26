#!/usr/bin/env python3
"""BRACE-SPX sealed generation 2.

Generation 2 is a genuinely new, predeclared search space. It reuses the audited
walk-forward/statistical machinery from generation 1 while adding new feature
subsets, model families and exposure structures. Routine runs cannot inspect the
48-month holdout.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable, List

import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

import brace_spx_generation_research as engine
import brace_spx_research as base

ROOT = Path(__file__).resolve().parents[1]
GENERATION_ID = "spx-sealed-v2"

ORIGINAL_FEATURE_COLUMNS = base.feature_columns
ORIGINAL_BUILD_MODEL = base.build_model
ORIGINAL_EXPOSURE = base.probabilities_to_exposure


def feature_columns(frame: pd.DataFrame, feature_set: str) -> List[str]:
    available = set(frame.columns)
    groups = {
        "trend_compact": [
            "spy_ma_gap_50", "spy_ma_gap_100", "spy_ma_gap_200",
            "spy_momentum_63", "spy_momentum_126", "spy_momentum_252",
            "spy_vol_20", "spy_drawdown_252",
        ],
        "breadth_credit": [
            "breadth_above_ma50", "breadth_above_ma200",
            "sector_momentum_mean_63", "sector_momentum_dispersion_63",
            "credit_ratio_63", "equal_weight_relative_63", "vix_change_21",
        ],
        "cross_asset": [
            "vix_level", "vix_change_21", "tnx_level", "tnx_change_63",
            "tlt_momentum_63", "credit_ratio_63", "dollar_momentum_63",
            "equal_weight_relative_63", "spy_momentum_126", "spy_vol_20",
        ],
        "balanced_compact": [
            "spy_ma_gap_100", "spy_ma_gap_200", "spy_momentum_63",
            "spy_momentum_126", "spy_vol_20", "spy_vol_60",
            "spy_drawdown_126", "vix_change_21", "credit_ratio_63",
            "breadth_above_ma200", "equal_weight_relative_63",
        ],
    }
    if feature_set in groups:
        return [name for name in groups[feature_set] if name in available]
    return ORIGINAL_FEATURE_COLUMNS(frame, feature_set)


def build_model(candidate: base.Candidate, seed: int) -> Pipeline:
    params = dict(candidate.params)
    if candidate.family == "elastic_logistic":
        model = LogisticRegression(
            C=float(params["C"]), penalty="elasticnet", l1_ratio=float(params["l1_ratio"]),
            solver="saga", max_iter=5000, class_weight=None, random_state=seed,
        )
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("scaler", RobustScaler()),
            ("model", model),
        ])
    if candidate.family == "extra_trees":
        model = ExtraTreesClassifier(
            n_estimators=500, max_depth=int(params["max_depth"]),
            min_samples_leaf=int(params["min_samples_leaf"]), max_features=float(params["max_features"]),
            class_weight="balanced", n_jobs=-1, random_state=seed,
        )
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("model", model),
        ])
    if candidate.family == "calibrated_hist_gb":
        core = HistGradientBoostingClassifier(
            learning_rate=float(params["learning_rate"]), max_iter=220,
            max_leaf_nodes=int(params["max_leaf_nodes"]), min_samples_leaf=16,
            l2_regularization=2.0, random_state=seed,
        )
        model = CalibratedClassifierCV(core, method="sigmoid", cv=3)
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("model", model),
        ])
    return ORIGINAL_BUILD_MODEL(candidate, seed)


def probabilities_to_exposure(probabilities: pd.Series, realized_vol: pd.Series, candidate: base.Candidate) -> pd.Series:
    mode = str(candidate.params.get("exposure_mode", "three_step"))
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
    candidates: List[base.Candidate] = []
    feature_sets = ["trend_compact", "breadth_credit", "cross_asset", "balanced_compact"]
    thresholds = [(0.55, 0.49), (0.59, 0.50), (0.63, 0.52)]
    vol_targets = [0.10, 0.14, 0.18]
    exposure_modes = ["binary", "conviction"]
    model_specs: list[tuple[str, dict[str, Any]]] = [
        ("elastic_logistic", {"C": 0.08, "l1_ratio": 0.25}),
        ("elastic_logistic", {"C": 0.30, "l1_ratio": 0.65}),
        ("extra_trees", {"max_depth": 4, "min_samples_leaf": 10, "max_features": 0.70}),
        ("calibrated_hist_gb", {"learning_rate": 0.04, "max_leaf_nodes": 9}),
    ]
    for feature_set in feature_sets:
        for high, low in thresholds:
            for vol_target in vol_targets:
                for exposure_mode in exposure_modes:
                    for family, params in model_specs:
                        candidates.append(base.Candidate(
                            family=family,
                            feature_set=feature_set,
                            threshold_high=high,
                            threshold_low=low,
                            max_exposure=1.0,
                            volatility_target=vol_target,
                            params={**params, "exposure_mode": exposure_mode, "generation": GENERATION_ID},
                        ))
    ids = [candidate.candidate_id() for candidate in candidates]
    if len(candidates) != 288 or len(set(ids)) != len(ids):
        raise RuntimeError("Generation 2 candidate universe is not the declared 288 unique candidates")
    return candidates


def configure_engine() -> None:
    engine.GENERATION_ID = GENERATION_ID
    engine.OUTPUT_PATH = ROOT / "data/research/brace_spx_generation2_research.json"
    engine.LEDGER_PATH = ROOT / "data/research/brace_spx_generation2_experiments.json"
    engine.MANIFEST_PATH = ROOT / "data/research/brace_spx_generation2_manifest.json"
    base.feature_columns = feature_columns
    base.build_model = build_model
    base.probabilities_to_exposure = probabilities_to_exposure
    base.candidate_pool = candidate_pool


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=base.DEFAULT_START)
    parser.add_argument("--budget", type=int, default=36)
    parser.add_argument("--seed", type=int, default=base.RANDOM_SEED + 2000)
    args = parser.parse_args()
    configure_engine()
    symbols: Iterable[str] = [*base.RICH_SYMBOLS.values(), *base.SECTOR_SYMBOLS, engine.RISK_FREE_SYMBOL]
    prices = base.download_prices(symbols, args.start)
    report = engine.run(prices, args.budget, args.seed)
    print(f"BRACE-SPX v2: status={report['status']} experiments={report['experiments_total']}/{report['candidate_space_size']}")


if __name__ == "__main__":
    main()
