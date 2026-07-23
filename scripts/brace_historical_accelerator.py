#!/usr/bin/env python3
"""Accelerate BRACE learning with purged walk-forward historical lessons.

Only point-in-time price-derived evidence that can be reconstructed honestly is
used. Candidate learning strength is selected on a chronological calibration
period and accepted only after progress on a later untouched test period.
Accepted lessons seed bounded Bayesian evidence reliability in BRACE memory;
they never change pillar weights, decision thresholds or place orders.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import portfolio_10k_brace_backtest as bt
import portfolio_10k_brace_learning as learning

PORTFOLIO_PATH = ROOT / "data" / "investments" / "portfolio_10k.json"
MEMORY_PATH = ROOT / "data" / "investments" / "portfolio_10k_brace_memory.json"
OUTPUT_PATH = ROOT / "data" / "investments" / "portfolio_10k_brace_historical_learning.json"
VERSION = "1.0.0"
HORIZONS = (4, 13, 26, 52)
HORIZON_WEIGHTS = {4: 0.35, 13: 0.70, 26: 1.0, 52: 1.0}
SIGNAL_QUALITY = {
    "price_vs_ma200": 0.75,
    "relative_strength_6m": 0.75,
    "drawdown_52w": 0.80,
}
HISTORICAL_CREDIT_CAP = 24.0
MIN_EFFECTIVE_SAMPLE = 8.0
TRANSACTION_COST = 0.0025


@dataclass(frozen=True)
class Lesson:
    code: str
    symbol: str
    observed_at: pd.Timestamp
    outcome_at: pd.Timestamp
    horizon_weeks: int
    direction: int
    strength: float
    quality: float
    excess_return: float
    correct: bool
    credit: float


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def signal_values(features: Mapping[str, Any], when: pd.Timestamp, symbol: str) -> Dict[str, Tuple[int, float, float]]:
    output: Dict[str, Tuple[int, float, float]] = {}

    ma = number(features["price_ma40"].at[when, symbol], float("nan"))
    if math.isfinite(ma):
        output["price_vs_ma200"] = (
            1 if ma >= 0 else -1,
            min(1.0, abs(ma) / 0.25),
            SIGNAL_QUALITY["price_vs_ma200"],
        )

    relative = number(features["relative_26"].at[when, symbol], float("nan"))
    if math.isfinite(relative):
        output["relative_strength_6m"] = (
            1 if relative >= 0 else -1,
            min(1.0, abs(relative) / 0.20),
            SIGNAL_QUALITY["relative_strength_6m"],
        )

    drawdown = number(features["drawdown"].at[when, symbol], float("nan"))
    if math.isfinite(drawdown):
        direction = -1 if drawdown <= -0.25 else 1 if drawdown > -0.10 else 0
        if direction:
            output["drawdown_52w"] = (
                direction,
                min(1.0, abs(drawdown) / 0.45),
                SIGNAL_QUALITY["drawdown_52w"],
            )
    return output


def build_lessons(prices: pd.DataFrame, benchmark: pd.Series) -> List[Lesson]:
    features = bt.feature_frames(prices, benchmark)
    lessons: List[Lesson] = []
    index = prices.index
    max_horizon = max(HORIZONS)
    for symbol in prices.columns:
        for i in range(52, len(index) - max_horizon):
            when = index[i]
            price_now = number(prices.at[when, symbol], float("nan"))
            benchmark_now = number(benchmark.at[when], float("nan"))
            if not math.isfinite(price_now) or not math.isfinite(benchmark_now) or min(price_now, benchmark_now) <= 0:
                continue
            signals = signal_values(features, when, symbol)
            if not signals:
                continue
            for horizon in HORIZONS:
                outcome_at = index[i + horizon]
                price_future = number(prices.at[outcome_at, symbol], float("nan"))
                benchmark_future = number(benchmark.at[outcome_at], float("nan"))
                if not math.isfinite(price_future) or not math.isfinite(benchmark_future) or min(price_future, benchmark_future) <= 0:
                    continue
                excess = price_future / price_now - benchmark_future / benchmark_now
                deadband = 0.005 * math.sqrt(horizon / 4.0)
                for code, (direction, strength, quality) in signals.items():
                    if direction == 0 or strength <= 0:
                        continue
                    neutral = abs(excess) < deadband
                    if neutral:
                        continue
                    credit = strength * quality * HORIZON_WEIGHTS[horizon]
                    lessons.append(Lesson(
                        code=code,
                        symbol=symbol,
                        observed_at=when,
                        outcome_at=outcome_at,
                        horizon_weeks=horizon,
                        direction=direction,
                        strength=strength,
                        quality=quality,
                        excess_return=excess,
                        correct=bool(direction * excess > 0),
                        credit=credit,
                    ))
    return lessons


def fit_reliability(
    lessons: Iterable[Lesson],
    cutoff: pd.Timestamp,
    prior_strength: float,
    cap: float,
) -> Dict[str, Dict[str, Any]]:
    buckets: Dict[str, List[float]] = {}
    for item in lessons:
        if item.outcome_at > cutoff:
            continue
        success, failure = buckets.setdefault(item.code, [0.0, 0.0])
        if item.correct:
            success += item.credit
        else:
            failure += item.credit
        buckets[item.code] = [success, failure]

    output: Dict[str, Dict[str, Any]] = {}
    for code, (success, failure) in sorted(buckets.items()):
        effective = success + failure
        alpha = prior_strength / 2.0 + success
        beta = prior_strength / 2.0 + failure
        mean = alpha / max(alpha + beta, 1e-12)
        multiplier = 1.0 if effective < MIN_EFFECTIVE_SAMPLE else 1.0 + (mean - 0.5) * 2.0 * cap
        output[code] = {
            "success_credit": round(success, 6),
            "failure_credit": round(failure, 6),
            "effective_samples": round(effective, 6),
            "posterior_mean": round(mean, 6),
            "multiplier": round(max(1.0 - cap, min(1.0 + cap, multiplier)), 6),
            "active": effective >= MIN_EFFECTIVE_SAMPLE,
        }
    return output


def multipliers(stats: Mapping[str, Mapping[str, Any]]) -> Dict[str, float]:
    return {
        code: number(item.get("multiplier"), 1.0) if item.get("active") else 1.0
        for code, item in stats.items()
    }


def adaptive_scores(features: Mapping[str, Any], when: pd.Timestamp, learned: Mapping[str, float]) -> pd.Series:
    symbols = features["mom_26"].columns
    context = np.nanmean([
        bt.score_linear(features["bench_mom26"].get(when, np.nan), -0.18, 0.20),
        bt.score_linear(features["bench_ma40"].get(when, np.nan), -0.20, 0.15),
        bt.score_linear(features["bench_vol13"].get(when, np.nan), 0.45, 0.10),
        bt.score_linear(features["breadth"].get(when, np.nan), 0.25, 0.80),
    ])
    result: Dict[str, float] = {}
    for symbol in symbols:
        confirmation = np.nanmean([
            bt.score_linear(features["mom_26"].at[when, symbol], -0.30, 0.35),
            bt.score_linear(features["mom_52"].at[when, symbol], -0.40, 0.55),
            bt.score_linear(features["price_ma40"].at[when, symbol], -0.25, 0.25),
            bt.score_linear(features["relative_26"].at[when, symbol], -0.25, 0.25),
        ])
        risk = np.nanmean([
            bt.score_linear(features["vol13"].at[when, symbol], 0.75, 0.12),
            bt.score_linear(features["drawdown"].at[when, symbol], -0.55, 0.0),
        ])
        for code, (direction, strength, quality) in signal_values(features, when, symbol).items():
            delta = 12.0 * direction * strength * quality * (number(learned.get(code), 1.0) - 1.0)
            if code in {"price_vs_ma200", "relative_strength_6m"}:
                confirmation = clamp(confirmation + delta)
            elif code == "drawdown_52w":
                risk = clamp(risk + delta)
        contradiction = 8.0 if confirmation >= 72 and risk <= 35 else 0.0
        result[symbol] = clamp(0.45 * confirmation + 0.30 * risk + 0.25 * context - contradiction)
    return pd.Series(result, dtype=float)


def run_adaptive_strategy(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    target: pd.Series,
    learned: Mapping[str, float],
    rebalance_every: int = 4,
) -> bt.StrategyResult:
    features = bt.feature_frames(prices, benchmark)
    asset_returns = features["returns"]
    valid_dates = prices.index[prices.notna().all(axis=1)]
    if len(valid_dates) < 60:
        raise ValueError("Insufficient aligned history")
    current_weights = pd.Series(0.0, index=prices.columns, dtype=float)
    returns_out: Dict[pd.Timestamp, float] = {}
    turnover_out: Dict[pd.Timestamp, float] = {}
    weights_out: Dict[pd.Timestamp, pd.Series] = {}
    start_index = 52

    for i in range(start_index, len(valid_dates) - 1):
        when, next_when = valid_dates[i], valid_dates[i + 1]
        rebalance = float(current_weights.sum()) == 0.0 or (i - start_index) % rebalance_every == 0
        cost = turnover = 0.0
        if rebalance:
            desired = bt.weights_from_scores(target, adaptive_scores(features, when, learned), "standard")
            turnover = float((desired - current_weights).abs().sum())
            cost = turnover * TRANSACTION_COST
            current_weights = desired.astype(float)
        next_return = asset_returns.loc[next_when].reindex(prices.columns).fillna(0.0)
        cash_weight = max(0.0, 1.0 - float(current_weights.sum()))
        gross_asset_values = current_weights * (1.0 + next_return)
        gross_total = float(gross_asset_values.sum()) + cash_weight
        returns_out[next_when] = gross_total - 1.0 - cost
        turnover_out[next_when] = turnover
        if gross_total > 0:
            current_weights = (gross_asset_values / gross_total).clip(lower=0.0)
        weights_out[next_when] = current_weights.copy()

    returns = pd.Series(returns_out, dtype=float).sort_index()
    return bt.StrategyResult(
        returns=returns,
        equity=(1.0 + returns).cumprod(),
        turnover=pd.Series(turnover_out, dtype=float).sort_index(),
        weights=pd.DataFrame(weights_out).T.sort_index(),
    )


def segment(result: bt.StrategyResult, start: pd.Timestamp, end: pd.Timestamp) -> bt.StrategyResult:
    returns = result.returns.loc[(result.returns.index >= start) & (result.returns.index <= end)]
    turnover = result.turnover.reindex(returns.index).fillna(0.0)
    weights = result.weights.reindex(returns.index)
    return bt.StrategyResult(returns, (1.0 + returns).cumprod(), turnover, weights)


def objective(metrics: Mapping[str, Any]) -> float:
    return (
        number(metrics.get("cagr"))
        + 0.020 * number(metrics.get("sharpe_zero_rf"))
        + 0.050 * number(metrics.get("max_drawdown"))
        - 0.001 * number(metrics.get("annualized_turnover"))
    )


def deltas(base: Mapping[str, Any], adaptive: Mapping[str, Any]) -> Dict[str, float]:
    return {
        "cagr": round(number(adaptive.get("cagr")) - number(base.get("cagr")), 6),
        "sharpe": round(number(adaptive.get("sharpe_zero_rf")) - number(base.get("sharpe_zero_rf")), 4),
        "max_drawdown": round(number(adaptive.get("max_drawdown")) - number(base.get("max_drawdown")), 6),
        "calmar": round(number(adaptive.get("calmar")) - number(base.get("calmar")), 4),
        "objective": round(objective(adaptive) - objective(base), 6),
    }


def significant_progress(validation_delta: Mapping[str, float], test_delta: Mapping[str, float]) -> bool:
    validation_ok = number(validation_delta.get("objective")) > 0
    risk_ok = number(test_delta.get("max_drawdown")) >= -0.01
    return bool(validation_ok and risk_ok and (
        number(test_delta.get("cagr")) >= 0.003
        or number(test_delta.get("sharpe")) >= 0.05
        or (number(test_delta.get("objective")) >= 0.002 and number(test_delta.get("calmar")) >= 0.02)
    ))


def seed_events(stats: Mapping[str, Mapping[str, Any]], training_id: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for code, item in sorted(stats.items()):
        success = number(item.get("success_credit"))
        failure = number(item.get("failure_credit"))
        total = success + failure
        if total < MIN_EFFECTIVE_SAMPLE:
            continue
        scale = min(1.0, HISTORICAL_CREDIT_CAP / total)
        for correct, credit, suffix in ((True, success * scale, "correct"), (False, failure * scale, "incorrect")):
            if credit <= 0:
                continue
            events.append({
                "outcome_event_id": f"historical-seed:{training_id}:{code}:{suffix}",
                "evaluated_at": datetime.now(timezone.utc).date().isoformat(),
                "horizon_days": 0,
                "id": "historical_walk_forward",
                "asset_type": "HistoricalMixed",
                "market_regime": "all",
                "decision": "HISTORICAL_LESSON",
                "asset_return": None,
                "benchmark_return": None,
                "excess_return": None,
                "historical_seed": True,
                "training_id": training_id,
                "evidence_attribution": [{
                    "evidence_id": f"historical:{code}:{suffix}",
                    "code": code,
                    "pillar": "confirmation" if code != "drawdown_52w" else "risk",
                    "direction": 1,
                    "signal_correct": correct,
                    "neutral_outcome": False,
                    "credit": round(credit, 6),
                    "importance": 1.0,
                    "decision_changed_without_evidence": False,
                    "marginal_score": None,
                    "historical_seed": True,
                }],
                "append_only": True,
            })
    return events


def build_training(portfolio: Mapping[str, Any], start: str, patience: int = 6) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    target, proxy_map = bt.build_proxy_target(portfolio)
    live_benchmark = str(portfolio.get("benchmark", {}).get("market_symbol") or "FWIA.DE")
    benchmark_symbol = bt.historical_symbol(live_benchmark)
    downloaded = bt.download_history([*target.index, benchmark_symbol], start)
    weekly = bt.weekly_prices(downloaded)
    available = [symbol for symbol in target.index if symbol in weekly.columns]
    if len(available) < 2:
        raise RuntimeError("Historical accelerator needs at least two available assets")

    # Lessons are built with each asset's available point-in-time history.
    lesson_prices = weekly[available]
    lesson_benchmark = weekly[benchmark_symbol].reindex(lesson_prices.index).ffill()
    lessons = build_lessons(lesson_prices, lesson_benchmark)

    # Strategy validation uses a common investable calendar for fair comparison.
    prices = lesson_prices.dropna(how="any")
    benchmark = lesson_benchmark.reindex(prices.index).dropna()
    prices = prices.reindex(benchmark.index).dropna(how="any")
    benchmark = benchmark.reindex(prices.index)
    target = target.reindex(prices.columns).fillna(0.0)
    target = target / target.sum()
    if len(prices.index) < 180:
        raise RuntimeError("Insufficient common history for train/calibration/test split")

    first_signal = 52
    usable = prices.index[first_signal + 1:]
    train_end = usable[max(1, int(len(usable) * 0.60)) - 1]
    calibration_end = usable[max(2, int(len(usable) * 0.80)) - 1]
    calibration_start = prices.index[prices.index.get_loc(train_end) + 1]
    test_start = prices.index[prices.index.get_loc(calibration_end) + 1]
    test_end = prices.index[-1]

    baseline_full = bt.run_strategy(prices, benchmark, target, "brace", variant="standard")
    baseline_cal = bt.metrics(segment(baseline_full, calibration_start, calibration_end))
    baseline_test = bt.metrics(segment(baseline_full, test_start, test_end))

    configs = [
        (prior, cap)
        for cap in (0.03, 0.05, 0.075, 0.10, 0.15, 0.20)
        for prior in (32.0, 16.0, 8.0)
    ]
    best: Dict[str, Any] | None = None
    candidates: List[Dict[str, Any]] = []
    no_progress = 0
    for prior, cap in configs:
        stats = fit_reliability(lessons, train_end, prior, cap)
        strategy = run_adaptive_strategy(prices, benchmark, target, multipliers(stats))
        metrics_cal = bt.metrics(segment(strategy, calibration_start, calibration_end))
        delta = deltas(baseline_cal, metrics_cal)
        candidate = {
            "prior_strength": prior,
            "multiplier_cap": cap,
            "metrics": metrics_cal,
            "delta_vs_unlearned": delta,
            "objective": round(objective(metrics_cal), 8),
        }
        candidates.append(candidate)
        if best is None or candidate["objective"] > best["objective"] + 0.0005:
            best = candidate
            no_progress = 0
        else:
            no_progress += 1
        if no_progress >= max(1, patience) and len(candidates) >= 9:
            break

    assert best is not None
    final_stats = fit_reliability(
        lessons,
        calibration_end,
        number(best["prior_strength"]),
        number(best["multiplier_cap"]),
    )
    adaptive_full = run_adaptive_strategy(prices, benchmark, target, multipliers(final_stats))
    adaptive_cal = bt.metrics(segment(adaptive_full, calibration_start, calibration_end))
    adaptive_test = bt.metrics(segment(adaptive_full, test_start, test_end))
    validation_delta = deltas(baseline_cal, adaptive_cal)
    test_delta = deltas(baseline_test, adaptive_test)
    activated = significant_progress(validation_delta, test_delta)
    training_id = f"brace-historical-{VERSION}-{calibration_end.date().isoformat()}"

    output = {
        "schema_version": VERSION,
        "training_id": training_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "activated_significant_progress" if activated else "trained_plateau_no_activation",
        "activated": activated,
        "method": "purged_chronological_walk_forward_bayesian_signal_reliability",
        "start_requested": start,
        "lesson_history_start": min((x.observed_at for x in lessons)).date().isoformat() if lessons else None,
        "lesson_history_end": max((x.outcome_at for x in lessons)).date().isoformat() if lessons else None,
        "lessons_total": len(lessons),
        "symbols": available,
        "benchmark_symbol": benchmark_symbol,
        "live_to_historical_proxy": proxy_map,
        "splits": {
            "train_end": train_end.date().isoformat(),
            "calibration_start": calibration_start.date().isoformat(),
            "calibration_end": calibration_end.date().isoformat(),
            "test_start": test_start.date().isoformat(),
            "test_end": test_end.date().isoformat(),
        },
        "search": {
            "candidates_evaluated": len(candidates),
            "patience": patience,
            "stop_reason": "progress_plateau" if len(candidates) < len(configs) else "candidate_space_exhausted",
            "selected": {
                "prior_strength": best["prior_strength"],
                "multiplier_cap": best["multiplier_cap"],
            },
            "candidates": candidates,
        },
        "validation": {
            "unlearned": baseline_cal,
            "learned": adaptive_cal,
            "delta": validation_delta,
        },
        "untouched_test": {
            "unlearned": baseline_test,
            "learned": adaptive_test,
            "delta": test_delta,
        },
        "activation_gate": {
            "passed": activated,
            "requires_positive_calibration_objective": True,
            "maximum_allowed_drawdown_deterioration": -0.01,
            "minimum_test_cagr_improvement": 0.003,
            "alternative_minimum_test_sharpe_improvement": 0.05,
        },
        "signal_reliability": final_stats,
        "multipliers": multipliers(final_stats) if activated else {code: 1.0 for code in final_stats},
        "historical_credit_cap_per_signal": HISTORICAL_CREDIT_CAP,
        "governance_pl": (
            "Historia uczy wyłącznie wiarygodności odtwarzalnych sygnałów cenowych. "
            "Ostatni fragment danych pozostaje nietkniętym testem. Brak istotnego postępu "
            "pozostawia mnożniki neutralne. Wagi filarów, progi decyzji i limity portfela nie są samoczynnie zmieniane."
        ),
        "governance_en": (
            "History trains only the reliability of reproducible price signals. The final data segment remains untouched. "
            "Without material progress multipliers stay neutral. Pillar weights, decision thresholds and portfolio limits do not self-modify."
        ),
    }
    return output, final_stats


def apply_to_memory(memory_path: Path, output: Mapping[str, Any], stats: Mapping[str, Mapping[str, Any]]) -> None:
    memory = learning.load_memory(memory_path)
    memory["outcome_events"] = [
        item for item in memory.get("outcome_events", [])
        if not item.get("historical_seed")
    ]
    if output.get("activated"):
        memory["outcome_events"].extend(seed_events(stats, str(output.get("training_id"))))
    memory["historical_training"] = {
        "training_id": output.get("training_id"),
        "generated_at": output.get("generated_at"),
        "status": output.get("status"),
        "activated": bool(output.get("activated")),
        "lessons_total": output.get("lessons_total"),
        "selected": (output.get("search") or {}).get("selected"),
        "validation_delta": (output.get("validation") or {}).get("delta"),
        "test_delta": (output.get("untouched_test") or {}).get("delta"),
        "multipliers": output.get("multipliers"),
        "historical_credit_cap_per_signal": HISTORICAL_CREDIT_CAP,
    }
    memory.setdefault("audit", []).append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": "historical_accelerator_completed",
        "training_id": output.get("training_id"),
        "status": output.get("status"),
        "lessons_total": output.get("lessons_total"),
    })
    learning.write_memory(memory_path, memory)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio", type=Path, default=PORTFOLIO_PATH)
    parser.add_argument("--memory", type=Path, default=MEMORY_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--start", default="1995-01-01")
    parser.add_argument("--patience", type=int, default=6)
    args = parser.parse_args()

    portfolio = json.loads(args.portfolio.read_text(encoding="utf-8"))
    output, stats = build_training(portfolio, args.start, args.patience)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    apply_to_memory(args.memory, output, stats)
    delta = (output.get("untouched_test") or {}).get("delta") or {}
    print(
        f"BRACE historical accelerator: status={output['status']}, lessons={output['lessons_total']}, "
        f"test_cagr_delta={number(delta.get('cagr')):+.2%}, test_sharpe_delta={number(delta.get('sharpe')):+.3f}"
    )


if __name__ == "__main__":
    main()
