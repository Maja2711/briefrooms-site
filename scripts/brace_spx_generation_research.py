#!/usr/bin/env python3
"""BRACE-SPX sealed-generation research runner.

This runner deliberately never evaluates the final holdout during routine search.
Each generation is predeclared, its candidate universe is immutable, and only
chronological development folds are used for ranking. It adds conventional
excess-return Sharpe, Deflated Sharpe Ratio (DSR) and a CSCV-style Probability
of Backtest Overfitting (PBO) diagnostic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd

import brace_spx_research as base

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "research" / "brace_spx_generation_research.json"
LEDGER_PATH = ROOT / "data" / "research" / "brace_spx_generation_experiments.json"
MANIFEST_PATH = ROOT / "data" / "research" / "brace_spx_generation_manifest.json"
GENERATION_ID = "spx-sealed-v1"
RISK_FREE_SYMBOL = "^IRX"
PBO_BLOCKS = 8


def read_json(path: Path, default: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default)
    except (OSError, json.JSONDecodeError):
        return dict(default)


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def monthly_risk_free(prices: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    if RISK_FREE_SYMBOL not in prices:
        return pd.Series(0.0, index=index, dtype=float)
    annual_yield = prices[RISK_FREE_SYMBOL].astype(float).resample("ME").last().reindex(index).ffill()
    # ^IRX is quoted in annual percentage points. Convert to an effective monthly return.
    return ((1.0 + annual_yield.clip(lower=0.0) / 100.0) ** (1.0 / 12.0) - 1.0).fillna(0.0)


def excess_metrics(returns: pd.Series, risk_free: pd.Series, turnover: pd.Series) -> Dict[str, float]:
    aligned = pd.concat(
        [returns.rename("return"), risk_free.rename("rf"), turnover.rename("turnover")], axis=1
    ).dropna(subset=["return", "rf"])
    if aligned.empty:
        return {}
    metrics = base.annualized_metrics(aligned["return"], aligned["turnover"])
    excess = aligned["return"] - aligned["rf"]
    monthly_std = float(excess.std(ddof=1)) if len(excess) > 1 else 0.0
    sharpe = float(excess.mean() / monthly_std * math.sqrt(12.0)) if monthly_std > 0.0 else 0.0
    metrics["sharpe_excess"] = round(sharpe, 6)
    metrics["mean_monthly_excess"] = round(float(excess.mean()), 8)
    metrics["risk_free_source"] = RISK_FREE_SYMBOL if float(aligned["rf"].abs().sum()) > 0 else "zero_fallback"
    return metrics


def skew_kurtosis(values: pd.Series) -> Tuple[float, float]:
    clean = values.dropna().astype(float)
    if len(clean) < 4:
        return 0.0, 3.0
    return float(clean.skew()), float(clean.kurt() + 3.0)


def deflated_sharpe_ratio(
    observed_sharpe: float,
    returns: pd.Series,
    trials: int,
    sharpe_std: float,
) -> Dict[str, float]:
    """Bailey-Lopez de Prado style DSR approximation.

    The expected maximum Sharpe under multiple testing is approximated from the
    cross-sectional standard deviation of tested Sharpes and the number of trials.
    """
    n = int(returns.dropna().shape[0])
    if n < 3:
        return {"probability": 0.0, "expected_max_sharpe": 0.0, "z_score": 0.0}
    trial_count = max(2, int(trials))
    gamma = 0.5772156649015329
    z1 = math.sqrt(max(0.0, 2.0 * math.log(trial_count)))
    z2 = z1 - (math.log(max(math.log(trial_count), 1e-12)) + math.log(4.0 * math.pi)) / max(2.0 * z1, 1e-12)
    expected_max = float(sharpe_std) * ((1.0 - gamma) * z2 + gamma * z1)
    skew, kurt = skew_kurtosis(returns)
    denominator_sq = max(1e-12, 1.0 - skew * observed_sharpe + ((kurt - 1.0) / 4.0) * observed_sharpe**2)
    z_score = (observed_sharpe - expected_max) * math.sqrt(max(1.0, n - 1.0)) / math.sqrt(denominator_sq)
    return {
        "probability": round(normal_cdf(z_score), 6),
        "expected_max_sharpe": round(expected_max, 6),
        "z_score": round(z_score, 6),
        "observations": n,
        "trials": trial_count,
    }


def probability_backtest_overfitting(return_matrix: pd.DataFrame, blocks: int = PBO_BLOCKS) -> Dict[str, Any]:
    clean = return_matrix.dropna(axis=1, how="all").dropna(axis=0, how="all")
    if clean.shape[1] < 2 or clean.shape[0] < blocks * 3 or blocks % 2:
        return {"available": False, "probability": None, "splits": 0, "reason": "insufficient_matrix"}
    positions = np.array_split(np.arange(clean.shape[0]), blocks)
    half = blocks // 2
    logits: List[float] = []
    for train_blocks in combinations(range(blocks), half):
        # Complementary partitions are symmetric; retain one representative.
        if 0 not in train_blocks:
            continue
        train_idx = np.concatenate([positions[i] for i in train_blocks])
        test_idx = np.concatenate([positions[i] for i in range(blocks) if i not in train_blocks])
        train = clean.iloc[train_idx]
        test = clean.iloc[test_idx]
        train_std = train.std(ddof=1).replace(0.0, np.nan)
        train_sharpe = train.mean().div(train_std) * math.sqrt(12.0)
        if train_sharpe.dropna().empty:
            continue
        winner = str(train_sharpe.idxmax())
        test_std = test.std(ddof=1).replace(0.0, np.nan)
        test_sharpe = (test.mean().div(test_std) * math.sqrt(12.0)).dropna().sort_values()
        if winner not in test_sharpe.index or len(test_sharpe) < 2:
            continue
        rank = int(test_sharpe.index.get_loc(winner)) + 1
        percentile = min(1.0 - 1e-9, max(1e-9, rank / (len(test_sharpe) + 1.0)))
        logits.append(math.log(percentile / (1.0 - percentile)))
    if not logits:
        return {"available": False, "probability": None, "splits": 0, "reason": "no_valid_splits"}
    probability = float(np.mean(np.asarray(logits) <= 0.0))
    return {
        "available": True,
        "probability": round(probability, 6),
        "splits": len(logits),
        "median_logit": round(float(np.median(logits)), 6),
        "interpretation": "lower_is_better",
    }


def generation_signature(candidates: Sequence[base.Candidate]) -> str:
    payload = [candidate.candidate_id() for candidate in candidates]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()


def ensure_manifest(candidates: Sequence[base.Candidate], development_end: str, holdout_start: str, holdout_end: str) -> Dict[str, Any]:
    signature = generation_signature(candidates)
    existing = read_json(MANIFEST_PATH, {})
    if existing:
        immutable = (
            existing.get("generation_id") == GENERATION_ID
            and existing.get("candidate_signature") == signature
            and existing.get("holdout", {}).get("start") == holdout_start
            and existing.get("holdout", {}).get("end") == holdout_end
        )
        if not immutable:
            raise RuntimeError("Generation manifest mismatch; refusing to mutate a predeclared generation")
        return existing
    manifest = {
        "schema_version": "1.0.0",
        "generation_id": GENERATION_ID,
        "declared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidate_signature": signature,
        "candidate_space_size": len(candidates),
        "development_end": development_end,
        "holdout": {
            "start": holdout_start,
            "end": holdout_end,
            "months": base.HOLDOUT_MONTHS,
            "status": "sealed",
            "accessed": False,
            "access_count": 0,
        },
        "promotion_policy": {
            "candidate_space_must_be_exhausted": True,
            "champion_hash_must_be_frozen_before_holdout": True,
            "workflow_can_open_holdout": False,
            "human_review_required": True,
        },
    }
    write_json(MANIFEST_PATH, manifest)
    return manifest


def candidate_returns(
    development: pd.DataFrame,
    candidate: base.Candidate,
    risk_free: pd.Series,
    seed: int,
) -> Dict[str, Any]:
    folds = base.chronological_folds(development.index)
    probability_parts: List[pd.Series] = []
    fold_metrics: List[Dict[str, Any]] = []
    for fold_number, (train_idx, valid_idx) in enumerate(folds):
        predicted = base.fit_predict_candidate(development, candidate, train_idx, valid_idx, seed + fold_number)
        valid = development.loc[predicted.index]
        exposure = base.probabilities_to_exposure(predicted, valid["realized_vol_20"], candidate)
        returns, turnover = base.strategy_returns(valid["asset_return"], exposure)
        fold_metrics.append(excess_metrics(returns, risk_free.reindex(valid.index).fillna(0.0), turnover))
        probability_parts.append(predicted)
    joined = pd.concat(probability_parts).sort_index()
    valid = development.loc[joined.index]
    exposure = base.probabilities_to_exposure(joined, valid["realized_vol_20"], candidate)
    returns, turnover = base.strategy_returns(valid["asset_return"], exposure)
    metrics = excess_metrics(returns, risk_free.reindex(valid.index).fillna(0.0), turnover)
    return {
        "metrics": metrics,
        "fold_metrics": fold_metrics,
        "returns": returns,
        "months": int(len(returns)),
    }


def run(prices: pd.DataFrame, budget: int, seed: int = base.RANDOM_SEED) -> Dict[str, Any]:
    frame = base.monthly_dataset(prices)
    development, holdout = base.holdout_split(frame)
    pool = base.candidate_pool()
    manifest = ensure_manifest(
        pool,
        development.index.max().date().isoformat(),
        holdout.index.min().date().isoformat(),
        holdout.index.max().date().isoformat(),
    )
    if manifest["holdout"].get("accessed"):
        raise RuntimeError("Final holdout has already been accessed; generation is closed")

    ledger = read_json(LEDGER_PATH, {
        "schema_version": "1.0.0",
        "generation_id": GENERATION_ID,
        "candidate_signature": manifest["candidate_signature"],
        "experiments": [],
    })
    if ledger.get("candidate_signature") != manifest["candidate_signature"]:
        raise RuntimeError("Ledger does not match immutable generation manifest")
    experiments = ledger.setdefault("experiments", [])
    seen = {str(row.get("candidate_id")) for row in experiments}
    unseen = [candidate for candidate in pool if candidate.candidate_id() not in seen]
    selected = unseen[: max(0, int(budget))]
    risk_free = monthly_risk_free(prices, development.index)

    for offset, candidate in enumerate(selected):
        result = candidate_returns(development, candidate, risk_free, seed + len(experiments) * 31 + offset * 17)
        experiments.append({
            "candidate_id": candidate.candidate_id(),
            "candidate": base.asdict(candidate),
            "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "metrics": result["metrics"],
            "fold_metrics": result["fold_metrics"],
            "months": result["months"],
            "monthly_returns": [
                {"date": idx.date().isoformat(), "return": round(float(value), 10)}
                for idx, value in result["returns"].items()
            ],
        })

    ranked = sorted(experiments, key=lambda row: float(row.get("metrics", {}).get("sharpe_excess", -999.0)), reverse=True)
    sharpe_values = pd.Series([float(row.get("metrics", {}).get("sharpe_excess", 0.0)) for row in ranked], dtype=float)
    return_series: Dict[str, pd.Series] = {}
    for row in ranked:
        series = pd.Series(
            {pd.Timestamp(item["date"]): float(item["return"]) for item in row.get("monthly_returns", [])},
            dtype=float,
        )
        return_series[str(row["candidate_id"])] = series
    matrix = pd.DataFrame(return_series).sort_index()
    pbo = probability_backtest_overfitting(matrix)

    champion = ranked[0] if ranked else None
    dsr: Dict[str, Any] | None = None
    if champion:
        champion_returns = return_series[str(champion["candidate_id"])]
        dsr = deflated_sharpe_ratio(
            float(champion["metrics"].get("sharpe_excess", 0.0)),
            champion_returns,
            len(ranked),
            float(sharpe_values.std(ddof=1)) if len(sharpe_values) > 1 else 0.0,
        )

    remaining = len(pool) - len(ranked)
    status = "development_search"
    frozen_champion_hash = None
    if remaining == 0 and champion:
        status = "generation_exhausted_holdout_still_sealed"
        frozen_champion_hash = hashlib.sha256(
            json.dumps(champion["candidate"], sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        manifest["frozen_champion"] = {
            "candidate_id": champion["candidate_id"],
            "candidate_hash": frozen_champion_hash,
            "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        write_json(MANIFEST_PATH, manifest)

    ledger["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ledger["experiments"] = ranked
    write_json(LEDGER_PATH, ledger)
    report = {
        "schema_version": "2.0.0",
        "generation_id": GENERATION_ID,
        "status": status,
        "generated_at": ledger["updated_at"],
        "research_only": True,
        "live_activation": False,
        "candidate_space_size": len(pool),
        "experiments_total": len(ranked),
        "experiments_remaining": remaining,
        "new_experiments": len(selected),
        "development_end": development.index.max().date().isoformat(),
        "holdout": manifest["holdout"],
        "champion": None if not champion else {
            "candidate_id": champion["candidate_id"],
            "metrics": champion["metrics"],
            "fold_metrics": champion["fold_metrics"],
            "deflated_sharpe_ratio": dsr,
            "candidate_hash": frozen_champion_hash,
        },
        "multiple_testing": {
            "trials_evaluated": len(ranked),
            "sharpe_cross_section_std": round(float(sharpe_values.std(ddof=1)), 6) if len(sharpe_values) > 1 else 0.0,
            "pbo": pbo,
        },
        "governance": {
            "chronological_purged_folds": True,
            "conventional_excess_return_sharpe": True,
            "risk_free_source": RISK_FREE_SYMBOL,
            "single_use_holdout": True,
            "holdout_opened_by_workflow": False,
            "candidate_universe_predeclared": True,
            "no_live_orders": True,
            "no_leverage": True,
            "human_review_required": True,
        },
    }
    write_json(OUTPUT_PATH, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=base.DEFAULT_START)
    parser.add_argument("--budget", type=int, default=36)
    parser.add_argument("--seed", type=int, default=base.RANDOM_SEED)
    args = parser.parse_args()
    symbols: Iterable[str] = [*base.RICH_SYMBOLS.values(), *base.SECTOR_SYMBOLS, RISK_FREE_SYMBOL]
    prices = base.download_prices(symbols, args.start)
    report = run(prices, args.budget, args.seed)
    print(
        f"BRACE-SPX sealed generation: status={report['status']} "
        f"experiments={report['experiments_total']}/{report['candidate_space_size']}"
    )


if __name__ == "__main__":
    main()
