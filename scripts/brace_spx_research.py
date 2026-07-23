#!/usr/bin/env python3
"""BRACE-SPX Research Lab.

Research-only, single-instrument laboratory for S&P 500 exposure.  The engine
borrows useful AlphaGo/AlphaZero ideas (one clearly defined environment,
champion-challenger competition, immutable experiment memory, repeated search)
without pretending that a market is a stationary board game.

The traded instrument is SPY.  Exogenous market series are features only.
No orders are generated and no live BriefRooms portfolio file is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "research" / "brace_spx_research.json"
LEDGER_PATH = ROOT / "data" / "research" / "brace_spx_experiments.json"

MODEL_VERSION = "0.1.0"
TARGET_SYMBOL = "SPY"
RICH_SYMBOLS = {
    "spy": "SPY",
    "vix": "^VIX",
    "tnx": "^TNX",
    "tlt": "TLT",
    "hyg": "HYG",
    "lqd": "LQD",
    "uup": "UUP",
    "rsp": "RSP",
}
SECTOR_SYMBOLS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]
DEFAULT_START = "1993-01-01"
FORWARD_DAYS = 21
MONTHLY_COST = 0.0005
HOLDOUT_MONTHS = 48
MIN_TRAIN_MONTHS = 120
VALIDATION_MONTHS = 18
PURGE_MONTHS = 1
RANDOM_SEED = 73291


@dataclass(frozen=True)
class Candidate:
    family: str
    feature_set: str
    threshold_high: float
    threshold_low: float
    max_exposure: float
    volatility_target: float
    params: Mapping[str, Any]

    def candidate_id(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def annualized_metrics(returns: pd.Series, turnover: pd.Series | None = None) -> Dict[str, float]:
    clean = returns.dropna().astype(float)
    if clean.empty:
        return {}
    periods = 12.0
    years = max(len(clean) / periods, 1.0 / periods)
    total = float((1.0 + clean).prod() - 1.0)
    cagr = float((1.0 + total) ** (1.0 / years) - 1.0)
    vol = float(clean.std(ddof=1) * math.sqrt(periods)) if len(clean) > 1 else 0.0
    downside = clean[clean < 0]
    downside_vol = float(downside.std(ddof=1) * math.sqrt(periods)) if len(downside) > 1 else 0.0
    equity = (1.0 + clean).cumprod()
    max_drawdown = float((equity / equity.cummax() - 1.0).min())
    sharpe = cagr / vol if vol > 0 else 0.0
    sortino = cagr / downside_vol if downside_vol > 0 else 0.0
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else 0.0
    annual_turnover = float(turnover.reindex(clean.index).fillna(0.0).mean() * periods) if turnover is not None else 0.0
    positive_years = clean.groupby(clean.index.year).apply(lambda x: float((1.0 + x).prod() - 1.0) > 0)
    return {
        "total_return": round(total, 6),
        "cagr": round(cagr, 6),
        "annualized_volatility": round(vol, 6),
        "sharpe_zero_rf": round(sharpe, 4),
        "sortino_zero_rf": round(sortino, 4),
        "max_drawdown": round(max_drawdown, 6),
        "calmar": round(calmar, 4),
        "annualized_turnover": round(annual_turnover, 4),
        "positive_year_ratio": round(float(positive_years.mean()) if len(positive_years) else 0.0, 4),
        "months": int(len(clean)),
    }


def objective(metrics: Mapping[str, float]) -> float:
    if not metrics:
        return -999.0
    cagr = float(metrics.get("cagr", 0.0))
    sharpe = float(metrics.get("sharpe_zero_rf", 0.0))
    calmar = float(metrics.get("calmar", 0.0))
    drawdown = abs(min(0.0, float(metrics.get("max_drawdown", 0.0))))
    turnover = float(metrics.get("annualized_turnover", 0.0))
    positive_year_ratio = float(metrics.get("positive_year_ratio", 0.0))
    drawdown_penalty = max(0.0, drawdown - 0.25) * 0.60
    turnover_penalty = max(0.0, turnover - 2.0) * 0.004
    return cagr + 0.10 * sharpe + 0.06 * calmar + 0.025 * positive_year_ratio - drawdown_penalty - turnover_penalty


def _extract_close(data: pd.DataFrame, symbols: Sequence[str]) -> pd.DataFrame:
    if data.empty:
        raise RuntimeError("No market data downloaded")
    if isinstance(data.columns, pd.MultiIndex):
        if "Close" in data.columns.get_level_values(0):
            close = data["Close"]
        elif "Close" in data.columns.get_level_values(1):
            close = data.xs("Close", axis=1, level=1)
        else:
            raise RuntimeError("Market response has no Close field")
    else:
        if "Close" not in data:
            raise RuntimeError("Market response has no Close field")
        close = data[["Close"]].rename(columns={"Close": symbols[0]})
    if isinstance(close, pd.Series):
        close = close.to_frame(symbols[0])
    close = close.copy()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close.sort_index()


def download_prices(symbols: Iterable[str], start: str) -> pd.DataFrame:
    import yfinance as yf

    ordered = list(dict.fromkeys(symbols))
    data = yf.download(ordered, start=start, auto_adjust=True, progress=False, group_by="column", threads=True)
    close = _extract_close(data, ordered)
    available = [symbol for symbol in ordered if symbol in close.columns and close[symbol].notna().sum() >= 260]
    if TARGET_SYMBOL not in available:
        raise RuntimeError("SPY history is unavailable")
    return close[available]


def _series(prices: pd.DataFrame, symbol: str) -> pd.Series:
    if symbol not in prices:
        return pd.Series(np.nan, index=prices.index, dtype=float)
    return prices[symbol].astype(float).reindex(prices.index).ffill()


def build_daily_features(prices: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    spy = _series(prices, TARGET_SYMBOL)
    daily_ret = spy.pct_change(fill_method=None)
    out = pd.DataFrame(index=spy.index)

    for window in (10, 20, 50, 100, 200):
        ma = spy.rolling(window, min_periods=max(5, int(window * 0.75))).mean()
        out[f"spy_ma_gap_{window}"] = spy / ma - 1.0
    for window in (5, 21, 63, 126, 252):
        out[f"spy_momentum_{window}"] = spy / spy.shift(window) - 1.0
    for window in (10, 20, 60, 126):
        out[f"spy_vol_{window}"] = daily_ret.rolling(window, min_periods=max(5, int(window * 0.75))).std(ddof=1) * math.sqrt(252)
    for window in (63, 126, 252):
        high = spy.rolling(window, min_periods=max(20, int(window * 0.5))).max()
        out[f"spy_drawdown_{window}"] = spy / high - 1.0

    vix = _series(prices, RICH_SYMBOLS["vix"])
    out["vix_level"] = vix
    out["vix_change_21"] = vix / vix.shift(21) - 1.0

    tnx = _series(prices, RICH_SYMBOLS["tnx"])
    out["tnx_level"] = tnx
    out["tnx_change_63"] = tnx - tnx.shift(63)

    tlt = _series(prices, RICH_SYMBOLS["tlt"])
    hyg = _series(prices, RICH_SYMBOLS["hyg"])
    lqd = _series(prices, RICH_SYMBOLS["lqd"])
    uup = _series(prices, RICH_SYMBOLS["uup"])
    rsp = _series(prices, RICH_SYMBOLS["rsp"])
    out["tlt_momentum_63"] = tlt / tlt.shift(63) - 1.0
    out["credit_ratio_63"] = (hyg / lqd) / (hyg / lqd).shift(63) - 1.0
    out["dollar_momentum_63"] = uup / uup.shift(63) - 1.0
    out["equal_weight_relative_63"] = (rsp / spy) / (rsp / spy).shift(63) - 1.0

    sectors = [symbol for symbol in SECTOR_SYMBOLS if symbol in prices]
    if sectors:
        sector_prices = prices[sectors].reindex(spy.index).ffill()
        sector_ma50 = sector_prices.rolling(50, min_periods=35).mean()
        sector_ma200 = sector_prices.rolling(200, min_periods=150).mean()
        sector_mom63 = sector_prices / sector_prices.shift(63) - 1.0
        out["breadth_above_ma50"] = (sector_prices > sector_ma50).mean(axis=1)
        out["breadth_above_ma200"] = (sector_prices > sector_ma200).mean(axis=1)
        out["sector_momentum_mean_63"] = sector_mom63.mean(axis=1)
        out["sector_momentum_dispersion_63"] = sector_mom63.std(axis=1, ddof=1)
    else:
        for column in ("breadth_above_ma50", "breadth_above_ma200", "sector_momentum_mean_63", "sector_momentum_dispersion_63"):
            out[column] = np.nan

    month = out.index.month.astype(float)
    out["calendar_month_sin"] = np.sin(2.0 * math.pi * month / 12.0)
    out["calendar_month_cos"] = np.cos(2.0 * math.pi * month / 12.0)
    forward_return = spy.shift(-FORWARD_DAYS) / spy - 1.0
    return out.replace([np.inf, -np.inf], np.nan), forward_return


def monthly_dataset(prices: pd.DataFrame) -> pd.DataFrame:
    features, forward = build_daily_features(prices)
    monthly_features = features.resample("ME").last()
    monthly_forward = forward.resample("ME").last().rename("forward_return")
    spy = _series(prices, TARGET_SYMBOL)
    monthly_spy = spy.resample("ME").last()
    monthly_return = monthly_spy.pct_change(fill_method=None).rename("asset_return")
    frame = monthly_features.join(monthly_forward).join(monthly_return)
    frame["target_up"] = (frame["forward_return"] > 0.0).astype(int)
    frame["realized_vol_20"] = features["spy_vol_20"].resample("ME").last()
    return frame.dropna(subset=["forward_return", "asset_return"])


CORE_PREFIXES = ("spy_ma_gap_", "spy_momentum_", "spy_vol_", "spy_drawdown_", "calendar_")
RICH_COLUMNS = (
    "vix_level", "vix_change_21", "tnx_level", "tnx_change_63", "tlt_momentum_63", "credit_ratio_63",
    "dollar_momentum_63", "equal_weight_relative_63", "breadth_above_ma50", "breadth_above_ma200",
    "sector_momentum_mean_63", "sector_momentum_dispersion_63",
)


def feature_columns(frame: pd.DataFrame, feature_set: str) -> List[str]:
    core = [column for column in frame.columns if column.startswith(CORE_PREFIXES)]
    if feature_set == "core":
        return sorted(core)
    rich = [column for column in RICH_COLUMNS if column in frame.columns]
    if feature_set == "risk":
        risk = [c for c in core if "vol_" in c or "drawdown_" in c or "ma_gap_" in c]
        return sorted(set(risk + [c for c in rich if c in {"vix_level", "vix_change_21", "tnx_change_63", "credit_ratio_63"}]))
    return sorted(set(core + rich))


def build_model(candidate: Candidate, seed: int) -> Pipeline:
    family = candidate.family
    params = dict(candidate.params)
    if family == "logistic":
        model = LogisticRegression(C=float(params["C"]), solver="lbfgs", max_iter=3000, class_weight=params.get("class_weight"), random_state=seed)
        return Pipeline([("imputer", SimpleImputer(strategy="median", add_indicator=True)), ("scaler", StandardScaler()), ("model", model)])
    if family == "hist_gb":
        model = HistGradientBoostingClassifier(
            learning_rate=float(params["learning_rate"]), max_iter=int(params["max_iter"]),
            max_leaf_nodes=int(params["max_leaf_nodes"]), min_samples_leaf=int(params["min_samples_leaf"]),
            l2_regularization=float(params["l2_regularization"]), random_state=seed,
        )
        return Pipeline([("imputer", SimpleImputer(strategy="median", add_indicator=True)), ("model", model)])
    if family == "random_forest":
        model = RandomForestClassifier(
            n_estimators=int(params["n_estimators"]), max_depth=params.get("max_depth"),
            min_samples_leaf=int(params["min_samples_leaf"]), max_features=float(params["max_features"]),
            class_weight="balanced_subsample", n_jobs=-1, random_state=seed,
        )
        return Pipeline([("imputer", SimpleImputer(strategy="median", add_indicator=True)), ("model", model)])
    raise ValueError(f"Unsupported family: {family}")


def chronological_folds(index: pd.DatetimeIndex) -> List[Tuple[np.ndarray, np.ndarray]]:
    count = len(index)
    if count < MIN_TRAIN_MONTHS + VALIDATION_MONTHS + PURGE_MONTHS:
        return []
    folds: List[Tuple[np.ndarray, np.ndarray]] = []
    train_end = MIN_TRAIN_MONTHS
    while train_end + PURGE_MONTHS + VALIDATION_MONTHS <= count:
        valid_start = train_end + PURGE_MONTHS
        valid_end = valid_start + VALIDATION_MONTHS
        folds.append((np.arange(0, train_end), np.arange(valid_start, valid_end)))
        train_end += VALIDATION_MONTHS
    return folds[-6:]


def probabilities_to_exposure(probabilities: pd.Series, realized_vol: pd.Series, candidate: Candidate) -> pd.Series:
    p = probabilities.astype(float)
    base = pd.Series(0.0, index=p.index, dtype=float)
    base[p >= candidate.threshold_low] = 0.50 * candidate.max_exposure
    base[p >= candidate.threshold_high] = candidate.max_exposure
    vol = realized_vol.reindex(p.index).astype(float)
    vol_scale = (candidate.volatility_target / vol).replace([np.inf, -np.inf], np.nan).clip(lower=0.25, upper=1.0)
    return (base * vol_scale.fillna(0.5)).clip(lower=0.0, upper=1.0)


def strategy_returns(asset_returns: pd.Series, exposure: pd.Series, cost: float = MONTHLY_COST) -> Tuple[pd.Series, pd.Series]:
    applied = exposure.shift(1).reindex(asset_returns.index).fillna(0.0).clip(0.0, 1.0)
    turnover = applied.diff().abs()
    if len(turnover):
        turnover.iloc[0] = abs(applied.iloc[0])
    returns = applied * asset_returns.fillna(0.0) - turnover.fillna(0.0) * cost
    return returns, turnover


def baseline_exposures(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    buy_hold = pd.Series(1.0, index=frame.index)
    trend = (frame["spy_ma_gap_200"] > 0.0).astype(float)
    dual = ((frame["spy_ma_gap_200"] > 0.0) & (frame["spy_momentum_126"] > 0.0)).astype(float)
    return {"buy_hold": buy_hold, "trend_200d": trend, "dual_trend": dual}


def evaluate_exposure(frame: pd.DataFrame, exposure: pd.Series) -> Dict[str, Any]:
    returns, turnover = strategy_returns(frame["asset_return"], exposure)
    return {"metrics": annualized_metrics(returns, turnover), "returns": returns, "turnover": turnover, "exposure": exposure}


def candidate_pool() -> List[Candidate]:
    candidates: List[Candidate] = []
    thresholds = [(0.56, 0.49), (0.60, 0.50), (0.64, 0.52)]
    vol_targets = [0.12, 0.16, 0.20]
    feature_sets = ["core", "risk", "rich"]
    for feature_set in feature_sets:
        for high, low in thresholds:
            for vol_target in vol_targets:
                for C in (0.05, 0.2, 1.0, 5.0):
                    candidates.append(Candidate("logistic", feature_set, high, low, 1.0, vol_target, {"C": C, "class_weight": None}))
                for learning_rate in (0.03, 0.06):
                    for leaves in (7, 15):
                        candidates.append(Candidate("hist_gb", feature_set, high, low, 1.0, vol_target, {
                            "learning_rate": learning_rate, "max_iter": 180, "max_leaf_nodes": leaves,
                            "min_samples_leaf": 12, "l2_regularization": 1.0,
                        }))
                for depth in (3, 5):
                    candidates.append(Candidate("random_forest", feature_set, high, low, 1.0, vol_target, {
                        "n_estimators": 400, "max_depth": depth, "min_samples_leaf": 8, "max_features": 0.65,
                    }))
    return candidates


def load_json(path: Path, default: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else dict(default)
    except (OSError, json.JSONDecodeError):
        return dict(default)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fit_predict_candidate(frame: pd.DataFrame, candidate: Candidate, train_indices: np.ndarray, predict_indices: np.ndarray, seed: int) -> pd.Series:
    columns = feature_columns(frame, candidate.feature_set)
    if not columns:
        raise ValueError("Candidate has no available feature columns")
    train = frame.iloc[train_indices]
    predict = frame.iloc[predict_indices]
    y = train["target_up"].astype(int)
    if y.nunique() < 2:
        return pd.Series(float(y.iloc[0]) if len(y) else 0.5, index=predict.index)
    model = build_model(candidate, seed)
    model.fit(train[columns], y)
    probabilities = model.predict_proba(predict[columns])[:, 1]
    return pd.Series(probabilities, index=predict.index, dtype=float)


def evaluate_candidate_walk_forward(frame: pd.DataFrame, candidate: Candidate, seed: int) -> Dict[str, Any]:
    folds = chronological_folds(frame.index)
    if not folds:
        raise ValueError("Insufficient history for chronological folds")
    probabilities: List[pd.Series] = []
    fold_metrics: List[Dict[str, float]] = []
    for fold_number, (train_idx, valid_idx) in enumerate(folds):
        predicted = fit_predict_candidate(frame, candidate, train_idx, valid_idx, seed + fold_number)
        probabilities.append(predicted)
        valid = frame.loc[predicted.index]
        exposure = probabilities_to_exposure(predicted, valid["realized_vol_20"], candidate)
        candidate_fold = evaluate_exposure(valid, exposure)["metrics"]
        fold_baselines = {name: evaluate_exposure(valid, baseline_exposure)["metrics"] for name, baseline_exposure in baseline_exposures(valid).items()}
        strongest_fold_name, strongest_fold = max(fold_baselines.items(), key=lambda item: objective(item[1]))
        fold_metrics.append({**candidate_fold, "strongest_baseline": strongest_fold_name, "objective_advantage": round(objective(candidate_fold) - objective(strongest_fold), 8)})
    joined = pd.concat(probabilities).sort_index()
    valid_frame = frame.loc[joined.index]
    exposure = probabilities_to_exposure(joined, valid_frame["realized_vol_20"], candidate)
    metrics = evaluate_exposure(valid_frame, exposure)["metrics"]
    baseline_metrics = {name: evaluate_exposure(valid_frame, baseline_exposure)["metrics"] for name, baseline_exposure in baseline_exposures(valid_frame).items()}
    return {
        "metrics": metrics, "objective": round(objective(metrics), 8), "baseline_metrics": baseline_metrics,
        "fold_metrics": fold_metrics, "folds": len(folds), "months": len(joined), "probabilities": joined,
    }


def holdout_split(frame: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if len(frame) <= HOLDOUT_MONTHS + MIN_TRAIN_MONTHS:
        raise ValueError("Insufficient history for sealed holdout")
    return frame.iloc[:-HOLDOUT_MONTHS].copy(), frame.iloc[-HOLDOUT_MONTHS:].copy()


def train_and_evaluate_holdout(development: pd.DataFrame, holdout: pd.DataFrame, candidate: Candidate, seed: int) -> Dict[str, Any]:
    columns = feature_columns(development, candidate.feature_set)
    model = build_model(candidate, seed)
    model.fit(development[columns], development["target_up"].astype(int))
    probability = pd.Series(model.predict_proba(holdout[columns])[:, 1], index=holdout.index)
    exposure = probabilities_to_exposure(probability, holdout["realized_vol_20"], candidate)
    evaluation = evaluate_exposure(holdout, exposure)
    return {
        "metrics": evaluation["metrics"], "objective": round(objective(evaluation["metrics"]), 8),
        "probability_tail": [{"date": idx.date().isoformat(), "probability": round(float(value), 6)} for idx, value in probability.tail(12).items()],
        "exposure_tail": [{"date": idx.date().isoformat(), "exposure": round(float(value), 6)} for idx, value in exposure.tail(12).items()],
    }


def robustness_gate(candidate_metrics: Mapping[str, float], baselines: Mapping[str, Mapping[str, float]], fold_metrics: Sequence[Mapping[str, float]]) -> Dict[str, Any]:
    strongest_name, strongest_metrics = max(baselines.items(), key=lambda item: objective(item[1]))
    fold_positive = sum(1 for item in fold_metrics if float(item.get("objective_advantage", -999.0)) > 0.0 and float(item.get("max_drawdown", 0.0)) > -0.35)
    required = max(3, math.ceil(0.67 * len(fold_metrics)))
    passed = (
        objective(candidate_metrics) >= objective(strongest_metrics) + 0.01 and fold_positive >= required
        and float(candidate_metrics.get("max_drawdown", -1.0)) >= -0.32
        and float(candidate_metrics.get("annualized_turnover", 99.0)) <= 3.0
    )
    return {
        "passed": passed, "strongest_baseline": strongest_name,
        "objective_advantage": round(objective(candidate_metrics) - objective(strongest_metrics), 8),
        "positive_robust_folds": fold_positive, "required_positive_folds": required,
    }


def wow_gate(candidate: Mapping[str, float], benchmark: Mapping[str, float]) -> Dict[str, Any]:
    cagr_delta = float(candidate.get("cagr", 0.0)) - float(benchmark.get("cagr", 0.0))
    sharpe_delta = float(candidate.get("sharpe_zero_rf", 0.0)) - float(benchmark.get("sharpe_zero_rf", 0.0))
    drawdown_delta = float(candidate.get("max_drawdown", 0.0)) - float(benchmark.get("max_drawdown", 0.0))
    calmar_delta = float(candidate.get("calmar", 0.0)) - float(benchmark.get("calmar", 0.0))
    passed = (
        (cagr_delta >= 0.02 or sharpe_delta >= 0.25) and drawdown_delta >= -0.02 and calmar_delta >= 0.10
        and float(candidate.get("positive_year_ratio", 0.0)) >= 0.60
        and float(candidate.get("annualized_turnover", 99.0)) <= 3.0
    )
    return {
        "passed": passed, "cagr_delta": round(cagr_delta, 6), "sharpe_delta": round(sharpe_delta, 4),
        "drawdown_delta": round(drawdown_delta, 6), "calmar_delta": round(calmar_delta, 4),
        "requirements": {
            "cagr_or_sharpe": "CAGR +2 pp or Sharpe +0.25", "max_drawdown_deterioration": "no worse than -2 pp",
            "calmar_improvement": "+0.10", "positive_year_ratio": ">=60%", "annualized_turnover": "<=3.0",
        },
    }


def serialize_experiment(candidate: Candidate, result: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id(), "candidate": asdict(candidate),
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "walk_forward": {
            "metrics": result["metrics"], "objective": result["objective"], "baseline_metrics": result["baseline_metrics"],
            "fold_metrics": result["fold_metrics"], "folds": result["folds"], "months": result["months"],
        },
    }


def run_research(prices: pd.DataFrame, budget: int, output_path: Path, ledger_path: Path, seed: int = RANDOM_SEED) -> Dict[str, Any]:
    frame = monthly_dataset(prices)
    development, holdout = holdout_split(frame)
    ledger = load_json(ledger_path, {
        "schema_version": "1.0.0", "model": "BRACE-SPX Research Lab", "experiments": [],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    experiments = ledger.setdefault("experiments", [])
    seen = {str(item.get("candidate_id")) for item in experiments}
    pool = candidate_pool()
    rng = random.Random(seed + len(experiments))
    unseen = [candidate for candidate in pool if candidate.candidate_id() not in seen]
    rng.shuffle(unseen)
    selected = unseen[: max(1, int(budget))]

    new_rows: List[Dict[str, Any]] = []
    for offset, candidate in enumerate(selected):
        result = evaluate_candidate_walk_forward(development, candidate, seed + offset * 17)
        row = serialize_experiment(candidate, result)
        experiments.append(row)
        new_rows.append(row)

    ranked = sorted(experiments, key=lambda item: float((item.get("walk_forward") or {}).get("objective", -999.0)), reverse=True)
    champion_row = ranked[0] if ranked else None
    baseline_development = {name: evaluate_exposure(development, exposure)["metrics"] for name, exposure in baseline_exposures(development).items()}
    holdout_baselines = {name: evaluate_exposure(holdout, exposure)["metrics"] for name, exposure in baseline_exposures(holdout).items()}

    champion_summary: Dict[str, Any] | None = None
    status = "searching"
    if champion_row:
        candidate = Candidate(**champion_row["candidate"])
        wf = champion_row["walk_forward"]
        robust = robustness_gate(wf["metrics"], wf.get("baseline_metrics") or baseline_development, wf["fold_metrics"])
        champion_summary = {"candidate_id": champion_row["candidate_id"], "candidate": champion_row["candidate"], "walk_forward": wf, "robustness_gate": robust}
        if robust["passed"]:
            holdout_result = train_and_evaluate_holdout(development, holdout, candidate, seed + 999)
            strongest_holdout_name, strongest_holdout_metrics = max(holdout_baselines.items(), key=lambda item: objective(item[1]))
            wow = wow_gate(holdout_result["metrics"], strongest_holdout_metrics)
            champion_summary.update({"holdout": holdout_result, "holdout_comparator": strongest_holdout_name, "wow_gate": wow})
            status = "wow_candidate" if wow["passed"] else "robust_but_not_wow"

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ledger["updated_at"] = now
    ledger["experiments"] = ranked[:2000]
    ledger["champion_candidate_id"] = champion_summary.get("candidate_id") if champion_summary else None
    write_json(ledger_path, ledger)

    report = {
        "schema_version": "1.0.0", "model": "BRACE-SPX Research Lab", "model_version": MODEL_VERSION,
        "status": status, "generated_at": now, "research_only": True, "live_activation": False,
        "target_instrument": TARGET_SYMBOL, "exogenous_features_only": sorted(set(prices.columns) - {TARGET_SYMBOL}),
        "data_start": frame.index.min().date().isoformat(), "data_end": frame.index.max().date().isoformat(),
        "development_end": development.index.max().date().isoformat(),
        "sealed_holdout_start": holdout.index.min().date().isoformat(), "sealed_holdout_end": holdout.index.max().date().isoformat(),
        "new_experiments": len(new_rows), "experiments_total": len(experiments), "candidate_space_size": len(pool),
        "development_baselines": baseline_development, "holdout_baselines": holdout_baselines,
        "champion": champion_summary, "top_candidates": ranked[:10],
        "governance": {
            "no_lookahead": True, "chronological_purged_folds": True, "single_traded_instrument": TARGET_SYMBOL,
            "transaction_cost_per_turnover": MONTHLY_COST, "no_leverage": True, "no_live_orders": True,
            "holdout_months": HOLDOUT_MONTHS, "wow_result_required": True, "promotion_requires_human_review": True,
        },
    }
    write_json(output_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--budget", type=int, default=24)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    parser.add_argument("--seed", type=int, default=RANDOM_SEED)
    args = parser.parse_args()
    prices = download_prices([*RICH_SYMBOLS.values(), *SECTOR_SYMBOLS], args.start)
    report = run_research(prices, args.budget, args.output, args.ledger, args.seed)
    champion = report.get("champion") or {}
    print(f"BRACE-SPX research complete: status={report['status']}, experiments={report['experiments_total']}, champion={champion.get('candidate_id', 'none')}")


if __name__ == "__main__":
    main()
