#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "investments" / "room_quotes.json"
LIVE = ROOT / "data" / "investments" / "live_prices.json"
WARSAW = timezone(timedelta(hours=2))
STOOQ_URL = "https://stooq.pl/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"

INSTRUMENTS = {
    "eurusd": {"symbols": ["eurusd", "eurusd.pl"], "live_key": "eurusd", "label_pl": "EUR/USD", "label_en": "EUR/USD", "kind_pl": "FX", "kind_en": "FX", "decimals": 5, "unit": ""},
    "sp500": {"symbols": ["spx", "^spx", "es.f", "es.f.us"], "live_key": "sp500_futures", "label_pl": "S&P 500", "label_en": "S&P 500", "kind_pl": "Indeks", "kind_en": "Index", "decimals": 2, "unit": ""},
    "pl10y": {"symbols": ["10yply.b", "10YPLY.B"], "live_key": "", "label_pl": "PL 10Y", "label_en": "PL 10Y", "kind_pl": "Polskie obligacje 10Y · rentowność", "kind_en": "Polish 10Y bonds · yield", "decimals": 3, "unit": "%"},
    "us10y": {"symbols": ["10yusy.b", "10YUSY.B"], "live_key": "", "label_pl": "US 10Y", "label_en": "US 10Y", "kind_pl": "Amerykańskie obligacje 10Y · rentowność", "kind_en": "US 10Y bonds · yield", "decimals": 3, "unit": "%"},
    "btcusd": {"symbols": ["btcusd", "btc.v", "xbtusd"], "live_key": "btcusd", "label_pl": "BTC/USD", "label_en": "BTC/USD", "kind_pl": "Krypto", "kind_en": "Crypto", "decimals": 2, "unit": ""},
}


def now_iso() -> str:
    return datetime.now(WARSAW).isoformat(timespec="seconds")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        text = str(value).strip().replace(",", ".")
        if not text or text.lower() in {"n/d", "nan", "none", "-", "null"}:
            return None
        x = float(text)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def fetch_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    url = STOOQ_URL.format(symbol=urllib.parse.quote(symbol))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BriefRoomsQuotes/1.1"})
        with urllib.request.urlopen(req, timeout=14) as res:
            text = res.read().decode("utf-8", errors="ignore")
        rows = list(csv.DictReader(text.splitlines()))
        if not rows:
            return None
        row = rows[0]
        close = to_float(row.get("Close"))
        if close is None:
            return None
        open_ = to_float(row.get("Open"))
        change = None if open_ in (None, 0) else ((close - open_) / open_) * 100
        return {"symbol": symbol, "date": row.get("Date") or "", "time": row.get("Time") or "", "open": open_, "high": to_float(row.get("High")), "low": to_float(row.get("Low")), "close": close, "change_percent": change, "source": "Stooq.pl", "source_url": url}
    except Exception as exc:
        return {"symbol": symbol, "error": str(exc), "source_url": url}


def base_quote(key: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {"key": key, "label_pl": cfg["label_pl"], "label_en": cfg["label_en"], "kind_pl": cfg["kind_pl"], "kind_en": cfg["kind_en"], "decimals": cfg["decimals"], "unit": cfg["unit"], "source": "Stooq.pl", "symbol": cfg["symbols"][0], "close": None, "change_percent": None}


def quote_for(key: str, cfg: Dict[str, Any], previous: Dict[str, Any], live_prices: Dict[str, Any]) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    for symbol in cfg["symbols"]:
        rec = fetch_symbol(symbol)
        if rec:
            attempts.append(rec)
            if rec.get("close") is not None:
                return {**base_quote(key, cfg), **rec, "attempts": attempts}

    live_key = cfg.get("live_key")
    live = live_prices.get(live_key, {}) if live_key else {}
    live_price = to_float(live.get("price"))
    if live_price is not None:
        return {**base_quote(key, cfg), "symbol": live_key or cfg["symbols"][0], "close": live_price, "change_percent": None, "date": str(live.get("timestamp") or "")[:10], "time": str(live.get("timestamp") or "")[11:19], "source": str(live.get("source") or "Yahoo Finance fallback"), "fallback_reason": "Stooq quote unavailable in this run; using live_prices fallback", "attempts": attempts}

    old = previous.get("quotes", {}).get(key, {}) if isinstance(previous, dict) else {}
    if to_float(old.get("close")) is not None:
        kept = {**base_quote(key, cfg), **old}
        kept["fallback_reason"] = "Stooq quote unavailable in this run; keeping previous non-empty value"
        kept["attempts"] = attempts
        return kept

    q = base_quote(key, cfg)
    q["attempts"] = attempts
    return q


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    previous = load_json(OUT, {})
    live = load_json(LIVE, {}).get("prices", {})
    quotes = {key: quote_for(key, cfg, previous, live) for key, cfg in INSTRUMENTS.items()}
    payload = {"updated_at": now_iso(), "source": "Stooq.pl q/l CSV; Yahoo/live_prices fallback for FX/index/crypto if Stooq is unavailable", "refresh": "15-minute workflow; pages fetch this JSON without cache", "pl_bond_key": "pl10y", "en_bond_key": "us10y", "quotes": quotes}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {OUT}")


if __name__ == "__main__":
    main()
