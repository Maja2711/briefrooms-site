#!/usr/bin/env python3
"""Walk-forward historical validation for the BRACE challenger.

The historical test is deliberately BRACE-Lite: it uses only point-in-time price
and market-regime information. Current fundamental fields are never injected
into historical dates. The purpose is to test the decision architecture and
risk controls, not to manufacture a flattering hindsight result.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_PATH = ROOT / "data" / "investments" / "portfolio_10k.json"
OUTPUT_PATH = ROOT / "data" / "investments" / "portfolio_10k_brace_backtest.json"


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def score_linear(value: float, bad: float, good: float) -> float:
    if not math.isfinite(float(value)) or good == bad:
        return 50.0
    return clamp(100.0 * (float(value) - bad) / (good - bad))


def weekly_prices(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return daily
    frame = daily.copy()
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    return frame.resample("W-FRI").last().dropna(how="all")


def feature_frames(prices: pd.DataFrame, benchmark: pd.Series) -> Dict[str, pd.DataFrame | pd.Series]:
    returns = prices.pct_change()
    mom_26 = prices / prices.shift(26) - 1.0
    mom_52 = prices / prices.shift(52) - 1.0
    ma40 = prices.rolling(40, min_periods=30).mean()
    price_ma40 = prices / ma40 - 1.0
    vol13 = returns.rolling(13, min_periods=10).std(ddof=1) * math.sqrt(52)
    rolling_high = prices.rolling(52, min_periods=26).max()
    drawdown = prices / rolling_high - 1.0
    bench_mom26 = benchmark / benchmark.shift(26) - 1.0
    relative_26 = mom_26.sub(bench_mom26, axis=0)
    bench_ma40 = benchmark / benchmark.rolling(40, min_periods=30).mean() - 1.0
    bench_vol13 = benchmark.pct_change().rolling(13, min_periods=10).std(ddof=1) * math.sqrt(52)
    breadth = (prices > ma40).mean(axis=1)
    return {
        "returns": returns,
        "mom_26": mom_26,
        "mom_52": mom_52,
        "price_ma40": price_ma40,
        "vol13": vol13,
        "drawdown": drawdown,
        "relative_26": relative_26,
        "bench_mom26": bench_mom26,
        "bench_ma40": bench_ma40,
        "bench_vol13": bench_vol13,
        "breadth": breadth,
    }


def brace_lite_scores(features: Mapping[str, Any], when: pd.Timestamp) -> pd.Series:
    symbols = features["mom_26"].columns
    context = np.nanmean([
        score_linear(features["bench_mom26"].get(when, np.nan), -0.18, 0.20),
        score_linear(features["bench_ma40"].get(when, np.nan), -0.20, 0.15),
        score_linear(features["bench_vol13"].get(when, np.nan), 0.45, 0.10),
        score_linear(features["breadth"].get(when, np.nan), 0.25, 0.80),
    ])
    scores: Dict[str, float] = {}
    for symbol in symbols:
        confirmation = np.nanmean([
            score_linear(features["mom_26"].at[when, symbol], -0.30, 0.35),
            score_linear(features["mom_52"].at[when, symbol], -0.40, 0.55),
            score_linear(features["price_ma40"].at[when, symbol], -0.25, 0.25),
            score_linear(features["relative_26"].at[when, symbol], -0.25, 0.25),
        ])
        risk = np.nanmean([
            score_linear(features["vol13"].at[when, symbol], 0.75, 0.12),
            score_linear(features["drawdown"].at[when, symbol], -0.55, 0.0),
        ])
        contradiction = 8.0 if confirmation >= 72 and risk <= 35 else 0.0
        scores[symbol] = clamp(0.45 * confirmation + 0.30 * risk + 0.25 * context - contradiction)
    return pd.Series(scores, dtype=float)


def baseline_scores(features: Mapping[str, Any], when: pd.Timestamp) -> pd.Series:
    scores: Dict[str, float] = {}
    for symbol in features["mom_26"].columns:
        points = 50.0
        points += 10 if features["price_ma40"].at[when, symbol] >= 0 else -10
        points += 10 if features["mom_26"].at[when, symbol] >= 0 else -10
        points += 5 if features["drawdown"].at[when, symbol] > -0.20 else -5
        points += 5 if features["vol13"].at[when, symbol] <= 0.35 else -5
        scores[symbol] = clamp(points)
    return pd.Series(scores, dtype=float)


def weights_from_scores(target: pd.Series, scores: pd.Series, variant: str = "standard") -> pd.Series:
    if variant == "conservative":
        multipliers = scores.map(lambda x: 1.10 if x >= 75 else 1.0 if x >= 58 else 0.65 if x >= 43 else 0.25)
    elif variant == "aggressive":
        multipliers = scores.map(lambda x: 1.35 if x >= 68 else 1.0 if x >= 52 else 0.50 if x >= 38 else 0.10)
    else:
        multipliers = scores.map(lambda x: 1.20 if x >= 70 else 1.0 if x >= 55 else 0.65 if x >= 40 else 0.25)
    raw = target.reindex(scores.index).fillna(0.0) * multipliers
    total = raw.sum()
    return raw / total if total > 1.0 else raw


def baseline_weights(target: pd.Series, scores: pd.Series) -> pd.Series:
    multipliers = scores.map(lambda x: 1.10 if x >= 75 else 1.0 if x >= 50 else 0.50)
    raw = target.reindex(scores.index).fillna(0.0) * multipliers
    total = raw.sum()
    return raw / total if total > 1.0 else raw


@dataclass
class StrategyResult:
    returns: pd.Series
    equity: pd.Series
    turnover: pd.Series
    weights: pd.DataFrame


def run_strategy(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    target: pd.Series,
    mode: str,
    rebalance_every: int = 4,
    transaction_cost: float = 0.0025,
    variant: str = "standard",
) -> StrategyResult:
    features = feature_frames(prices, benchmark)
    asset_returns = features["returns"]
    valid_dates = prices.index[prices.notna().all(axis=1)]
    if len(valid_dates) < 60:
        raise ValueError("Insufficient aligned history")
    start_index = 52
    current_weights = pd.Series(0.0, index=prices.columns)
    returns_out: Dict[pd.Timestamp, float] = {}
    turnover_out: Dict[pd.Timestamp, float] = {}
    weights_out: Dict[pd.Timestamp, pd.Series] = {}

    for i in range(start_index, len(valid_dates) - 1):
        when = valid_dates[i]
        next_when = valid_dates[i + 1]
        rebalance = (i - start_index) % rebalance_every == 0 or current_weights.sum() == 0
        cost = 0.0
        if rebalance:
            if mode == "buy_hold":
                desired = target.reindex(prices.columns).fillna(0.0)
                desired = desired / desired.sum()
            elif mode == "baseline":
                desired = baseline_weights(target, baseline_scores(features, when))
            elif mode == "brace":
                desired = weights_from_scores(target, brace_lite_scores(features, when), variant)
            else:
                raise ValueError(f"Unknown mode: {mode}")
            turnover = float((desired - current_weights).abs().sum())
            cost = turnover * transaction_cost
            current_weights = desired
        else:
            turnover = 0.0

        next_return = asset_returns.loc[next_when].reindex(prices.columns).fillna(0.0)
        portfolio_return = float((current_weights * next_return).sum()) - cost
        returns_out[next_when] = portfolio_return
        turnover_out[next_when] = turnover
        weights_out[next_when] = current_weights.copy()

    returns_series = pd.Series(returns_out, dtype=float).sort_index()
    return StrategyResult(
        returns=returns_series,
        equity=(1.0 + returns_series).cumprod(),
        turnover=pd.Series(turnover_out, dtype=float).sort_index(),
        weights=pd.DataFrame(weights_out).T.sort_index(),
    )


def metrics(result: StrategyResult) -> Dict[str, float]:
    returns = result.returns.dropna()
    if returns.empty:
        return {}
    years = max(len(returns) / 52.0, 1 / 52.0)
    total_return = float((1.0 + returns).prod() - 1.0)
    cagr = float((1.0 + total_return) ** (1.0 / years) - 1.0)
    volatility = float(returns.std(ddof=1) * math.sqrt(52))
    downside = returns[returns < 0]
    downside_vol = float(downside.std(ddof=1) * math.sqrt(52)) if len(downside) > 1 else 0.0
    sharpe = cagr / volatility if volatility > 0 else 0.0
    sortino = cagr / downside_vol if downside_vol > 0 else 0.0
    equity = result.equity
    drawdown = equity / equity.cummax() - 1.0
    max_drawdown = float(drawdown.min())
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else 0.0
    return {
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 6),
        "annualized_volatility": round(volatility, 6),
        "sharpe_zero_rf": round(sharpe, 4),
        "sortino_zero_rf": round(sortino, 4),
        "max_drawdown": round(max_drawdown, 6),
        "calmar": round(calmar, 4),
        "annualized_turnover": round(float(result.turnover.mean() * 52), 4),
        "weeks": int(len(returns)),
    }


def benchmark_result(benchmark: pd.Series, index: pd.Index) -> StrategyResult:
    returns = benchmark.pct_change().reindex(index).fillna(0.0)
    return StrategyResult(
        returns=returns,
        equity=(1.0 + returns).cumprod(),
        turnover=pd.Series(0.0, index=returns.index),
        weights=pd.DataFrame(index=returns.index),
    )


def download_history(symbols: Iterable[str], start: str) -> pd.DataFrame:
    import yfinance as yf

    symbols = list(dict.fromkeys(symbols))
    data = yf.download(symbols, start=start, auto_adjust=True, progress=False, group_by="column")
    if data.empty:
        raise RuntimeError("No historical prices downloaded")
    if isinstance(data.columns, pd.MultiIndex):
        if "Close" in data.columns.get_level_values(0):
            close = data["Close"]
        elif "Close" in data.columns.get_level_values(1):
            close = data.xs("Close", axis=1, level=1)
        else:
            raise RuntimeError("Historical response has no Close field")
    else:
        close = data[["Close"]].rename(columns={"Close": symbols[0]})
    return close.astype(float)


def build_backtest(portfolio: Mapping[str, Any], start: str = "2016-01-01") -> Dict[str, Any]:
    target = pd.Series({str(p["market_symbol"]): float(p["target_weight"]) for p in portfolio.get("positions", [])})
    benchmark_symbol = str(portfolio.get("benchmark", {}).get("market_symbol") or "FWIA.DE")
    downloaded = download_history([*target.index, benchmark_symbol], start)
    weekly = weekly_prices(downloaded)
    available_assets = [symbol for symbol in target.index if symbol in weekly.columns]
    prices = weekly[available_assets].dropna(how="any")
    benchmark = weekly[benchmark_symbol].reindex(prices.index).ffill().dropna()
    prices = prices.reindex(benchmark.index).dropna(how="any")
    benchmark = benchmark.reindex(prices.index)
    target = target.reindex(prices.columns).fillna(0.0)
    target = target / target.sum()

    results = {
        "buy_hold": run_strategy(prices, benchmark, target, "buy_hold"),
        "baseline": run_strategy(prices, benchmark, target, "baseline"),
        "brace_standard": run_strategy(prices, benchmark, target, "brace", variant="standard"),
        "brace_conservative": run_strategy(prices, benchmark, target, "brace", variant="conservative"),
        "brace_aggressive": run_strategy(prices, benchmark, target, "brace", variant="aggressive"),
    }
    common_index = results["buy_hold"].returns.index
    bench = benchmark_result(benchmark, common_index)
    output_metrics = {name: metrics(result) for name, result in results.items()}
    output_metrics["benchmark"] = metrics(bench)

    standard_cagr = output_metrics["brace_standard"].get("cagr", 0.0)
    variant_cagrs = [output_metrics[name].get("cagr", 0.0) for name in ("brace_conservative", "brace_standard", "brace_aggressive")]
    robust = max(variant_cagrs) - min(variant_cagrs) <= 0.05
    return {
        "schema_version": "1.0.0",
        "model": "BRACE-Lite walk-forward validation",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "start_date": common_index.min().date().isoformat(),
        "end_date": common_index.max().date().isoformat(),
        "symbols": available_assets,
        "benchmark_symbol": benchmark_symbol,
        "assumptions": {
            "rebalance_frequency_weeks": 4,
            "transaction_cost_per_turnover": 0.0025,
            "lookahead": False,
            "fundamentals_in_backtest": False,
            "cash_return": 0.0,
            "survivorship_limitation": "The current portfolio universe is tested; delisted historical candidates are not reconstructed.",
        },
        "metrics": output_metrics,
        "robustness": {
            "parameter_variants": ["conservative", "standard", "aggressive"],
            "cagr_range": [round(min(variant_cagrs), 6), round(max(variant_cagrs), 6)],
            "stable_within_five_percentage_points": robust,
            "standard_cagr": round(standard_cagr, 6),
        },
        "equity_tail": {
            name: [
                {"date": index.date().isoformat(), "value": round(float(value), 6)}
                for index, value in result.equity.tail(104).items()
            ]
            for name, result in {**results, "benchmark": bench}.items()
        },
        "methodology_pl": (
            "Test walk-forward używa wyłącznie informacji cenowej dostępnej do danego tygodnia. "
            "Pełne fundamenty historyczne point-in-time nie są zastępowane dzisiejszymi danymi; "
            "dlatego test nazywa się BRACE-Lite. Wagi z tygodnia t obowiązują dopiero dla zwrotu t→t+1."
        ),
        "methodology_en": (
            "The walk-forward test uses only price information available by each week. "
            "Current fundamentals are never backfilled into history, so the test is labelled BRACE-Lite. "
            "Weights formed at week t apply only to the t→t+1 return."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio", type=Path, default=PORTFOLIO_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--start", default="2016-01-01")
    args = parser.parse_args()
    portfolio = json.loads(args.portfolio.read_text(encoding="utf-8"))
    output = build_backtest(portfolio, args.start)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "BRACE-Lite backtest updated: "
        f"CAGR={output['metrics']['brace_standard']['cagr']:.2%}, "
        f"maxDD={output['metrics']['brace_standard']['max_drawdown']:.2%}"
    )


if __name__ == "__main__":
    main()
