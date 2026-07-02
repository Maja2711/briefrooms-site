#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional

try:
    import yfinance as yf
except Exception:
    yf = None

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "investments" / "learning" / "eurusd_ma_timeframe_research.json"
SYMBOL = "EURUSD=X"
MODEL = "eurusd-ma-30-60-100-mtf-1.0"


def f(x: Any) -> Optional[float]:
    try:
        v = float(x)
        if math.isfinite(v):
            return v
    except Exception:
        return None
    return None


def load(interval: str, period: str) -> List[Dict[str, Any]]:
    if yf is None:
        return []
    try:
        df = yf.Ticker(SYMBOL).history(period=period, interval=interval, auto_adjust=False)
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    if df is None or df.empty:
        return rows
    for idx, row in df.iterrows():
        c = f(row.get("Close"))
        if c is not None and c > 0:
            rows.append({"date": str(idx.date()), "close": c})
    return rows


def sma(values: List[float], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < n:
            out.append(None)
        else:
            out.append(mean(values[i - n + 1 : i + 1]))
    return out


def trend_signal(close: float, ma30: Optional[float], ma60: Optional[float], ma100: Optional[float]) -> str:
    if ma30 is None or ma60 is None or ma100 is None:
        return "neutral"
    if close > ma30 > ma60 > ma100:
        return "long"
    if close < ma30 < ma60 < ma100:
        return "short"
    if ma30 > ma60 > ma100:
        return "bullish_bias"
    if ma30 < ma60 < ma100:
        return "bearish_bias"
    return "neutral"


def one_tf(interval: str, period: str) -> Dict[str, Any]:
    rows = load(interval, period)
    closes = [r["close"] for r in rows]
    ma30, ma60, ma100 = sma(closes, 30), sma(closes, 60), sma(closes, 100)
    if len(rows) < 120:
        return {"interval": interval, "status": "insufficient_data", "bars": len(rows)}
    latest = {
        "close": closes[-1],
        "ma30": ma30[-1],
        "ma60": ma60[-1],
        "ma100": ma100[-1],
        "signal": trend_signal(closes[-1], ma30[-1], ma60[-1], ma100[-1]),
    }
    trades = []
    for i in range(101, len(closes) - 5):
        sig = trend_signal(closes[i], ma30[i], ma60[i], ma100[i])
        if sig not in {"long", "short"}:
            continue
        entry = closes[i]
        exit_price = closes[min(i + 5, len(closes) - 1)]
        pnl = (exit_price / entry - 1) * 100 if sig == "long" else (entry / exit_price - 1) * 100
        trades.append(pnl)
    wins = [x for x in trades if x > 0]
    losses = [x for x in trades if x < 0]
    total = sum(trades)
    pf = sum(wins) / abs(sum(losses)) if losses else (999 if wins else 0)
    return {
        "interval": interval,
        "status": "ok",
        "bars": len(rows),
        "latest": latest,
        "backtest": {
            "trades": len(trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
            "avg_percent": round(mean(trades), 4) if trades else 0,
            "total_percent": round(total, 2),
            "profit_factor": round(pf, 2),
        },
    }


def combined(daily: Dict[str, Any], weekly: Dict[str, Any], monthly: Dict[str, Any]) -> Dict[str, Any]:
    signals = [x.get("latest", {}).get("signal") for x in [daily, weekly, monthly] if x.get("status") == "ok"]
    long_votes = sum(1 for x in signals if x in {"long", "bullish_bias"})
    short_votes = sum(1 for x in signals if x in {"short", "bearish_bias"})
    if long_votes >= 2 and short_votes == 0:
        final = "long_filter"
    elif short_votes >= 2 and long_votes == 0:
        final = "short_filter"
    elif long_votes > short_votes:
        final = "soft_long_filter"
    elif short_votes > long_votes:
        final = "soft_short_filter"
    else:
        final = "neutral_filter"
    decision = "apply_as_filter" if final in {"long_filter", "short_filter"} else "use_as_context_only"
    return {"signals": signals, "long_votes": long_votes, "short_votes": short_votes, "final_signal": final, "decision": decision}


def build() -> Dict[str, Any]:
    daily = one_tf("1d", "5y")
    weekly = one_tf("1wk", "10y")
    monthly = one_tf("1mo", "20y")
    return {
        "model_version": MODEL,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "instrument": "EUR/USD",
        "method": "SMA30/SMA60/SMA100 multi-timeframe filter on daily, weekly and monthly charts",
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "combined": combined(daily, weekly, monthly),
        "rule_pl": "Zastosuj jako filtr kierunku tylko gdy co najmniej dwa interwały są zgodne i trzeci nie jest przeciwny. W innym przypadku traktuj jako kontekst, nie jako samodzielny sygnał wejścia.",
        "rule_en": "Use as a direction filter only when at least two timeframes agree and the third is not opposite. Otherwise use as context, not as a standalone entry signal."
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
