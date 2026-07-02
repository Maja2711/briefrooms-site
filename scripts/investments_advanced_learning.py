#!/usr/bin/env python3
"""Advanced learning/backtest engine for BriefRooms weekly positions.

Goal: improve future educational position rules with evidence, not guesswork.
The script downloads historical market data, simulates weekly entries, evaluates
trend/momentum/volatility parameter grids, selects robust parameters, and writes
learned parameters used by the risk review script.

Important: this is not a promise of profit. It is a disciplined research loop:
backtest -> validation -> parameter selection -> forward journal -> review.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Tuple

import investments_weekly as base

LEARNING_DIR = base.REPO / "data" / "investments" / "learning"
PARAMS_PATH = LEARNING_DIR / "learned_parameters.json"
REPORT_PATH = LEARNING_DIR / "advanced_model_report.json"
RESEARCH_PATH = LEARNING_DIR / "research_queue.json"
MODEL_VERSION = "advanced-learning-1.0"
MIN_TRADES_FOR_SELECTION = 18


@dataclass
class Bar:
    date: str
    close: float


@dataclass
class Trade:
    instrument_id: str
    side: str
    entry_date: str
    exit_date: str
    entry: float
    exit: float
    exit_reason: str
    pnl_pct: float
    pnl_usd: float


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        x = float(value)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except Exception:
        return None


def pct_change(a: float, b: float) -> float:
    return (b / a - 1.0) * 100.0 if a else 0.0


def ema(values: List[float], span: int) -> List[Optional[float]]:
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    out: List[Optional[float]] = []
    current: Optional[float] = None
    for v in values:
        current = v if current is None else alpha * v + (1 - alpha) * current
        out.append(current)
    return out


def rolling_abs_move(values: List[float], lookback: int = 20) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    moves = [0.0]
    for i in range(1, len(values)):
        moves.append(abs(values[i] / values[i - 1] - 1.0))
    for i in range(len(values)):
        if i < lookback:
            out.append(None)
        else:
            out.append(mean(moves[i - lookback + 1 : i + 1]))
    return out


def load_prices(symbol: str, period: str = "3y") -> List[Bar]:
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
        rows: List[Bar] = []
        if hist is None or hist.empty:
            return rows
        for idx, row in hist.iterrows():
            close = safe_float(row.get("Close"))
            if close is not None and close > 0:
                rows.append(Bar(date=str(idx.date()), close=close))
        return rows
    except Exception as exc:
        print(f"[WARN] Cannot load {symbol}: {exc}")
        return []


def notional(inst_id: str) -> float:
    return 10_000.0


def unit_size(inst_id: str) -> float:
    return 0.0001 if inst_id == "eurusd" else 1.0


def score_at(i: int, closes: List[float], ema20: List[Optional[float]], ema50: List[Optional[float]]) -> float:
    close = closes[i]
    score = 0.0
    if ema20[i] is not None and ema50[i] is not None:
        if ema20[i] > ema50[i] and close > ema20[i]:
            score += 35
        elif ema20[i] < ema50[i] and close < ema20[i]:
            score -= 35
    if i >= 5:
        m5 = pct_change(closes[i - 5], close)
        score += max(-20, min(20, m5 * 5))
    if i >= 20:
        m20 = pct_change(closes[i - 20], close)
        score += max(-25, min(25, m20 * 2.2))
    return max(-100.0, min(100.0, score))


def side_from_score(score: float, threshold: float) -> str:
    if score >= threshold:
        return "long"
    if score <= -threshold:
        return "short"
    return "none"


def trade_pnl(inst_id: str, side: str, entry: float, exit_price: float) -> Tuple[float, float]:
    raw = exit_price - entry if side == "long" else entry - exit_price
    pnl_pct = raw / entry * 100.0 if entry else 0.0
    pnl_usd = pnl_pct / 100.0 * notional(inst_id)
    if inst_id == "eurusd":
        pnl_usd = raw * 10_000.0
    return pnl_pct, pnl_usd


def simulate_strategy(inst_id: str, bars: List[Bar], threshold: float, tp_mult: float, sl_mult: float) -> List[Trade]:
    closes = [b.close for b in bars]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    vol = rolling_abs_move(closes, 20)
    trades: List[Trade] = []
    # Simulate Monday-ish entries: every 5th trading day after warmup.
    for i in range(55, len(bars) - 5, 5):
        s = score_at(i - 1, closes, e20, e50)
        side = side_from_score(s, threshold)
        if side == "none":
            continue
        entry = closes[i]
        daily_vol = vol[i - 1] or 0.006
        expected_week_pct = daily_vol * math.sqrt(5) * 100.0
        tp_pct = max(0.15, expected_week_pct * tp_mult)
        sl_pct = max(0.10, expected_week_pct * sl_mult)
        exit_price = closes[min(i + 4, len(bars) - 1)]
        exit_idx = min(i + 4, len(bars) - 1)
        exit_reason = "weekly_close"
        for j in range(i + 1, min(i + 5, len(bars))):
            move_pct = pct_change(entry, closes[j]) if side == "long" else pct_change(closes[j], entry)
            if move_pct <= -sl_pct:
                exit_price, exit_idx, exit_reason = closes[j], j, "stop_loss"
                break
            if move_pct >= tp_pct:
                exit_price, exit_idx, exit_reason = closes[j], j, "take_profit"
                break
        pnl_pct, pnl_usd = trade_pnl(inst_id, side, entry, exit_price)
        trades.append(Trade(inst_id, side, bars[i].date, bars[exit_idx].date, entry, exit_price, exit_reason, pnl_pct, pnl_usd))
    return trades


def metrics(trades: List[Trade]) -> Dict[str, Any]:
    if not trades:
        return {"trades": 0, "score": -999999}
    pnl = [t.pnl_usd for t in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    total = sum(pnl)
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else (999.0 if wins else 0.0)
    win_rate = len(wins) / len(trades) * 100.0
    avg = total / len(trades)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in pnl:
        equity += x
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    # Conservative composite score: profitability, consistency, and drawdown penalty.
    score = total + avg * 8.0 + profit_factor * 60.0 + win_rate * 3.0 + max_dd * 1.2
    return {
        "trades": len(trades),
        "total_usd": round(total, 2),
        "avg_usd": round(avg, 2),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_usd": round(max_dd, 2),
        "score": round(score, 2),
        "take_profit_hits": sum(1 for t in trades if t.exit_reason == "take_profit"),
        "stop_loss_hits": sum(1 for t in trades if t.exit_reason == "stop_loss"),
    }


def split_train_validate(trades: List[Trade]) -> Tuple[List[Trade], List[Trade]]:
    if len(trades) < 8:
        return trades, []
    cut = int(len(trades) * 0.70)
    return trades[:cut], trades[cut:]


def grid_search(inst_id: str, symbol: str) -> Dict[str, Any]:
    bars = load_prices(symbol, "3y")
    if len(bars) < 90:
        return {"status": "insufficient_data", "bars": len(bars)}
    candidates: List[Dict[str, Any]] = []
    for threshold in [12, 16, 20, 24, 28, 34]:
        for tp_mult in [0.65, 0.80, 0.95, 1.10, 1.30]:
            for sl_mult in [0.40, 0.50, 0.60, 0.75, 0.90]:
                if tp_mult <= sl_mult * 0.90:
                    continue
                trades = simulate_strategy(inst_id, bars, threshold, tp_mult, sl_mult)
                if len(trades) < MIN_TRADES_FOR_SELECTION:
                    continue
                train, valid = split_train_validate(trades)
                m_train, m_valid, m_all = metrics(train), metrics(valid), metrics(trades)
                # Guard against overfitting: validation must not collapse.
                validation_ok = not valid or (m_valid.get("profit_factor", 0) >= 0.75 and m_valid.get("max_drawdown_usd", -999999) > -1800)
                stability_penalty = 0 if validation_ok else -1000
                combined = float(m_train.get("score", -999999)) * 0.45 + float(m_valid.get("score", 0)) * 0.55 + stability_penalty
                candidates.append({
                    "threshold": threshold,
                    "take_profit_multiplier": tp_mult,
                    "stop_loss_multiplier": sl_mult,
                    "train": m_train,
                    "validation": m_valid,
                    "all": m_all,
                    "combined_score": round(combined, 2),
                    "validation_ok": validation_ok,
                })
    candidates.sort(key=lambda x: x.get("combined_score", -999999), reverse=True)
    best = candidates[0] if candidates else None
    return {
        "status": "ok" if best else "no_candidate",
        "bars": len(bars),
        "best": best,
        "top_candidates": candidates[:10],
        "research_note_pl": "Parametry wybrane przez backtest z walidacją. Nie są gwarancją wyniku; mają poprawiać dyscyplinę wejścia, SL i TP.",
        "research_note_en": "Parameters selected by backtest with validation. They do not guarantee results; they improve entry discipline, SL and TP sizing.",
    }


def build_research_queue(results: Dict[str, Any]) -> Dict[str, Any]:
    tasks = []
    for inst_id, result in results.items():
        best = result.get("best") or {}
        valid = best.get("validation") or {}
        allm = best.get("all") or {}
        if result.get("status") != "ok":
            tasks.append({"instrument_id": inst_id, "priority": "high", "task_pl": "Za mało danych lub brak kandydata — sprawdzić pobieranie cen i zakres testu.", "task_en": "Insufficient data or no candidate — check price download and test range."})
            continue
        if (valid.get("profit_factor") or 0) < 1.0:
            tasks.append({"instrument_id": inst_id, "priority": "high", "task_pl": "Walidacja ma profit factor poniżej 1.0 — sprawdzić dodatkowy filtr trendu albo wyższy próg wejścia.", "task_en": "Validation profit factor below 1.0 — test an additional trend filter or a higher entry threshold."})
        if (allm.get("stop_loss_hits") or 0) > (allm.get("take_profit_hits") or 0) * 1.5:
            tasks.append({"instrument_id": inst_id, "priority": "medium", "task_pl": "Zbyt dużo stop loss względem TP — sprawdzić szerszy SL lub mniej agresywne wejścia.", "task_en": "Too many stop losses versus TP — test wider SL or less aggressive entries."})
    return {"updated_at": base.now_local().isoformat(timespec="seconds"), "tasks": tasks}


def write_outputs(results: Dict[str, Any]) -> None:
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    instruments: Dict[str, Any] = {}
    for inst_id, result in results.items():
        best = result.get("best") or {}
        if result.get("status") == "ok" and best:
            instruments[inst_id] = {
                "direction_threshold": best.get("threshold"),
                "favorable_multiplier": best.get("take_profit_multiplier"),
                "adverse_multiplier": best.get("stop_loss_multiplier"),
                "selected_score": best.get("combined_score"),
                "train": best.get("train"),
                "validation": best.get("validation"),
                "all": best.get("all"),
                "status": "active_candidate" if best.get("validation_ok") else "watch_only",
            }
    params = {
        "model_version": MODEL_VERSION,
        "updated_at": base.now_local().isoformat(timespec="seconds"),
        "selection_rule": "Use only future trades. Do not rewrite past forecasts. Re-run weekly and prefer parameters that survive validation.",
        "instruments": instruments,
    }
    report = {"model_version": MODEL_VERSION, "updated_at": params["updated_at"], "results": results}
    base.write_json(PARAMS_PATH, params)
    base.write_json(REPORT_PATH, report)
    base.write_json(RESEARCH_PATH, build_research_queue(results))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="3y")
    parser.parse_args()
    method = base.load_json(base.METHOD_PATH, {})
    results: Dict[str, Any] = {}
    for inst in method.get("instruments", []):
        inst_id = str(inst.get("id"))
        symbol = str(inst.get("symbol"))
        if not inst_id or not symbol:
            continue
        results[inst_id] = grid_search(inst_id, symbol)
    write_outputs(results)
    print(f"Advanced learning complete. Parameters: {PARAMS_PATH}. Report: {REPORT_PATH}.")


if __name__ == "__main__":
    main()
