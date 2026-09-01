#!/usr/bin/env python3
"""Resilient market-data layer for the GPW Daily Pick.

Provider A is Yahoo Finance. Provider B is Stooq. Historical provider recovery
remains separate from execution data. Every returned execution snapshot is
validated by the canonical P0.4 gpw-data-gates-v1 contract before consumers can
use it for Opening Confirmation, repricing or final publication.
"""
from __future__ import annotations

import csv
import io
import json
import os
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

try:
    from scripts import gpw_data_gates as gates
    from scripts import gpw_daily_pick as gpw
except ModuleNotFoundError:
    import gpw_data_gates as gates
    import gpw_daily_pick as gpw


_ORIGINAL_YAHOO_FETCHER = gpw.fetch_yahoo_bars
MAX_OPENING_CROSSCHECK_DEVIATION = 0.02


@dataclass(frozen=True)
class OpeningQuote:
    provider: str
    symbol: str
    day: date
    observed_at: datetime
    open: float
    high: float
    low: float
    last: float
    volume: int


def _stooq_symbol(symbol: str) -> str:
    return symbol.removesuffix(".WA").upper()


def _parse_number(value: Any) -> float:
    text = str(value or "").strip().replace(" ", "").replace(",", ".")
    if not text or text.lower() in {"n/d", "nd", "nan", "none"}:
        raise ValueError("missing numeric value")
    return float(text)


def _parse_volume(value: Any) -> int:
    text = str(value or "").strip().replace(" ", "").replace(",", ".").lower()
    if not text or text in {"n/d", "nd", "nan", "none"}:
        return 0
    multiplier = 1
    if text.endswith("k"):
        multiplier, text = 1_000, text[:-1]
    elif text.endswith("m"):
        multiplier, text = 1_000_000, text[:-1]
    return int(float(text) * multiplier)


def _csv_rows(payload: bytes) -> list[dict[str, str]]:
    text = payload.decode("utf-8-sig", errors="replace").strip()
    if not text or "exceeded" in text.lower() or "error" in text.lower():
        raise gpw.PublicationError("Stooq zwrócił pustą odpowiedź lub komunikat błędu.")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise gpw.PublicationError("Stooq nie zwrócił żadnych rekordów CSV.")
    return rows


def fetch_stooq_daily_bars(symbol: str, *, range_value: str = "6mo") -> list[gpw.Bar]:
    days = {"3mo": 120, "6mo": 240, "1y": 400}.get(range_value, 240)
    end = gpw.now_warsaw().date()
    start = end - timedelta(days=days)
    params = {
        "s": _stooq_symbol(symbol),
        "d1": start.strftime("%Y%m%d"),
        "d2": end.strftime("%Y%m%d"),
        "i": "d",
    }
    api_key = str(os.environ.get("STOOQ_API_KEY") or "").strip()
    if api_key:
        params["apikey"] = api_key
    url = "https://stooq.pl/q/d/l/?" + urllib.parse.urlencode(params)
    rows = _csv_rows(gpw.request_bytes(url))
    bars: list[gpw.Bar] = []
    for row in rows:
        normalized = {str(key or "").strip().lower(): value for key, value in row.items()}
        try:
            day = datetime.strptime(str(normalized.get("date") or ""), "%Y-%m-%d").date()
            bars.append(
                gpw.Bar(
                    day=day,
                    open=_parse_number(normalized.get("open")),
                    high=_parse_number(normalized.get("high")),
                    low=_parse_number(normalized.get("low")),
                    close=_parse_number(normalized.get("close")),
                    volume=_parse_volume(normalized.get("volume")),
                )
            )
        except Exception:
            continue
    bars.sort(key=lambda bar: bar.day)
    minimum = 25 if range_value == "3mo" else 60
    if len(bars) < minimum:
        raise gpw.PublicationError(f"Stooq: za mało poprawnych sesji dla {symbol}: {len(bars)}.")
    return bars


def fetch_resilient_bars(symbol: str, *, range_value: str = "6mo") -> list[gpw.Bar]:
    failures: list[str] = []
    try:
        return _ORIGINAL_YAHOO_FETCHER(symbol, range_value=range_value)
    except Exception as exc:
        failures.append(f"Yahoo:{type(exc).__name__}")
    try:
        return fetch_stooq_daily_bars(symbol, range_value=range_value)
    except Exception as exc:
        failures.append(f"Stooq:{type(exc).__name__}")
    raise gpw.PublicationError(
        f"Brak historii rynkowej {symbol} z niezależnych źródeł ({'; '.join(failures)})."
    )


def fetch_yahoo_opening_quote(symbol: str, *, now: datetime) -> OpeningQuote:
    encoded = urllib.parse.quote(symbol, safe="")
    params = urllib.parse.urlencode(
        {"range": "1d", "interval": "1m", "events": "history", "includePrePost": "false"}
    )
    failures: list[str] = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            payload = json.loads(gpw.request_bytes(f"https://{host}/v8/finance/chart/{encoded}?{params}"))
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not result:
                raise ValueError("empty chart")
            stamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            points: list[tuple[datetime, float, float, float, float, int]] = []
            for index, stamp in enumerate(stamps):
                values = {
                    key: (quote.get(key) or [None] * len(stamps))[index]
                    for key in ("open", "high", "low", "close", "volume")
                }
                if any(values[key] is None for key in ("open", "high", "low", "close")):
                    continue
                observed = datetime.fromtimestamp(int(stamp), gpw.WARSAW)
                if observed.date() != now.date():
                    continue
                points.append(
                    (
                        observed,
                        float(values["open"]),
                        float(values["high"]),
                        float(values["low"]),
                        float(values["close"]),
                        int(values["volume"] or 0),
                    )
                )
            if not points:
                raise ValueError("no current-session points")
            first, latest = points[0], points[-1]
            return OpeningQuote(
                provider="Yahoo",
                symbol=symbol,
                day=now.date(),
                observed_at=latest[0],
                open=first[1],
                high=max(point[2] for point in points),
                low=min(point[3] for point in points),
                last=latest[4],
                volume=sum(max(point[5], 0) for point in points),
            )
        except Exception as exc:
            failures.append(f"{host}:{type(exc).__name__}")
    raise gpw.PublicationError(
        f"Yahoo intraday nie zwrócił świeżej ceny {symbol} ({'; '.join(failures)})."
    )


def fetch_stooq_opening_quote(symbol: str, *, now: datetime) -> OpeningQuote:
    params = urllib.parse.urlencode({"s": _stooq_symbol(symbol), "f": "sd2t2ohlcv", "h": "", "e": "csv"})
    rows = _csv_rows(gpw.request_bytes(f"https://stooq.pl/q/l/?{params}"))
    row = {str(key or "").strip().lower(): value for key, value in rows[-1].items()}
    day_text = str(row.get("date") or "").strip()
    time_text = str(row.get("time") or "").strip()
    day = datetime.strptime(day_text, "%Y-%m-%d").date()
    if day != now.date():
        raise gpw.PublicationError(f"Stooq zwrócił nieaktualną sesję {symbol}: {day.isoformat()}.")
    observed = datetime.strptime(
        f"{day_text} {time_text or '09:00:00'}", "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=gpw.WARSAW)
    return OpeningQuote(
        provider="Stooq",
        symbol=symbol,
        day=day,
        observed_at=observed,
        open=_parse_number(row.get("open")),
        high=_parse_number(row.get("high")),
        low=_parse_number(row.get("low")),
        last=_parse_number(row.get("close")),
        volume=_parse_volume(row.get("volume")),
    )


def opening_snapshot(symbol: str, *, now: datetime) -> dict[str, Any]:
    quotes: list[OpeningQuote] = []
    errors: list[str] = []
    for provider_name, fetcher in (
        ("Yahoo", fetch_yahoo_opening_quote),
        ("Stooq", fetch_stooq_opening_quote),
    ):
        try:
            quote = fetcher(symbol, now=now)
            if quote.last <= 0 or quote.open <= 0:
                raise ValueError("non-positive quote")
            quotes.append(quote)
        except Exception as exc:
            errors.append(f"{provider_name}:{type(exc).__name__}")
    if not quotes:
        raise gpw.PublicationError(
            f"Brak dzisiejszego kwotowania {symbol} po otwarciu ({'; '.join(errors)})."
        )

    primary = quotes[0]
    crosscheck: dict[str, Any] = {
        "available_providers": [quote.provider for quote in quotes],
        "errors": errors,
        "status": "single_source" if len(quotes) == 1 else "confirmed",
    }
    if len(quotes) >= 2:
        deviation = abs(quotes[0].last / quotes[1].last - 1.0)
        crosscheck["last_price_deviation"] = round(deviation, 5)
        if deviation > MAX_OPENING_CROSSCHECK_DEVIATION:
            raise gpw.PublicationError(
                f"Rozbieżność Yahoo/Stooq dla {symbol} wynosi {deviation:.2%}."
            )
        primary = max(quotes, key=lambda quote: quote.observed_at)

    snapshot = {
        "provider": primary.provider,
        "symbol": symbol,
        "date": primary.day.isoformat(),
        "observed_at": primary.observed_at.isoformat(timespec="seconds"),
        "open": gpw.round2(primary.open),
        "high": gpw.round2(primary.high),
        "low": gpw.round2(primary.low),
        "last": gpw.round2(primary.last),
        "volume": primary.volume,
        "crosscheck": crosscheck,
    }
    snapshot["data_gate"] = gates.execution_gate(
        snapshot,
        now=now,
        config=gpw.load_config(),
    )
    return snapshot


def reprice_transaction(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Anchor entry/SL/TP only to a P0.4-accepted current-session quote."""
    if payload.get("decision") != "TRANSAKCJA":
        return payload
    selection = payload.get("selection") or {}
    symbol = str(selection.get("symbol") or "")
    if not symbol:
        return payload

    try:
        snapshot = opening_snapshot(symbol, now=now)
        execution_gate = snapshot.get("data_gate") or gates.execution_gate(
            snapshot,
            now=now,
            config=gpw.load_config(),
        )
    except Exception as exc:
        failed = gpw.failure_payload(
            now,
            gpw.load_config(),
            f"Nie udało się potwierdzić świeżej dzisiejszej ceny wejścia: {type(exc).__name__}.",
            "opening_quote",
        )
        failed["data_quality"]["opening_quote_error"] = str(exc)
        failed["data_quality"]["data_gate_engine"] = gates.ENGINE
        return failed

    old_reference = float(selection.get("reference_price") or snapshot["last"])
    old_stop = float(selection.get("stop") or old_reference * 0.98)
    risk_fraction = max((old_reference - old_stop) / max(old_reference, 0.01), 0.012)
    risk_fraction = min(risk_fraction, 0.07)
    reference = float(snapshot["last"])
    reward_risk = float(selection.get("reward_risk") or 1.8)
    stop = reference * (1.0 - risk_fraction)
    target = reference + (reference - stop) * reward_risk

    selection["reference_price"] = gpw.round2(reference)
    selection["entry_zone"] = [gpw.round2(reference * 0.997), gpw.round2(reference * 1.006)]
    selection["stop"] = gpw.round2(stop)
    selection["target"] = gpw.round2(target)
    selection["activation"] = (
        "Setup po otwarciu: wejście tylko w podanej strefie od 09:05; powyżej górnej granicy nie gonić ceny."
    )
    selection["market_snapshot"] = snapshot
    selection["execution_data_gate"] = execution_gate
    payload.setdefault("data_quality", {})["opening_quote"] = snapshot["crosscheck"]["status"]
    payload["data_quality"]["execution_quote_age_minutes"] = execution_gate.get("age_minutes")
    payload["data_quality"]["data_gate_engine"] = gates.ENGINE
    return payload
