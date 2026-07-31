#!/usr/bin/env python3
"""Unsupervised orthogonality audit for BRACE-SPX Architecture 2 signals.

The audit uses development data only and never ranks sources by SPY returns,
Sharpe, CAGR or any holdout result. Its purpose is to identify at most four
sources with genuinely distinct information and to construct transparent
orthogonal residual factors for a later, separately predeclared experiment.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

import brace_spx_architecture_v2 as a2

SOURCES = ("trend", "breadth", "liquidity", "options", "rates")
MAX_SELECTED = 4
MIN_SELECTED = 2
MAX_PAIRWISE_CORRELATION = 0.75
MIN_UNIQUE_VARIANCE = 0.15


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _standardize(frame: pd.DataFrame) -> pd.DataFrame:
    centered = frame - frame.mean()
    scale = frame.std(ddof=1).replace(0.0, np.nan)
    return centered.div(scale).dropna()


def effective_rank(correlation: pd.DataFrame) -> float:
    eigenvalues = np.clip(np.linalg.eigvalsh(correlation.to_numpy(dtype=float)), 0.0, None)
    total = float(eigenvalues.sum())
    if total <= 1e-12:
        return 0.0
    weights = eigenvalues / total
    positive = weights[weights > 1e-12]
    return float(math.exp(-float(np.sum(positive * np.log(positive)))))


def unique_variance_fraction(frame: pd.DataFrame, source: str) -> float:
    y = frame[source].to_numpy(dtype=float)
    others = [column for column in frame.columns if column != source]
    if not others:
        return 1.0
    x = frame[others].to_numpy(dtype=float)
    x = np.column_stack([np.ones(len(x)), x])
    coefficients, *_ = np.linalg.lstsq(x, y, rcond=None)
    fitted = x @ coefficients
    residual = y - fitted
    denominator = float(np.var(y, ddof=1))
    if denominator <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, float(np.var(residual, ddof=1)) / denominator))


def select_sources(frame: pd.DataFrame) -> tuple[list[str], dict[str, str], dict[str, float]]:
    unique = {source: unique_variance_fraction(frame, source) for source in frame.columns}
    selected: list[str] = []
    excluded: dict[str, str] = {}

    first = max(frame.columns, key=lambda source: (unique[source], float(frame[source].std(ddof=1)), source))
    selected.append(first)

    remaining = set(frame.columns) - set(selected)
    while remaining and len(selected) < MAX_SELECTED:
        diagnostics: list[tuple[float, float, str]] = []
        for source in remaining:
            max_correlation = max(abs(float(frame[source].corr(frame[current]))) for current in selected)
            diagnostics.append((max_correlation, -unique[source], source))
        max_corr, _negative_unique, candidate = min(diagnostics)
        if max_corr > MAX_PAIRWISE_CORRELATION or unique[candidate] < MIN_UNIQUE_VARIANCE:
            break
        selected.append(candidate)
        remaining.remove(candidate)

    if len(selected) < MIN_SELECTED and remaining:
        candidate = min(
            remaining,
            key=lambda source: max(abs(float(frame[source].corr(frame[current]))) for current in selected),
        )
        selected.append(candidate)
        remaining.remove(candidate)

    for source in sorted(set(frame.columns) - set(selected)):
        max_corr = max(abs(float(frame[source].corr(frame[current]))) for current in selected)
        if unique[source] < MIN_UNIQUE_VARIANCE:
            excluded[source] = f"unique_variance_below_{MIN_UNIQUE_VARIANCE:.2f}"
        elif max_corr > MAX_PAIRWISE_CORRELATION:
            excluded[source] = f"correlation_to_selected_above_{MAX_PAIRWISE_CORRELATION:.2f}"
        else:
            excluded[source] = "selection_cap_reached"
    return selected, excluded, unique


def orthogonal_residual_factors(frame: pd.DataFrame, selected: list[str]) -> pd.DataFrame:
    factors = pd.DataFrame(index=frame.index)
    for position, source in enumerate(selected):
        y = frame[source].to_numpy(dtype=float)
        if position == 0:
            residual = y
        else:
            x = factors.to_numpy(dtype=float)
            x = np.column_stack([np.ones(len(x)), x])
            coefficients, *_ = np.linalg.lstsq(x, y, rcond=None)
            residual = y - x @ coefficients
        residual_series = pd.Series(residual, index=frame.index, dtype=float)
        standard_deviation = float(residual_series.std(ddof=1))
        if standard_deviation <= 1e-12:
            raise RuntimeError(f"Orthogonal factor for {source} is degenerate")
        factors[f"orth_{position + 1}_{source}"] = (residual_series - residual_series.mean()) / standard_deviation
    return factors


def audit(prices: pd.DataFrame) -> dict[str, Any]:
    if prices.index.max() >= pd.Timestamp(a2.SEALED_HOLDOUT_START):
        raise RuntimeError("Orthogonality audit received a sealed-holdout observation")
    features = a2.build_features(prices, research_mode=True)
    signals = a2.signal_frame(features).loc[:, list(SOURCES)]
    clean = _standardize(signals.dropna())
    if len(clean) < 750:
        raise RuntimeError("Insufficient complete development observations for orthogonality audit")

    correlation = clean.corr()
    selected, excluded, unique = select_sources(clean)
    orthogonal = orthogonal_residual_factors(clean, selected)
    orthogonal_correlation = orthogonal.corr()

    eigenvalues = np.linalg.eigvalsh(correlation.to_numpy(dtype=float))[::-1]
    explained = np.clip(eigenvalues, 0.0, None) / max(float(np.clip(eigenvalues, 0.0, None).sum()), 1e-12)
    cumulative = np.cumsum(explained)
    components_85 = int(np.searchsorted(cumulative, 0.85) + 1)
    components_90 = int(np.searchsorted(cumulative, 0.90) + 1)

    quality = {}
    original_signal_frame = a2.signal_frame(features).loc[:, list(SOURCES)]
    for source in SOURCES:
        series = original_signal_frame[source]
        quality[source] = {
            "coverage": round(float(series.notna().mean()), 6),
            "standard_deviation": round(float(series.dropna().std(ddof=1)), 6),
            "lag1_autocorrelation": round(float(series.dropna().autocorr(1)), 6),
            "unique_variance_fraction": round(float(unique[source]), 6),
        }

    off_diagonal = correlation.abs().to_numpy()[np.triu_indices(len(correlation), k=1)]
    orthogonal_off_diagonal = orthogonal_correlation.abs().to_numpy()[np.triu_indices(len(orthogonal_correlation), k=1)]
    report = {
        "schema_version": "1.0.0",
        "audit_id": "brace-spx-a2-signal-orthogonality-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "architecture_id": a2.ARCHITECTURE_ID,
        "candidate_signature_unchanged": a2.candidate_signature(),
        "method": {
            "supervised_by_market_returns": False,
            "uses_sharpe_or_cagr_for_source_selection": False,
            "uses_holdout": False,
            "source_selection": "greedy minimum-correlation with unique-variance floor",
            "orthogonalization": "sequential OLS residualization in selected diversity order",
            "maximum_selected_sources": MAX_SELECTED,
        },
        "development": {
            "start": clean.index.min().date().isoformat(),
            "end": clean.index.max().date().isoformat(),
            "observations": int(len(clean)),
        },
        "holdout": {
            "start": a2.SEALED_HOLDOUT_START,
            "end": a2.SEALED_HOLDOUT_END,
            "status": "sealed_not_downloaded",
            "accessed": False,
            "access_count": 0,
        },
        "raw_signal_diagnostics": {
            "sources": list(SOURCES),
            "correlation_matrix": {
                row: {column: round(float(correlation.loc[row, column]), 6) for column in correlation.columns}
                for row in correlation.index
            },
            "median_absolute_pairwise_correlation": round(float(np.median(off_diagonal)), 6),
            "mean_absolute_pairwise_correlation": round(float(np.mean(off_diagonal)), 6),
            "effective_rank": round(effective_rank(correlation), 6),
            "principal_components_for_85pct_variance": components_85,
            "principal_components_for_90pct_variance": components_90,
            "data_quality": quality,
        },
        "recommendation": {
            "selected_raw_sources": selected,
            "selected_count": len(selected),
            "excluded_sources": excluded,
            "next_experiment_candidate_limit": min(4, len(selected)),
            "do_not_select_by_backtest_performance": True,
            "requires_new_predeclared_signature_before_any_backtest": True,
        },
        "orthogonal_factor_diagnostics": {
            "factor_names": list(orthogonal.columns),
            "maximum_absolute_pairwise_correlation": round(float(np.max(orthogonal_off_diagonal)) if len(orthogonal_off_diagonal) else 0.0, 10),
            "correlation_matrix": {
                row: {column: round(float(orthogonal_correlation.loc[row, column]), 10) for column in orthogonal_correlation.columns}
                for row in orthogonal_correlation.index
            },
        },
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prices-csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prices = a2.load_prices_csv(args.prices_csv) if args.prices_csv else a2.download_prices()
    report = audit(prices)
    _write(args.output, report)
    print(
        "Signal orthogonality audit: "
        f"selected={report['recommendation']['selected_raw_sources']} "
        f"effective_rank={report['raw_signal_diagnostics']['effective_rank']}"
    )


if __name__ == "__main__":
    main()
