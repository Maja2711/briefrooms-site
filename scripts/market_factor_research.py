#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional

try:
    import yfinance as yf
except Exception:
    yf = None

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "investments" / "learning" / "market_factor_research.json"
MODEL = "market-factor-research-1.0"

ASSETS = {
    "eurusd": "EURUSD=X",
    "sp500_futures": "ES=F",
    "btcusd": "BTC-USD",
}
AUX = {
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",
    "vix": "^VIX",
    "nasdaq": "^IXIC",
    "btc": "BTC-USD",
    "eth": "ETH-USD",
    "tip": "TIP",
    "ief": "IEF",
}


def num(x: Any) -> Optional[float]:
    try:
        v = float(x)
        if math.isfinite(v):
            return v
    except Exception:
        return None
    return None


def load(symbol: str, period: str) -> Dict[str, List[float]]:
    if yf is None:
        return {"close": [], "high": [], "low": []}
    try:
        df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
    except Exception:
        return {"close": [], "high": [], "low": []}
    out = {"close": [], "high": [], "low": []}
    if df is None or df.empty:
        return out
    for _, row in df.iterrows():
        c, h, l = num(row.get("Close")), num(row.get("High")), num(row.get("Low"))
        if c is not None and h is not None and l is not None:
            out["close"].append(c); out["high"].append(h); out["low"].append(l)
    return out


def ema(values: List[float], n: int) -> List[float]:
    if not values:
        return []
    a = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(a * v + (1 - a) * out[-1])
    return out


def rsi(values: List[float], n: int = 14) -> Optional[float]:
    if len(values) <= n:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0)); losses.append(abs(min(d, 0)))
    ag, al = mean(gains[-n:]), mean(losses[-n:])
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def macd(values: List[float]) -> Dict[str, Optional[float]]:
    if len(values) < 35:
        return {"line": None, "signal": None, "hist": None}
    e12, e26 = ema(values, 12), ema(values, 26)
    line = [a - b for a, b in zip(e12, e26)]
    sig = ema(line, 9)
    return {"line": line[-1], "signal": sig[-1], "hist": line[-1] - sig[-1]}


def atr(data: Dict[str, List[float]], n: int = 14) -> Optional[float]:
    c, h, l = data["close"], data["high"], data["low"]
    if len(c) <= n + 1:
        return None
    trs = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    return mean(trs[-n:])


def change(values: List[float], n: int) -> Optional[float]:
    if len(values) <= n or values[-n - 1] == 0:
        return None
    return (values[-1] / values[-n - 1] - 1) * 100


def z(values: List[float], n: int = 60) -> Optional[float]:
    if len(values) < n:
        return None
    s = values[-n:]
    sd = pstdev(s)
    return 0.0 if sd == 0 else (s[-1] - mean(s)) / sd


def base_factors(data: Dict[str, List[float]]) -> Dict[str, Any]:
    c = data["close"]
    if len(c) < 60:
        return {"score": 0, "error": "not enough data"}
    e20, e50 = ema(c, 20)[-1], ema(c, 50)[-1]
    r = rsi(c) or 50
    m = macd(c)
    a = atr(data)
    sup = min(data["low"][-60:])
    res = max(data["high"][-60:])
    pos = 0.5 if res == sup else (c[-1] - sup) / (res - sup)
    score = 0.0
    if c[-1] > e20 > e50: score += 25
    if c[-1] < e20 < e50: score -= 25
    hist = num(m.get("hist"))
    if hist is not None: score += max(-20, min(20, hist / c[-1] * 10000))
    if r > 70: score -= 10
    if r < 30: score += 10
    if pos > 0.85: score -= 7
    if pos < 0.15: score += 7
    return {"score": round(max(-100, min(100, score)), 2), "rsi14": round(r, 2), "macd": m, "atr14": a, "atr14_pct": a / c[-1] * 100 if a else None, "support": sup, "resistance": res, "range_position": round(pos, 3)}


def build(period: str) -> Dict[str, Any]:
    aux = {k: load(v, period) for k, v in AUX.items()}
    results = {}
    for key, sym in ASSETS.items():
        data = load(sym, period)
        base = base_factors(data)
        extra = 0.0
        notes = []
        if key == "eurusd":
            dxy = change(aux["dxy"]["close"], 20)
            us10y = change(aux["us10y"]["close"], 20)
            if dxy is not None: extra += max(-25, min(25, -5 * dxy)); notes.append(f"DXY20={dxy:+.2f}%")
            if us10y is not None: extra += max(-20, min(20, -1.5 * us10y)); notes.append(f"US10Y20={us10y:+.2f}%")
        if key == "sp500_futures":
            vz = z(aux["vix"]["close"], 60)
            if vz is not None: extra += max(-35, min(35, -18 * vz)); notes.append(f"VIXz={vz:+.2f}")
        if key == "btcusd":
            ndx = change(aux["nasdaq"]["close"], 20)
            if ndx is not None: extra += max(-22, min(22, 2.5 * ndx)); notes.append(f"Nasdaq20={ndx:+.2f}%")
            b, e = aux["btc"]["close"], aux["eth"]["close"]
            dom = [x / (x + y) for x, y in zip(b[-90:], e[-90:]) if x + y > 0]
            dc = change(dom, 20) if len(dom) > 25 else None
            if dc is not None: extra += max(-18, min(18, 7 * dc)); notes.append(f"BTCdom20={dc:+.2f}%")
            t, i = aux["tip"]["close"], aux["ief"]["close"]
            proxy = [x / y for x, y in zip(t[-90:], i[-90:]) if y > 0]
            pc = change(proxy, 20) if len(proxy) > 25 else None
            if pc is not None: extra += max(-20, min(20, -8 * pc)); notes.append(f"RealYieldProxy20={pc:+.2f}%")
        brace = 0.62 * float(base.get("score", 0)) + 0.38 * extra
        results[key] = {"symbol": sym, "last_price": data["close"][-1] if data["close"] else None, "technical": base, "cross_market_score": round(extra, 2), "cross_market_notes": notes, "brace_score": round(max(-100, min(100, brace)), 2), "brace_name": "BriefRooms Regime Alignment & Conviction Engine"}
    return {"model_version": MODEL, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "period": period, "results": results}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--period", default="1y")
    args = p.parse_args()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(args.period), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
