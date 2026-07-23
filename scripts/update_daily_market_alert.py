#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import feedparser
import pandas_market_calendars as mcal
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "investments" / "daily_market_alert.json"
HISTORY_DIR = ROOT / "data" / "investments" / "daily_alert_history"
NY = ZoneInfo("America/New_York")
WARSAW = ZoneInfo("Europe/Warsaw")
UTC = timezone.utc

sys.path.insert(0, str(ROOT / "scripts"))
from comment_quality import get_ai_runtime, request_json_completion  # noqa: E402

INSTRUMENTS = {
    "sp500": {
        "name": "S&P 500",
        "symbol": "^GSPC",
        "asset_class": {"pl": "Indeks akcji", "en": "Equity index"},
        "kind": "percent",
        "material_move": 0.45,
        "queries": [
            "S&P 500 Wall Street stocks market catalyst when:1d",
            "S&P 500 Treasury yields oil Big Tech earnings when:1d",
            "site:reuters.com S&P 500 Wall Street when:1d",
        ],
    },
    "brent": {
        "name": "Brent",
        "symbol": "BZ=F",
        "asset_class": {"pl": "Ropa naftowa", "en": "Crude oil"},
        "kind": "percent",
        "material_move": 1.0,
        "queries": [
            "Brent oil price supply OPEC Middle East shipping when:1d",
            "oil tanker Red Sea Hormuz Brent when:1d",
            "site:reuters.com Brent oil when:1d",
        ],
    },
    "us10y": {
        "name": "US 10Y",
        "symbol": "^TNX",
        "asset_class": {"pl": "Rentowność obligacji", "en": "Bond yield"},
        "kind": "basis_points",
        "material_move": 5.0,
        "queries": [
            "US 10-year Treasury yield Fed inflation oil when:1d",
            "Treasury yields bond market Federal Reserve when:1d",
            "site:reuters.com Treasury yields when:1d",
        ],
    },
}

PUBLISHER_PRIORITY = {
    "Reuters": 100,
    "Associated Press": 95,
    "AP News": 95,
    "Bloomberg": 92,
    "Financial Times": 90,
    "The Wall Street Journal": 90,
    "CNBC": 85,
    "MarketWatch": 82,
    "Yahoo Finance": 78,
    "Barron's": 78,
    "BBC": 75,
    "The Guardian": 72,
    "Investing.com": 65,
    "OilPrice.com": 62,
}

USER_AGENT = "BriefRoomsDailyMarketAlert/1.0 (+https://briefrooms.com)"
MAX_NEWS_AGE_HOURS = 40
MAX_NEWS_PER_INSTRUMENT = 10


@dataclass
class MarketSnapshot:
    instrument_id: str
    name: str
    symbol: str
    price: float
    previous_close: float
    change_numeric: float
    price_text: str
    change_text: str
    direction: str
    support: float
    resistance: float
    next_support: float
    next_resistance: float
    support_text: str
    resistance_text: str
    next_support_text: str
    next_resistance_text: str
    atr: float
    five_day_change: float
    twenty_day_change: float
    volatility_20d: float

    def prompt_dict(self) -> dict[str, Any]:
        return {
            "id": self.instrument_id,
            "name": self.name,
            "price": self.price,
            "price_text": self.price_text,
            "daily_change": self.change_numeric,
            "change_text": self.change_text,
            "direction": self.direction,
            "support": self.support_text,
            "resistance": self.resistance_text,
            "next_support": self.next_support_text,
            "next_resistance": self.next_resistance_text,
            "five_day_change": round(self.five_day_change, 3),
            "twenty_day_change": round(self.twenty_day_change, 3),
            "volatility_20d": round(self.volatility_20d, 3),
        }


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_utc() -> datetime:
    override = os.getenv("BR_ALERT_NOW", "").strip()
    if override:
        value = datetime.fromisoformat(override.replace("Z", "+00:00"))
        return value.astimezone(UTC)
    return datetime.now(UTC)


def session_schedule(moment: datetime) -> tuple[datetime, datetime] | None:
    calendar = mcal.get_calendar("NYSE")
    local_date = moment.astimezone(NY).date()
    schedule = calendar.schedule(start_date=local_date, end_date=local_date)
    if schedule.empty:
        return None
    market_open = schedule.iloc[0]["market_open"].to_pydatetime().astimezone(UTC)
    market_close = schedule.iloc[0]["market_close"].to_pydatetime().astimezone(UTC)
    return market_open, market_close


def resolve_mode(requested: str, moment: datetime) -> str:
    if requested in {"open", "preclose"}:
        return requested
    schedule = session_schedule(moment)
    if not schedule:
        return "skip"
    market_open, market_close = schedule
    targets = {
        "open": market_open + timedelta(minutes=15),
        "preclose": market_close - timedelta(hours=1),
    }
    tolerance = timedelta(minutes=32)
    matches = [
        (abs(moment - target), mode)
        for mode, target in targets.items()
        if abs(moment - target) <= tolerance
    ]
    return min(matches)[1] if matches else "skip"


def yahoo_chart(symbol: str, *, chart_range: str, interval: str) -> dict[str, Any]:
    encoded = quote(symbol, safe="")
    last_error: Exception | None = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{encoded}"
        for attempt in range(3):
            try:
                response = requests.get(
                    url,
                    params={"range": chart_range, "interval": interval, "includePrePost": "false"},
                    headers={"User-Agent": USER_AGENT},
                    timeout=25,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise RuntimeError(f"Yahoo temporary status {response.status_code}")
                response.raise_for_status()
                result = (response.json().get("chart", {}).get("result") or [None])[0]
                if result:
                    return result
                raise RuntimeError(f"Yahoo returned no chart for {symbol}")
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Yahoo chart failed for {symbol}: {last_error}") from last_error


def finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def latest_intraday(symbol: str) -> tuple[float, float, datetime]:
    result = yahoo_chart(symbol, chart_range="5d", interval="1m")
    meta = result.get("meta", {}) or {}
    quote_block = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote_block.get("close") or []
    timestamps = result.get("timestamp") or []

    points: list[tuple[datetime, float]] = []
    for timestamp, close in zip(timestamps, closes):
        value = finite(close)
        if value is None:
            continue
        points.append((datetime.fromtimestamp(int(timestamp), tz=UTC), value))

    current = points[-1][1] if points else finite(meta.get("regularMarketPrice"))
    stamp = points[-1][0] if points else datetime.fromtimestamp(
        int(meta.get("regularMarketTime") or now_utc().timestamp()), tz=UTC
    )
    previous_close = finite(meta.get("chartPreviousClose") or meta.get("previousClose"))
    if current is None or previous_close in (None, 0):
        raise RuntimeError(f"Incomplete intraday quote for {symbol}")
    return current, previous_close, stamp


def daily_history(symbol: str) -> list[dict[str, float]]:
    result = yahoo_chart(symbol, chart_range="1y", interval="1d")
    quote_block = (result.get("indicators", {}).get("quote") or [{}])[0]
    rows: list[dict[str, float]] = []
    for index, timestamp in enumerate(result.get("timestamp") or []):
        values = {}
        for key in ("open", "high", "low", "close"):
            sequence = quote_block.get(key) or []
            values[key] = finite(sequence[index]) if index < len(sequence) else None
        if all(values[key] is not None for key in ("high", "low", "close")):
            rows.append(
                {
                    "timestamp": float(timestamp),
                    "open": float(values["open"] or values["close"]),
                    "high": float(values["high"]),
                    "low": float(values["low"]),
                    "close": float(values["close"]),
                }
            )
    if len(rows) < 40:
        raise RuntimeError(f"Not enough daily history for {symbol}")
    return rows


def convert_tnx(value: float, instrument_id: str) -> float:
    return value / 10.0 if instrument_id == "us10y" else value


def true_range(rows: list[dict[str, float]], instrument_id: str) -> float:
    values: list[float] = []
    for previous, current in zip(rows[-16:-1], rows[-15:]):
        high = convert_tnx(current["high"], instrument_id)
        low = convert_tnx(current["low"], instrument_id)
        previous_close = convert_tnx(previous["close"], instrument_id)
        values.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return sum(values) / len(values) if values else 0.0


def local_extrema(rows: list[dict[str, float]], key: str, instrument_id: str) -> list[float]:
    values = [convert_tnx(row[key], instrument_id) for row in rows[-90:]]
    out: list[float] = []
    for index in range(2, len(values) - 2):
        window = values[index - 2:index + 3]
        if key == "low" and values[index] == min(window):
            out.append(values[index])
        if key == "high" and values[index] == max(window):
            out.append(values[index])
    return out


def moving_average(rows: list[dict[str, float]], length: int, instrument_id: str) -> float:
    closes = [convert_tnx(row["close"], instrument_id) for row in rows[-length:]]
    return sum(closes) / len(closes)


def cluster_levels(levels: list[float], tolerance: float) -> list[float]:
    if not levels:
        return []
    ordered = sorted(levels)
    clusters: list[list[float]] = [[ordered[0]]]
    for value in ordered[1:]:
        center = sum(clusters[-1]) / len(clusters[-1])
        if abs(value - center) <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def derive_levels(
    rows: list[dict[str, float]], current: float, instrument_id: str
) -> tuple[float, float, float, float, float]:
    atr = true_range(rows, instrument_id)
    if atr <= 0:
        atr = max(abs(current) * 0.01, 0.01)

    supports = local_extrema(rows, "low", instrument_id)
    resistances = local_extrema(rows, "high", instrument_id)
    supports.extend(
        [
            convert_tnx(rows[-2]["low"], instrument_id),
            min(convert_tnx(row["low"], instrument_id) for row in rows[-20:]),
            moving_average(rows, 20, instrument_id),
            moving_average(rows, 50, instrument_id),
        ]
    )
    resistances.extend(
        [
            convert_tnx(rows[-2]["high"], instrument_id),
            max(convert_tnx(row["high"], instrument_id) for row in rows[-20:]),
            moving_average(rows, 20, instrument_id),
            moving_average(rows, 50, instrument_id),
        ]
    )
    if len(rows) >= 200:
        average_200 = moving_average(rows, 200, instrument_id)
        supports.append(average_200)
        resistances.append(average_200)

    tolerance = max(atr * 0.28, abs(current) * 0.0008)
    below = [level for level in cluster_levels(supports + resistances, tolerance) if level < current - tolerance * 0.15]
    above = [level for level in cluster_levels(supports + resistances, tolerance) if level > current + tolerance * 0.15]

    if not below:
        below = [current - atr]
    if not above:
        above = [current + atr]

    below = sorted(set(round(value, 8) for value in below), reverse=True)
    above = sorted(set(round(value, 8) for value in above))
    support = below[0]
    next_support = below[1] if len(below) > 1 else support - atr
    resistance = above[0]
    next_resistance = above[1] if len(above) > 1 else resistance + atr
    return support, resistance, next_support, next_resistance, atr


def percent_change(rows: list[dict[str, float]], days: int, instrument_id: str) -> float:
    if len(rows) <= days:
        return 0.0
    start = convert_tnx(rows[-days - 1]["close"], instrument_id)
    end = convert_tnx(rows[-1]["close"], instrument_id)
    return 0.0 if start == 0 else (end / start - 1.0) * 100.0


def volatility(rows: list[dict[str, float]], instrument_id: str) -> float:
    closes = [convert_tnx(row["close"], instrument_id) for row in rows[-21:]]
    returns = [(b / a - 1.0) * 100.0 for a, b in zip(closes, closes[1:]) if a]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance)


def round_level(value: float, instrument_id: str) -> float:
    if instrument_id == "sp500":
        return round(value / 5.0) * 5.0
    if instrument_id == "brent":
        return round(value * 2.0) / 2.0
    return round(value, 2)


def format_number(value: float, instrument_id: str, *, level: bool = False) -> str:
    if instrument_id == "sp500":
        rounded = round_level(value, instrument_id) if level else round(value)
        return f"{rounded:,.0f}".replace(",", " ")
    if instrument_id == "brent":
        rounded = round_level(value, instrument_id) if level else round(value, 2)
        return f"{rounded:.2f}".replace(".", ",") + " USD"
    rounded = round_level(value, instrument_id) if level else round(value, 2)
    return f"{rounded:.2f}".replace(".", ",") + "%"


def fetch_snapshot(instrument_id: str) -> MarketSnapshot:
    config = INSTRUMENTS[instrument_id]
    current_raw, previous_raw, _ = latest_intraday(config["symbol"])
    rows = daily_history(config["symbol"])
    current = convert_tnx(current_raw, instrument_id)
    previous_close = convert_tnx(previous_raw, instrument_id)
    support, resistance, next_support, next_resistance, atr = derive_levels(rows, current, instrument_id)
    support = round_level(support, instrument_id)
    resistance = round_level(resistance, instrument_id)
    next_support = round_level(next_support, instrument_id)
    next_resistance = round_level(next_resistance, instrument_id)

    if config["kind"] == "basis_points":
        change_numeric = (current - previous_close) * 100.0
        change_text = f"{change_numeric:+.0f} pb"
    else:
        change_numeric = (current / previous_close - 1.0) * 100.0
        change_text = f"{change_numeric:+.2f}%".replace(".", ",")

    return MarketSnapshot(
        instrument_id=instrument_id,
        name=config["name"],
        symbol=config["symbol"],
        price=current,
        previous_close=previous_close,
        change_numeric=change_numeric,
        price_text=format_number(current, instrument_id),
        change_text=change_text,
        direction="up" if change_numeric > 0 else "down" if change_numeric < 0 else "flat",
        support=support,
        resistance=resistance,
        next_support=next_support,
        next_resistance=next_resistance,
        support_text=format_number(support, instrument_id, level=True),
        resistance_text=format_number(resistance, instrument_id, level=True),
        next_support_text=format_number(next_support, instrument_id, level=True),
        next_resistance_text=format_number(next_resistance, instrument_id, level=True),
        atr=atr,
        five_day_change=percent_change(rows, 5, instrument_id),
        twenty_day_change=percent_change(rows, 20, instrument_id),
        volatility_20d=volatility(rows, instrument_id),
    )


def parse_published(entry: Any) -> datetime | None:
    value = getattr(entry, "published", "") or getattr(entry, "updated", "")
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        return None


def clean_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


def normalize_story_title(value: str) -> str:
    value = re.sub(r"\s+-\s+[^-]{2,70}$", "", value or "")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def fetch_news_for(instrument_id: str, moment: datetime) -> list[dict[str, Any]]:
    config = INSTRUMENTS[instrument_id]
    stories: list[dict[str, Any]] = []
    seen: set[str] = set()
    cutoff = moment - timedelta(hours=MAX_NEWS_AGE_HOURS)

    for query_text in config["queries"]:
        url = (
            "https://news.google.com/rss/search?q="
            + quote(query_text)
            + "&hl=en-US&gl=US&ceid=US:en"
        )
        feed = feedparser.parse(url, request_headers={"User-Agent": USER_AGENT})
        for entry in feed.entries:
            published = parse_published(entry)
            if published and published < cutoff:
                continue
            title = clean_html(getattr(entry, "title", ""))
            if not title:
                continue
            key = normalize_story_title(title)
            if not key or key in seen:
                continue
            seen.add(key)
            source_object = getattr(entry, "source", {}) or {}
            source = (
                source_object.get("title")
                if isinstance(source_object, dict)
                else getattr(source_object, "title", "")
            ) or title.rsplit(" - ", 1)[-1]
            summary = clean_html(getattr(entry, "summary", ""))
            stories.append(
                {
                    "instrument_id": instrument_id,
                    "title": title,
                    "summary": summary[:600],
                    "source": source.strip(),
                    "url": getattr(entry, "link", ""),
                    "published_at": published.isoformat() if published else "",
                    "priority": PUBLISHER_PRIORITY.get(source.strip(), 40),
                }
            )

    stories.sort(key=lambda item: (item["priority"], item["published_at"]), reverse=True)
    return stories[:MAX_NEWS_PER_INSTRUMENT]


def news_candidates(moment: datetime) -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []
    seen: set[str] = set()
    for instrument_id in INSTRUMENTS:
        for story in fetch_news_for(instrument_id, moment):
            key = normalize_story_title(story["title"])
            if key in seen:
                existing = next((item for item in combined if normalize_story_title(item["title"]) == key), None)
                if existing and instrument_id not in existing["instrument_ids"]:
                    existing["instrument_ids"].append(instrument_id)
                continue
            seen.add(key)
            story["instrument_ids"] = [instrument_id]
            combined.append(story)
    combined.sort(key=lambda item: (item["priority"], item["published_at"]), reverse=True)
    for index, item in enumerate(combined):
        item["index"] = index
    return combined[:24]


def prompt_payload(
    snapshots: list[MarketSnapshot],
    candidates: list[dict[str, Any]],
    mode: str,
    previous: dict[str, Any],
) -> dict[str, Any]:
    opening_context = {}
    if mode == "preclose" and previous:
        opening_context = {
            "session_date": previous.get("session_date"),
            "updated_at": previous.get("updated_at"),
            "edition": previous.get("edition"),
            "instruments": [
                {
                    "id": item.get("id"),
                    "price_value": item.get("price_value"),
                    "change_numeric": item.get("change_numeric"),
                    "driver_keys": item.get("driver_keys", []),
                    "scenario_probabilities": item.get("scenario_probabilities", {}),
                }
                for item in previous.get("instruments", [])
            ],
        }
    return {
        "mode": mode,
        "market_data": [snapshot.prompt_dict() for snapshot in snapshots],
        "news_candidates": [
            {
                "index": item["index"],
                "instrument_ids": item["instrument_ids"],
                "title": item["title"],
                "summary": item["summary"],
                "source": item["source"],
                "published_at": item["published_at"],
            }
            for item in candidates
        ],
        "opening_context": opening_context,
    }


def generate_editorial(
    snapshots: list[MarketSnapshot],
    candidates: list[dict[str, Any]],
    mode: str,
    previous: dict[str, Any],
) -> dict[str, Any]:
    runtime = get_ai_runtime()
    if not runtime.available:
        raise RuntimeError("No AI runtime available; refusing to publish an unverified market explanation")

    supplied = prompt_payload(snapshots, candidates, mode, previous)
    system = """
You are the governed market editor for BriefRooms. Produce a calm, evidence-led intraday market alert in Polish and English.
Use ONLY the supplied market data and news candidates. Never invent an event, quote, number, support, resistance, institution action or causal link.
The movement reason must be concise but exhaustive: normally 2 sentences and 240-650 characters in Polish. It must say what is NEW, identify the concrete catalyst, and explain the transmission mechanism into the instrument. Do not merely restate that price rose or fell.
When evidence does not establish one fresh catalyst, explicitly say that no single new catalyst is confirmed and explain the observable cross-asset mechanism without pretending certainty.
Treat causality as the most likely interpretation, not proven fact. Avoid emotional words and buy/sell instructions.
Select source_indexes only from the supplied candidates and only when they directly support the reason.
For each instrument return three probabilities: range, continuation and reversal. Each must be a multiple of 5, between 10 and 70, and sum to 100.
driver_keys must contain 1-4 short stable English topic keys such as oil-supply, fed-path, ai-capex, earnings, inflation, geopolitics.
Return strict JSON with this structure:
{
  "market_regime":{"pl":"...","en":"..."},
  "summary":{"pl":"...","en":"..."},
  "instruments":[
    {
      "id":"sp500|brent|us10y",
      "reason":{"pl":"...","en":"..."},
      "driver_keys":["..."],
      "source_indexes":[0],
      "probabilities":{"range":45,"continuation":35,"reversal":20}
    }
  ],
  "preclose_note":{"pl":"...","en":"..."}
}
preclose_note should be one factual sentence comparing the pre-close picture with the opening context; leave it empty in open mode.
"""
    draft = request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(supplied, ensure_ascii=False)},
        ],
        max_tokens=2600,
        temperature=0.15,
        timeout=60,
    )

    review_system = """
Act as a strict independent market-copy reviewer. Correct the draft using only the supplied market data and news candidates.
Remove unsupported specificity, false causality, stale claims, emotional language and generic descriptions that merely repeat the price move.
Keep concrete new developments when directly supported. Ensure PL and EN say the same thing.
Ensure all three instrument IDs exist exactly once, all source indexes are valid, and probabilities are multiples of 5 between 10 and 70 and sum to 100.
Return the same strict JSON structure and no commentary.
"""
    return request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=[
            {"role": "system", "content": review_system},
            {"role": "user", "content": json.dumps({"evidence": supplied, "draft": draft}, ensure_ascii=False)},
        ],
        max_tokens=2600,
        temperature=0.0,
        review=True,
        timeout=60,
    )


def normalize_probabilities(value: Any) -> dict[str, int]:
    raw = value if isinstance(value, dict) else {}
    keys = ("range", "continuation", "reversal")
    numbers: dict[str, int] = {}
    for key in keys:
        try:
            number = int(round(float(raw.get(key, 0)) / 5.0) * 5)
        except (TypeError, ValueError):
            number = 0
        numbers[key] = min(70, max(10, number))
    total = sum(numbers.values())
    while total != 100:
        if total < 100:
            key = min(keys, key=lambda item: numbers[item])
            if numbers[key] >= 70:
                key = "range"
            numbers[key] += 5
            total += 5
        else:
            key = max(keys, key=lambda item: numbers[item])
            if numbers[key] <= 10:
                key = "range"
            numbers[key] -= 5
            total -= 5
    return numbers


def ensure_localized(value: Any, fallback_pl: str, fallback_en: str) -> dict[str, str]:
    if not isinstance(value, dict):
        return {"pl": fallback_pl, "en": fallback_en}
    pl = re.sub(r"\s+", " ", str(value.get("pl") or "")).strip()
    en = re.sub(r"\s+", " ", str(value.get("en") or "")).strip()
    return {"pl": pl or fallback_pl, "en": en or fallback_en}


def trigger_for(snapshot: MarketSnapshot) -> dict[str, str]:
    if snapshot.instrument_id == "us10y":
        return {
            "pl": (
                f"Wybicie ponad {snapshot.resistance_text} zwiększy presję na wyceny spółek wzrostowych; "
                f"spadek poniżej {snapshot.support_text} byłby sygnałem ulgi dla rynku akcji."
            ),
            "en": (
                f"A break above {snapshot.resistance_text.replace(',', '.')} would increase pressure on growth-stock valuations; "
                f"a move below {snapshot.support_text.replace(',', '.')} would provide relief for equities."
            ),
        }
    return {
        "pl": (
            f"Zamknięcie poniżej {snapshot.support_text} zwiększa ryzyko ruchu w kierunku {snapshot.next_support_text}. "
            f"Trwałe wybicie ponad {snapshot.resistance_text} otwiera przestrzeń do {snapshot.next_resistance_text}."
        ),
        "en": (
            f"A close below {snapshot.support_text.replace(',', '.')} increases the risk of a move toward {snapshot.next_support_text.replace(',', '.')}. "
            f"A sustained break above {snapshot.resistance_text.replace(',', '.')} opens room toward {snapshot.next_resistance_text.replace(',', '.')}."
        ),
    }


def scenario_labels(snapshot: MarketSnapshot, probabilities: dict[str, int]) -> list[dict[str, Any]]:
    range_label = {
        "pl": f"Konsolidacja między {snapshot.support_text} a {snapshot.resistance_text}",
        "en": f"Consolidation between {snapshot.support_text.replace(',', '.')} and {snapshot.resistance_text.replace(',', '.')}",
    }
    if snapshot.direction == "down":
        continuation = {
            "pl": f"Kontynuacja spadku w kierunku {snapshot.next_support_text}",
            "en": f"Continued decline toward {snapshot.next_support_text.replace(',', '.')}",
        }
        reversal = {
            "pl": f"Odwrócenie ruchu i odzyskanie {snapshot.resistance_text}",
            "en": f"Reversal and recovery above {snapshot.resistance_text.replace(',', '.')}",
        }
    else:
        continuation = {
            "pl": f"Kontynuacja wzrostu w kierunku {snapshot.next_resistance_text}",
            "en": f"Continued rise toward {snapshot.next_resistance_text.replace(',', '.')}",
        }
        reversal = {
            "pl": f"Odwrócenie ruchu i zejście pod {snapshot.support_text}",
            "en": f"Reversal and move below {snapshot.support_text.replace(',', '.')}",
        }
    return [
        {"probability": probabilities["range"], "label": range_label},
        {"probability": probabilities["continuation"], "label": continuation},
        {"probability": probabilities["reversal"], "label": reversal},
    ]


def build_alert(
    snapshots: list[MarketSnapshot],
    candidates: list[dict[str, Any]],
    editorial: dict[str, Any],
    mode: str,
    moment: datetime,
) -> dict[str, Any]:
    by_id = {
        str(item.get("id")): item
        for item in editorial.get("instruments", [])
        if isinstance(item, dict)
    }
    candidate_by_index = {item["index"]: item for item in candidates}
    source_indexes: set[int] = set()
    instruments: list[dict[str, Any]] = []

    for snapshot in snapshots:
        item = by_id.get(snapshot.instrument_id)
        if not item:
            raise RuntimeError(f"AI response omitted {snapshot.instrument_id}")
        probabilities = normalize_probabilities(item.get("probabilities"))
        selected_indexes = []
        for raw_index in item.get("source_indexes", []):
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if index in candidate_by_index:
                selected_indexes.append(index)
                source_indexes.add(index)
        reason = ensure_localized(
            item.get("reason"),
            "Brak wystarczających danych do wiarygodnego przypisania jednego nowego katalizatora.",
            "There is not enough evidence to assign one new catalyst reliably.",
        )
        driver_keys = [
            re.sub(r"[^a-z0-9-]+", "-", str(value).lower()).strip("-")
            for value in item.get("driver_keys", [])
        ]
        driver_keys = [value for value in driver_keys if value][:4]

        instruments.append(
            {
                "id": snapshot.instrument_id,
                "name": snapshot.name,
                "asset_class": INSTRUMENTS[snapshot.instrument_id]["asset_class"],
                "price": snapshot.price_text,
                "price_value": round(snapshot.price, 6),
                "change": snapshot.change_text,
                "change_numeric": round(snapshot.change_numeric, 6),
                "change_kind": INSTRUMENTS[snapshot.instrument_id]["kind"],
                "direction": snapshot.direction,
                "reason": reason,
                "driver_keys": driver_keys,
                "source_indexes": selected_indexes,
                "support": snapshot.support_text,
                "support_value": snapshot.support,
                "resistance": snapshot.resistance_text,
                "resistance_value": snapshot.resistance,
                "next_support": snapshot.next_support_text,
                "next_support_value": snapshot.next_support,
                "next_resistance": snapshot.next_resistance_text,
                "next_resistance_value": snapshot.next_resistance,
                "trigger": trigger_for(snapshot),
                "scenario_probabilities": probabilities,
                "scenarios": scenario_labels(snapshot, probabilities),
            }
        )

    sources = []
    for index in sorted(source_indexes):
        story = candidate_by_index[index]
        sources.append(
            {
                "name": f"{story['source']} — {story['title']}",
                "url": story["url"],
                "published_at": story["published_at"],
            }
        )

    session_date = moment.astimezone(NY).date().isoformat()
    return {
        "schema_version": "2.0",
        "session_date": session_date,
        "edition": mode,
        "updated_at": moment.astimezone(WARSAW).isoformat(timespec="seconds"),
        "market_regime": ensure_localized(editorial.get("market_regime"), "Rynek mieszany", "Mixed market"),
        "summary": ensure_localized(
            editorial.get("summary"),
            "Ruchy są oceniane na podstawie bieżących danych cenowych i potwierdzonych informacji.",
            "Moves are assessed from current price data and confirmed information.",
        ),
        "instruments": instruments,
        "sources": sources,
        "preclose_check": None,
        "_editorial_preclose_note": ensure_localized(editorial.get("preclose_note"), "", ""),
    }


def snapshot_from_alert(alert: dict[str, Any]) -> dict[str, Any]:
    return {
        "updated_at": alert.get("updated_at"),
        "instruments": [
            {
                "id": item.get("id"),
                "price_value": item.get("price_value"),
                "change_numeric": item.get("change_numeric"),
                "support_value": item.get("support_value"),
                "resistance_value": item.get("resistance_value"),
                "driver_keys": item.get("driver_keys", []),
                "scenario_probabilities": item.get("scenario_probabilities", {}),
            }
            for item in alert.get("instruments", [])
        ],
    }


def material_reasons(opening: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    opening_items = {item.get("id"): item for item in opening.get("instruments", [])}
    reasons: list[str] = []
    for current in candidate.get("instruments", []):
        instrument_id = current.get("id")
        before = opening_items.get(instrument_id)
        if not before:
            reasons.append(f"{instrument_id}:missing-opening")
            continue
        before_price = finite(before.get("price_value"))
        current_price = finite(current.get("price_value"))
        if before_price in (None, 0) or current_price is None:
            continue
        config = INSTRUMENTS[instrument_id]
        if config["kind"] == "basis_points":
            movement = abs(current_price - before_price) * 100.0
        else:
            movement = abs(current_price / before_price - 1.0) * 100.0
        if movement >= config["material_move"]:
            reasons.append(f"{instrument_id}:price")

        support = finite(before.get("support_value"))
        resistance = finite(before.get("resistance_value"))
        if support is not None and (before_price - support) * (current_price - support) <= 0:
            reasons.append(f"{instrument_id}:support")
        if resistance is not None and (before_price - resistance) * (current_price - resistance) <= 0:
            reasons.append(f"{instrument_id}:resistance")

        old_probabilities = before.get("scenario_probabilities", {}) or {}
        new_probabilities = current.get("scenario_probabilities", {}) or {}
        if any(
            abs(int(new_probabilities.get(key, 0)) - int(old_probabilities.get(key, 0))) >= 10
            for key in ("range", "continuation", "reversal")
        ):
            reasons.append(f"{instrument_id}:probability")

        old_drivers = set(before.get("driver_keys", []) or [])
        new_drivers = set(current.get("driver_keys", []) or [])
        if new_drivers - old_drivers:
            reasons.append(f"{instrument_id}:driver")
    return sorted(set(reasons))


def no_change_note(moment: datetime) -> dict[str, str]:
    time_text = moment.astimezone(WARSAW).strftime("%H:%M")
    return {
        "pl": f"Sprawdzono przed zamknięciem o {time_text}: brak istotnej zmiany względem alertu po otwarciu.",
        "en": f"Checked before the close at {time_text}: no material change from the post-open alert.",
    }


def material_note(candidate: dict[str, Any]) -> dict[str, str]:
    provided = candidate.pop("_editorial_preclose_note", {"pl": "", "en": ""})
    fallback_pl = "Aktualizacja przed zamknięciem: zmienił się co najmniej jeden istotny poziom, scenariusz lub główny impuls rynkowy."
    fallback_en = "Pre-close update: at least one material level, scenario or primary market driver changed."
    return ensure_localized(provided, fallback_pl, fallback_en)


def validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "2.0":
        raise ValueError("Unexpected schema version")
    instruments = payload.get("instruments")
    if not isinstance(instruments, list) or {item.get("id") for item in instruments} != set(INSTRUMENTS):
        raise ValueError("Payload must contain exactly sp500, brent and us10y")
    for item in instruments:
        probabilities = item.get("scenario_probabilities", {})
        values = [int(probabilities.get(key, 0)) for key in ("range", "continuation", "reversal")]
        if sum(values) != 100 or any(value % 5 for value in values):
            raise ValueError(f"Invalid probabilities for {item.get('id')}")
        if not item.get("reason", {}).get("pl") or not item.get("reason", {}).get("en"):
            raise ValueError(f"Missing reason for {item.get('id')}")
        if finite(item.get("support_value")) is None or finite(item.get("resistance_value")) is None:
            raise ValueError(f"Missing levels for {item.get('id')}")


def archive(payload: dict[str, Any], suffix: str) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    write_json(HISTORY_DIR / f"{payload['session_date']}-{suffix}.json", payload)


def run(mode_requested: str) -> int:
    moment = now_utc()
    mode = resolve_mode(mode_requested, moment)
    if mode == "skip":
        print("Outside the governed NYSE alert window or the market is closed; no update.")
        return 0

    previous = load_json(OUT, {})
    snapshots = [fetch_snapshot(instrument_id) for instrument_id in INSTRUMENTS]
    candidates = news_candidates(moment)
    editorial = generate_editorial(snapshots, candidates, mode, previous)
    candidate = build_alert(snapshots, candidates, editorial, mode, moment)

    if mode == "open":
        candidate["opening_snapshot"] = snapshot_from_alert(candidate)
        candidate.pop("_editorial_preclose_note", None)
        validate_payload(candidate)
        write_json(OUT, candidate)
        archive(candidate, "open")
        print(f"Published opening alert for {candidate['session_date']}")
        return 0

    same_session = previous.get("session_date") == candidate.get("session_date")
    opening = previous.get("opening_snapshot") if same_session else None
    if not isinstance(opening, dict):
        candidate["opening_snapshot"] = snapshot_from_alert(candidate)
        candidate["preclose_check"] = {
            "checked_at": moment.astimezone(WARSAW).isoformat(timespec="seconds"),
            "material_change": True,
            "reasons": ["opening-alert-unavailable"],
            "note": {
                "pl": "Opublikowano pełny alert przed zamknięciem, ponieważ nie było prawidłowego alertu po otwarciu z tej sesji.",
                "en": "A full pre-close alert was published because no valid post-open alert was available for this session.",
            },
        }
        candidate.pop("_editorial_preclose_note", None)
        validate_payload(candidate)
        write_json(OUT, candidate)
        archive(candidate, "preclose")
        print("Published pre-close alert without a valid opening baseline")
        return 0

    reasons = material_reasons(opening, candidate)
    checked_at = moment.astimezone(WARSAW).isoformat(timespec="seconds")
    if reasons:
        candidate["opening_snapshot"] = opening
        candidate["preclose_check"] = {
            "checked_at": checked_at,
            "material_change": True,
            "reasons": reasons,
            "note": material_note(candidate),
        }
        validate_payload(candidate)
        write_json(OUT, candidate)
        archive(candidate, "preclose")
        print("Published material pre-close update:", ", ".join(reasons))
    else:
        output = copy.deepcopy(previous)
        output["preclose_check"] = {
            "checked_at": checked_at,
            "material_change": False,
            "reasons": [],
            "note": no_change_note(moment),
        }
        output.pop("_editorial_preclose_note", None)
        validate_payload(output)
        write_json(OUT, output)
        archive(output, "preclose-check")
        print("Recorded pre-close check with no material change")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "open", "preclose"), default="auto")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        validate_payload(load_json(OUT, {}))
        print("Daily market alert JSON is valid")
        return 0
    return run(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
