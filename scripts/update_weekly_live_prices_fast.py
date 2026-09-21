#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import io
import json
import math
import re
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
EASTERN = ZoneInfo("America/New_York")
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
    "sp500_futures": Instrument("ES=F", "es.f", 500.0, 100_000.0, timedelta(minutes=45)),
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
            "User-Agent": "Mozilla/5.0 BriefRoomsWeeklyLive/1.1",
            "Accept": "application/json,text/html,text/plain,text/csv,*/*",
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


def third_friday(year: int, month: int) -> datetime:
    first = datetime(year, month, 1, tzinfo=WARSAW)
    days_to_friday = (4 - first.weekday()) % 7
    return first + timedelta(days=days_to_friday + 14)


def next_quarter(year: int, month: int) -> tuple[int, int]:
    if month == 3:
        return year, 6
    if month == 6:
        return year, 9
    if month == 9:
        return year, 12
    return year + 1, 3


def active_es_contract(at: Optional[datetime] = None) -> tuple[str, str]:
    current = (at or now_local()).astimezone(WARSAW)
    quarters = (3, 6, 9, 12)
    year = current.year
    future_quarters = [candidate for candidate in quarters if current.month <= candidate]
    if future_quarters:
        month = future_quarters[0]
    else:
        year += 1
        month = 3

    if current.month == month:
        expiry = third_friday(year, month)
        roll_start = expiry - timedelta(days=8)
        today = datetime(current.year, current.month, current.day, tzinfo=WARSAW)
        if today >= roll_start:
            year, month = next_quarter(year, month)

    code = {3: "H", 6: "M", 9: "U", 12: "Z"}[month]
    yy = str(year)[-2:]
    return f"ES{code}{yy}.CME", f"ES {code}{yy}"


def active_es_yahoo_symbol(at: Optional[datetime] = None) -> str:
    return active_es_contract(at)[0]


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


def yahoo_quote(instrument_id: str, yahoo_symbol: Optional[str] = None) -> Dict[str, Any]:
    cfg = INSTRUMENTS[instrument_id]
    raw_symbol = yahoo_symbol or cfg.yahoo
    symbol = urllib.parse.quote(raw_symbol, safe="")
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
            "source": f"Yahoo Finance:{raw_symbol}:chart:1d:1m",
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
        "source": f"Yahoo Finance:{raw_symbol}:regularMarketPrice",
        "note": "",
    }


def esignal_quote() -> Dict[str, Any]:
    yahoo_symbol, esignal_symbol = active_es_contract()
    query = urllib.parse.urlencode({"symbol": esignal_symbol, "types": "future"})
    url = f"https://quotes.esignal.com/esignalprod/quote.action?{query}"
    markup = request_bytes(url).decode("utf-8", errors="ignore")
    text = re.sub(r"<script[\s\S]*?</script>", " ", markup, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()

    price_match = re.search(r"Last:\s*([0-9,]+(?:\.[0-9]+)?)", text, re.I)
    time_match = re.search(
        r"Time of last trade:\s*([A-Za-z]{3}\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2})\s+(?:EST|EDT)",
        text,
        re.I,
    )
    if not price_match or not time_match:
        raise RuntimeError("eSignal active ES quote incomplete")

    price = safe_float(price_match.group(1).replace(",", ""))
    if price is None:
        raise RuntimeError("eSignal active ES price invalid")
    naive = datetime.strptime(time_match.group(1), "%b %d %Y %H:%M:%S")
    stamp = naive.replace(tzinfo=EASTERN).astimezone(WARSAW)
    return {
        "price": price,
        "timestamp": stamp.isoformat(timespec="seconds"),
        "source": f"eSignal delayed:{esignal_symbol}",
        "note": f"active quarterly ES contract {yahoo_symbol}; delayed CME quote",
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


def fxapi_eurusd_quote() -> Dict[str, Any]:
    url = "https://fxapi.app/api/EUR/USD.json"
    data = json.loads(request_bytes(url).decode("utf-8"))
    price = safe_float(data.get("rate"))
    stamp = parse_iso(data.get("timestamp"))
    if price is None or stamp is None:
        raise RuntimeError("fxapi.app EUR/USD quote incomplete")
    return {
        "price": price,
        "timestamp": stamp.isoformat(timespec="seconds"),
        "source": "fxapi.app:EUR/USD",
        "note": "same primary EUR/USD feed as Daily",
    }


def currency_exchange_tool_eurusd_quote() -> Dict[str, Any]:
    url = "https://www.currencyexchangetool.com/api/v1/convert?amount=1&from=EUR&to=USD"
    data = json.loads(request_bytes(url).decode("utf-8"))
    if not data or data.get("success") is False:
        raise RuntimeError("Currency Exchange Tool EUR/USD API error")
    price = safe_float(data.get("rate") if data.get("rate") is not None else data.get("result"))
    stamp = parse_iso(data.get("updatedAt") or data.get("updated_at") or data.get("timestamp") or data.get("time"))
    if price is None or stamp is None:
        raise RuntimeError("Currency Exchange Tool EUR/USD quote incomplete")
    return {
        "price": price,
        "timestamp": stamp.isoformat(timespec="seconds"),
        "source": "Currency Exchange Tool:EUR/USD",
        "note": "same fallback EUR/USD feed as Daily",
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
    if instrument_id == "eurusd":
        return [
            fxapi_eurusd_quote,
            currency_exchange_tool_eurusd_quote,
        ]

    if instrument_id == "sp500_futures":
        return [
            esignal_quote,
            lambda: yahoo_quote(instrument_id, active_es_yahoo_symbol()),
            lambda: yahoo_quote(instrument_id),
            lambda: stooq_quote(instrument_id),
        ]

    result: list[Callable[[], Dict[str, Any]]] = [lambda: yahoo_quote(instrument_id)]
    if instrument_id == "btcusd":
        result.append(coinbase_quote)
    elif INSTRUMENTS[instrument_id].stooq:
        result.append(lambda: stooq_quote(instrument_id))
    return result


def newest_valid(instrument_id: str) -> tuple[Optional[Dict[str, Any]], list[str]]:
    candidates: list[Dict[str, Any]] = []
    errors: list[str] = []
    cfg = INSTRUMENTS[instrument_id]
    for provider in providers(instrument_id):
        try:
            quote = provider()
            if valid_quote(instrument_id, quote):
                candidates.append(quote)
                if instrument_id == "eurusd":
                    age = quote_age(quote)
                    if timedelta(seconds=-60) <= age <= cfg.max_age:
                        return quote, errors
            else:
                errors.append(f"{getattr(provider, '__name__', 'provider')}: invalid quote")
        except Exception as exc:
            errors.append(f"{getattr(provider, '__name__', 'provider')}: {exc}")

    if not candidates:
        return None, errors
    candidates.sort(
        key=lambda item: parse_iso(item.get("timestamp")) or datetime.min.replace(tzinfo=WARSAW),
        reverse=True,
    )
    return candidates[0], errors


def refresh_one(instrument_id: str, previous: Dict[str, Any]) -> Dict[str, Any]:
    cfg = INSTRUMENTS[instrument_id]
    candidate, errors = newest_valid(instrument_id)
    old = previous.get(instrument_id) if isinstance(previous, dict) else None
    old = dict(old) if isinstance(old, dict) else None

    chosen = candidate
    old_source = str((old or {}).get("source") or "")
    old_aligned_for_eurusd = (
        old_source.startswith("fxapi.app:")
        or old_source.startswith("Currency Exchange Tool:")
    )
    old_eligible = instrument_id != "eurusd" or old_aligned_for_eurusd
    if old and old_eligible and valid_quote(instrument_id, old):
        old_stamp = parse_iso(old.get("current_price_updated_at") or old.get("timestamp"))
        new_stamp = parse_iso(candidate.get("timestamp")) if candidate else None
        candidate_fresh = candidate is not None and timedelta(seconds=-60) <= quote_age(candidate) <= cfg.max_age
        if instrument_id == "eurusd" and candidate_fresh:
            chosen = candidate
        elif new_stamp is None or (old_stamp is not None and old_stamp > new_stamp):
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
            "note": "; ".join(errors[-4:]) or "no usable quote",
        }

    stamp = parse_iso(chosen.get("current_price_updated_at") or chosen.get("timestamp"))
    age = now_local() - stamp if stamp else timedelta.max
    fresh = timedelta(seconds=-60) <= age <= cfg.max_age
    note = str(chosen.get("note") or "")
    if errors and not fresh:
        note = "; ".join(errors[-4:])
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
