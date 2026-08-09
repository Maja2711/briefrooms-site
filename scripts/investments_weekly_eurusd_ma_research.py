#!/usr/bin/env python3
"""Research EUR/USD daily MA30/60/100/200 regimes for five-session forward returns.

Research-only. No broker orders, no automatic production signal changes.
The script uses only information available at each historical close and evaluates
forward five-session returns without look-ahead in the signal definition.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import fmean, median
from typing import Any, Dict, List

import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/investments/eurusd_ma_30_60_100_200_research.json"
SYMBOL = "EURUSD=X"
WINDOWS = (30, 60, 100, 200)
FORWARD = 5


def finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def summarize(values: List[float], side: str) -> Dict[str, Any]:
    if not values:
        return {"count": 0, "mean_forward_5d_percent": None, "median_forward_5d_percent": None, "hit_rate": None}
    hits = sum(1 for value in values if value > 0) if side == "long" else sum(1 for value in values if value < 0)
    signed = values if side == "long" else [-value for value in values]
    return {
        "count": len(values),
        "mean_forward_5d_percent": round(fmean(signed), 6),
        "median_forward_5d_percent": round(median(signed), 6),
        "hit_rate": round(hits / len(values), 6),
    }


def main() -> None:
    df = yf.download(SYMBOL, period="15y", interval="1d", progress=False, auto_adjust=False, threads=False)
    if df is None or df.empty:
        raise SystemExit("EURUSD daily data unavailable")
    close = df["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    close = close.dropna().astype(float)
    frame = close.to_frame("close")
    for window in WINDOWS:
        frame[f"ma{window}"] = frame["close"].rolling(window).mean()
    frame["forward_5d_pct"] = (frame["close"].shift(-FORWARD) / frame["close"] - 1.0) * 100.0
    frame = frame.dropna()

    regimes: Dict[str, Dict[str, List[float]]] = {
        "full_stack": {"long": [], "short": []},
        "price_vs_all": {"long": [], "short": []},
        "ma30_60_with_100_200_filter": {"long": [], "short": []},
        "ma100_200_trend": {"long": [], "short": []},
    }
    for _, row in frame.iterrows():
        p = float(row["close"]); m30 = float(row["ma30"]); m60 = float(row["ma60"]); m100 = float(row["ma100"]); m200 = float(row["ma200"])
        fwd = finite(row["forward_5d_pct"])
        if fwd is None:
            continue
        if m30 > m60 > m100 > m200:
            regimes["full_stack"]["long"].append(fwd)
        elif m30 < m60 < m100 < m200:
            regimes["full_stack"]["short"].append(fwd)
        if p > m30 and p > m60 and p > m100 and p > m200:
            regimes["price_vs_all"]["long"].append(fwd)
        elif p < m30 and p < m60 and p < m100 and p < m200:
            regimes["price_vs_all"]["short"].append(fwd)
        if m30 > m60 and m100 > m200:
            regimes["ma30_60_with_100_200_filter"]["long"].append(fwd)
        elif m30 < m60 and m100 < m200:
            regimes["ma30_60_with_100_200_filter"]["short"].append(fwd)
        if m100 > m200:
            regimes["ma100_200_trend"]["long"].append(fwd)
        elif m100 < m200:
            regimes["ma100_200_trend"]["short"].append(fwd)

    results = {
        name: {side: summarize(values, side) for side, values in sides.items()}
        for name, sides in regimes.items()
    }
    payload = {
        "status": "completed",
        "research_only": True,
        "symbol": SYMBOL,
        "daily_bars": int(len(close)),
        "usable_bars": int(len(frame)),
        "ma_windows": list(WINDOWS),
        "forward_horizon_sessions": FORWARD,
        "methodology": "Signal is computed from close and trailing simple moving averages available on that date; outcome is the next five-session close-to-close return. No transaction costs are included because this is a regime-screening study, not a trading P/L backtest.",
        "results": results,
        "interpretation_rule": "Do not promote an MA rule into the production tournament solely on in-sample mean return. Require adequate sample size, directional hit-rate improvement, stability across subperiods, and a separate holdout before production use.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
