#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
import math
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "investments" / "live_prices.json"
WARSAW = ZoneInfo("Europe/Warsaw")
REQUEST_TIMEOUT = 6


@dataclass(frozen=True)
class Instrument:
    yahoo: str
    stooq: Optional[str]
    min_price: float
    max_price: float
    max_age: timedelta


INSTRUMENTS: Dict[str, Instrument] = {
    "eurusd": Instrument("EURUSD=X", "eurusd", 0.8, 1.5, timedelta(minutes=10)),
    "sp500_futures": Instrument("ES=F", "es.f", 500.0, 100_000.0, timedelta(minutes=10)),
    "btcusd": Instrument("BTC-USD", None, 1_000.0, 2_000_000.0, timedelta(minutes=5)),
}


def now_local() -> datetime:
    return datetime.now(WARSAW)


def safe_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def load_previous() -> Dict[str, Any]:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {"prices": {}}


def request_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 BriefRoomsWeeklyLive/1.0",
            "Accept": "application/json,text/plain,text/csv,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
        return response.read()


def parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WARSAW)
    return parsed.astimezone(WARSAW)


def valid_quote(instrument_id: str, quote: Dict[str, Any]) -> bool:
    cfg = INSTRUMENTS[instrument_id]
    price = safe_float(quote.get("price"))
    stamp = parse_iso(quote.get("timestamp"))
    return bool(
        price is not None
        and cfg.min_price <= price <= cfg.max_price
        and stamp is not None
        and stamp <= now_local() + timedelta(minutes=1)
    )


def quote_age(quote: Dict[str, Any]) -> timedelta:
    stamp = parse_iso(quote.get("timestamp"))
    if stamp is None:
        return timedelta.max
    return now_local() - stamp


def yahoo_quote(instrument_id: str) -> Dict[str, Any]:
    cfg = INSTRUMENTS[instrument_id]
    symbol = urllib.parse.quote(cfg.yahoo, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1m"
    payload = json.loads(request_bytes(url).decode("utf-8"))
    chart = (payload.get("chart", {}).get("result") or [None])[0]
    if not chart:
        raise RuntimeError("empty Yahoo chart")

    timestamps = chart.get("timestamp") or []
    closes = ((chart.get("indicators", {}).get("quote") or [{}])[0]).get("close") or []
    for raw_ts, raw_close in reversed(list(zip(timestamps, closes))):
        price = safe_float(raw_close)
        try:
            ts = int(raw_ts)
        except (TypeError, ValueError):
            continue
        if price is None:
            continue
        stamp = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(WARSAW)
        return {
            "price": price,
            "timestamp": stamp.isoformat(timespec="seconds"),
            "source": f"Yahoo Finance:{cfg.yahoo}:chart:1d:1m",
            "note": "",
        }

    meta = chart.get("meta") or {}
    price = safe_float(meta.get("regularMarketPrice"))
    market_time = safe_float(meta.get("regularMarketTime"))
    if price is None or market_time is None:
        raise RuntimeError("Yahoo chart has no usable quote")
    stamp = datetime.fromtimestamp(market_time, tz=timezone.utc).astimezone(WARSAW)
    return {
        "price": price,
        "timestamp": stamp.isoformat(timespec="seconds"),
        "source": f"Yahoo Finance:{cfg.yahoo}:regularMarketPrice",
        "note": "",
    }


def stooq_quote(instrument_id: str) -> Dict[str, Any]:
    cfg = INSTRUMENTS[instrument_id]
    if not cfg.stooq:
        raise RuntimeError("Stooq not configured")
    symbol = urllib.parse.quote(cfg.stooq)
    url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
    text = request_bytes(url).decode("utf-8", errors="ignore")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise RuntimeError("empty Stooq CSV")
    row = rows[-1]
    price = safe_float(row.get("Close"))
    date_text = str(row.get("Date") or "").strip()
    time_text = str(row.get("Time") or "").strip()
    if price is None or not date_text or not time_text:
        raise RuntimeError("Stooq quote incomplete")
    stamp = datetime.fromisoformat(f"{date_text}T{time_text}").replace(tzinfo=WARSAW)
    return {
        "price": price,
        "timestamp": stamp.isoformat(timespec="seconds"),
        "source": f"Stooq:{cfg.stooq}",
        "note": "server-side fallback",
    }


def coinbase_quote() -> Dict[str, Any]:
    url = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
    data = json.loads(request_bytes(url).decode("utf-8"))
    price = safe_float(data.get("price"))
    stamp = parse_iso(data.get("time"))
    if price is None or stamp is None:
        raise RuntimeError("Coinbase quote incomplete")
    return {
        "price": price,
        "timestamp": stamp.isoformat(timespec="seconds"),
        "source": "Coinbase:BTC-USD",
        "note": "server-side fallback",
    }


def providers(instrument_id: str) -> list[Callable[[], Dict[str, Any]]]:
    result: list[Callable[[], Dict[str, Any]]] = [lambda: yahoo_quote(instrument_id)]
    if instrument_id == "btcusd":
        result.append(coinbase_quote)
    elif INSTRUMENTS[instrument_id].stooq:
        result.append(lambda: stooq_quote(instrument_id))
    return result


def newest_valid(instrument_id: str) -> tuple[Optional[Dict[str, Any]], list[str]]:
    candidates: list[Dict[str, Any]] = []
    errors: list[str] = []
    for provider in providers(instrument_id):
        try:
            quote = provider()
            if valid_quote(instrument_id, quote):
                candidates.append(quote)
            else:
                errors.append(f"{getattr(provider, '__name__', 'provider')}: invalid quote")
        except Exception as exc:
            errors.append(f"{getattr(provider, '__name__', 'provider')}: {exc}")
    if not candidates:
        return None, errors
    candidates.sort(key=lambda item: parse_iso(item.get("timestamp")) or datetime.min.replace(tzinfo=WARSAW), reverse=True)
    return candidates[0], errors


def refresh_one(instrument_id: str, previous: Dict[str, Any]) -> Dict[str, Any]:
    cfg = INSTRUMENTS[instrument_id]
    candidate, errors = newest_valid(instrument_id)
    old = previous.get(instrument_id) if isinstance(previous, dict) else None
    old = dict(old) if isinstance(old, dict) else None

    chosen = candidate
    if old and valid_quote(instrument_id, old):
        old_stamp = parse_iso(old.get("current_price_updated_at") or old.get("timestamp"))
        new_stamp = parse_iso(candidate.get("timestamp")) if candidate else None
        if new_stamp is None or (old_stamp is not None and old_stamp > new_stamp):
            chosen = old

    attempt_at = now_local().isoformat(timespec="seconds")
    if not chosen:
        return {
            "price": None,
            "timestamp": attempt_at,
            "current_price_updated_at": attempt_at,
            "source": "BriefRooms fast feed",
            "fresh": False,
            "last_attempt_at": attempt_at,
            "note": "; ".join(errors[-3:]) or "no usable quote",
        }

    stamp = parse_iso(chosen.get("current_price_updated_at") or chosen.get("timestamp"))
    age = now_local() - stamp if stamp else timedelta.max
    fresh = timedelta(seconds=-60) <= age <= cfg.max_age
    note = str(chosen.get("note") or "")
    if errors and not fresh:
        note = "; ".join(errors[-3:])
    return {
        "price": safe_float(chosen.get("price")),
        "timestamp": stamp.isoformat(timespec="seconds") if stamp else str(chosen.get("timestamp") or attempt_at),
        "current_price_updated_at": stamp.isoformat(timespec="seconds") if stamp else str(chosen.get("timestamp") or attempt_at),
        "source": str(chosen.get("source") or "BriefRooms fast feed"),
        "fresh": fresh,
        "last_attempt_at": attempt_at,
        "note": note,
    }


def main() -> None:
    previous_payload = load_previous()
    previous_prices = previous_payload.get("prices") if isinstance(previous_payload, dict) else {}
    previous_prices = previous_prices if isinstance(previous_prices, dict) else {}

    prices: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(INSTRUMENTS)) as executor:
        futures = {
            executor.submit(refresh_one, instrument_id, previous_prices): instrument_id
            for instrument_id in INSTRUMENTS
        }
        for future in as_completed(futures):
            instrument_id = futures[future]
            prices[instrument_id] = future.result()

    ordered_prices = {key: prices[key] for key in INSTRUMENTS}
    payload = {
        "updated_at": now_local().isoformat(timespec="seconds"),
        "owner": "weekly-live-prices-fast",
        "prices": ordered_prices,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    status = ", ".join(
        f"{key}={ordered_prices[key]['price']} fresh={ordered_prices[key]['fresh']} src={ordered_prices[key]['source']}"
        for key in INSTRUMENTS
    )
    print(f"Updated {OUT}: {status}")


if __name__ == "__main__":
    main()
