#!/usr/bin/env python3
"""Fail-closed Daily Stock publisher for the English BriefRooms US-market module.

One audited US equity setup per regular session, intended for a 1-2 session
paper-trade horizon. Ranking is deterministic. Gemini Flash-Lite may explain
and review evidence but cannot bypass data, liquidity, risk/reward or source
integrity gates.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import statistics
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import sitecustomize  # noqa: F401
except ImportError:
    pass

try:
    from comment_quality import get_ai_runtime, request_json_completion
except ModuleNotFoundError:
    from scripts.comment_quality import get_ai_runtime, request_json_completion

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/us_daily_stock_config.json"
PUBLIC_PATH = ROOT / "data/investments/us_daily_stock.json"
METRICS_PATH = ROOT / "data/investments/us_daily_stock_metrics.json"
HISTORY_DIR = ROOT / "data/investments/us_daily_stock_history"
NEW_YORK = ZoneInfo("America/New_York")
DECISIONS = {"TRADE", "NO_TRADE", "DATA_ERROR", "PENDING"}
USER_AGENT = "BriefRooms-US-Daily-Stock/1.0"


class PublicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def round2(value: float) -> float:
    return round(float(value) + 1e-10, 2)


def now_ny() -> datetime:
    return datetime.now(NEW_YORK)


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        tmp = Path(handle.name)
    tmp.replace(path)


def load_config() -> dict[str, Any]:
    config = load_json(CONFIG_PATH)
    if not isinstance(config, dict) or not config.get("universe"):
        raise PublicationError("US Daily Stock configuration is missing or invalid.")
    if abs(sum(float(v) for v in config["weights"].values()) - 100.0) > 1e-9:
        raise PublicationError("US Daily Stock ranking weights must sum to 100.")
    return config


def easter_sunday(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    cursor = date(year, month, 1)
    while cursor.weekday() != weekday:
        cursor += timedelta(days=1)
    return cursor + timedelta(days=7 * (nth - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    while cursor.weekday() != weekday:
        cursor -= timedelta(days=1)
    return cursor


def standard_us_market_holidays(year: int) -> set[date]:
    easter = easter_sunday(year)
    return {
        observed(date(year, 1, 1)),
        nth_weekday(year, 1, 0, 3),
        nth_weekday(year, 2, 0, 3),
        easter - timedelta(days=2),
        last_weekday(year, 5, 0),
        observed(date(year, 6, 19)),
        observed(date(year, 7, 4)),
        nth_weekday(year, 9, 0, 1),
        nth_weekday(year, 11, 3, 4),
        observed(date(year, 12, 25)),
    }


def is_session_day(day: date, config: dict[str, Any]) -> bool:
    extra = set(config.get("non_session_dates") or [])
    return day.weekday() < 5 and day not in standard_us_market_holidays(day.year) and day.isoformat() not in extra


def adjacent_session(day: date, config: dict[str, Any], step: int) -> date:
    cursor = day
    while True:
        cursor += timedelta(days=step)
        if is_session_day(cursor, config):
            return cursor


def previous_session(day: date, config: dict[str, Any]) -> date:
    return adjacent_session(day, config, -1)


def add_sessions(day: date, count: int, config: dict[str, Any]) -> date:
    cursor = day
    for _ in range(count):
        cursor = adjacent_session(cursor, config, 1)
    return cursor


def parse_clock(value: str) -> clock_time:
    hour, minute = (int(x) for x in value.split(":"))
    return clock_time(hour, minute)


def request_bytes(url: str, *, timeout: int = 20, attempts: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, application/xml, text/xml, text/csv, */*",
                "Cache-Control": "no-cache",
            })
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(1.2 * (attempt + 1))
    raise PublicationError(f"Provider request failed: {type(last).__name__}")


def fetch_yahoo_bars(symbol: str, *, range_value: str = "6mo") -> list[Bar]:
    params = urllib.parse.urlencode({"range": range_value, "interval": "1d", "events": "history", "includeAdjustedClose": "true"})
    encoded = urllib.parse.quote(symbol, safe="")
    failures: list[str] = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            payload = json.loads(request_bytes(f"https://{host}/v8/finance/chart/{encoded}?{params}"))
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not result:
                raise ValueError("empty chart")
            stamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            rows: list[Bar] = []
            for i, stamp in enumerate(stamps):
                vals = {k: (quote.get(k) or [None] * len(stamps))[i] for k in ("open", "high", "low", "close", "volume")}
                if any(vals[k] is None for k in ("open", "high", "low", "close")):
                    continue
                rows.append(Bar(
                    day=datetime.fromtimestamp(int(stamp), NEW_YORK).date(),
                    open=float(vals["open"]), high=float(vals["high"]), low=float(vals["low"]), close=float(vals["close"]), volume=int(vals["volume"] or 0),
                ))
            if len(rows) < 60:
                raise ValueError(f"only {len(rows)} sessions")
            return rows
        except Exception as exc:
            failures.append(f"{host}:{type(exc).__name__}")
    raise PublicationError(f"Yahoo history unavailable for {symbol} ({'; '.join(failures)}).")


def fetch_stooq_bars(symbol: str, *, range_value: str = "6mo") -> list[Bar]:
    days = {"3mo": 120, "6mo": 240, "1y": 420}.get(range_value, 240)
    end = now_ny().date()
    start = end - timedelta(days=days)
    stooq_symbol = symbol.lower().replace("-", ".") + ".us"
    params = urllib.parse.urlencode({"s": stooq_symbol, "d1": start.strftime("%Y%m%d"), "d2": end.strftime("%Y%m%d"), "i": "d"})
    raw = request_bytes("https://stooq.com/q/d/l/?" + params, attempts=2)
    text = raw.decode("utf-8-sig", errors="replace").strip()
    if not text or "error" in text.lower() or "exceeded" in text.lower():
        raise PublicationError("Stooq returned an empty/error response.")
    rows: list[Bar] = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            rows.append(Bar(
                day=datetime.strptime(row["Date"], "%Y-%m-%d").date(),
                open=float(row["Open"]), high=float(row["High"]), low=float(row["Low"]), close=float(row["Close"]), volume=int(float(row.get("Volume") or 0)),
            ))
        except Exception:
            continue
    rows.sort(key=lambda item: item.day)
    if len(rows) < 60:
        raise PublicationError(f"Stooq returned only {len(rows)} sessions for {symbol}.")
    return rows


def fetch_resilient_bars(symbol: str, *, range_value: str = "6mo") -> tuple[list[Bar], dict[str, Any]]:
    failures: list[str] = []
    try:
        return fetch_yahoo_bars(symbol, range_value=range_value), {"provider": "Yahoo", "failures": failures}
    except Exception as exc:
        failures.append(f"Yahoo:{type(exc).__name__}")
    try:
        return fetch_stooq_bars(symbol, range_value=range_value), {"provider": "Stooq", "failures": failures}
    except Exception as exc:
        failures.append(f"Stooq:{type(exc).__name__}")
    raise PublicationError(f"No historical data for {symbol}: {' | '.join(failures)}")


def true_range(bars: list[Bar], window: int = 14) -> float:
    values: list[float] = []
    for previous, current in zip(bars[-window - 1:-1], bars[-window:]):
        values.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
    return statistics.fmean(values) if values else 0.0


def return_over(bars: list[Bar], sessions: int) -> float:
    if len(bars) <= sessions or bars[-sessions - 1].close <= 0:
        return 0.0
    return bars[-1].close / bars[-sessions - 1].close - 1.0


def build_candidate(company: dict[str, str], bars: list[Bar], expected: date, config: dict[str, Any]) -> dict[str, Any] | None:
    completed = [bar for bar in bars if bar.day <= expected]
    if not completed or completed[-1].day != expected or len(completed) < 60:
        return None
    bars = completed
    close = bars[-1].close
    turnover = statistics.median(bar.close * bar.volume for bar in bars[-20:])
    if turnover < float(config["minimum_median_turnover_usd"]):
        return None
    atr = true_range(bars)
    if close <= 0 or atr <= 0:
        return None
    atr_pct = atr / close
    ret1, ret5, ret20 = return_over(bars, 1), return_over(bars, 5), return_over(bars, 20)
    avg_vol = statistics.fmean(max(bar.volume, 0) for bar in bars[-21:-1]) or 1.0
    vol_ratio = bars[-1].volume / avg_vol
    ma20 = statistics.fmean(bar.close for bar in bars[-20:])
    ma50 = statistics.fmean(bar.close for bar in bars[-50:])
    momentum = clamp(50 + ret5 * 330 + ret20 * 125 + (8 if close > ma20 else -8) + (6 if ma20 > ma50 else -6))
    liquidity = clamp(42 + math.log10(max(turnover, 25_000_000) / 25_000_000) * 25 + math.log(max(vol_ratio, 0.25)) * 14)
    risk = max(atr * 1.05, close * 0.011)
    risk_pct = risk / close
    if risk_pct > float(config["maximum_risk_percent"]):
        return None
    rr = 1.8
    risk_score = clamp(92 - abs(atr_pct - 0.023) * 1250)
    return {
        **company,
        "last_session": expected.isoformat(),
        "reference_price": round2(close),
        "entry_zone": [round2(close * 0.995), round2(close * 1.012)],
        "stop": round2(close - risk),
        "target": round2(close + risk * rr),
        "reward_risk": rr,
        "risk_percent": round(risk_pct, 4),
        "median_turnover_usd": round(turnover),
        "volume_ratio": round(vol_ratio, 3),
        "returns": {"1d": round(ret1, 5), "5d": round(ret5, 5), "20d": round(ret20, 5)},
        "raw_momentum": momentum,
        "scores": {
            "relative_momentum": round2(momentum),
            "volume_liquidity": round2(liquidity),
            "market_context": 50.0,
            "risk_reward": round2(risk_score),
            "historical_expectancy": 50.0,
        },
    }


def percentile(value: float, low: float, high: float) -> float:
    return 50.0 if high <= low else clamp((value - low) * 100 / (high - low))


def normalize_cross_section(candidates: list[dict[str, Any]]) -> None:
    returns5 = [row["returns"]["5d"] for row in candidates]
    lo, hi = min(returns5), max(returns5)
    median5 = statistics.median(returns5)
    market1 = statistics.median(row["returns"]["1d"] for row in candidates)
    breadth = sum(row["returns"]["1d"] > 0 for row in candidates) / len(candidates)
    sectors = {row["sector"] for row in candidates}
    sector5 = {sector: statistics.median(row["returns"]["5d"] for row in candidates if row["sector"] == sector) for sector in sectors}
    for row in candidates:
        cross = percentile(row["returns"]["5d"], lo, hi)
        row["relative_5d"] = round(row["returns"]["5d"] - median5, 5)
        row["scores"]["relative_momentum"] = round2(0.55 * row["raw_momentum"] + 0.45 * cross)
        row["scores"]["market_context"] = round2(clamp(50 + (breadth - 0.5) * 36 + market1 * 180 + median5 * 70 + sector5[row["sector"]] * 85))
        s = row["scores"]
        row["quant_pre_score"] = round2((s["relative_momentum"] * 20 + s["volume_liquidity"] * 15 + s["market_context"] * 15 + s["risk_reward"] * 15 + s["historical_expectancy"] * 10) / 75)


def news_items(company: dict[str, str], *, now: datetime, limit: int = 8) -> list[dict[str, Any]]:
    query = urllib.parse.quote_plus(f'"{company["name"]}" stock when:3d')
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    root = ET.fromstring(request_bytes(url, timeout=18, attempts=2))
    items: list[dict[str, Any]] = []
    trusted_tokens = ("reuters", "associated press", "ap news", "business wire", "globenewswire", "cnbc", "bloomberg", "sec")
    for element in root.findall("./channel/item"):
        title = (element.findtext("title") or "").strip()
        link = (element.findtext("link") or "").strip()
        pub_raw = (element.findtext("pubDate") or "").strip()
        source_el = element.find("source")
        publisher = ((source_el.text if source_el is not None else "") or "").strip()
        try:
            published = parsedate_to_datetime(pub_raw).astimezone(NEW_YORK)
        except Exception:
            continue
        age = (now - published).total_seconds() / 3600
        if not title or not link or age < -1 or age > float(config_value("news_lookback_hours", 84)):
            continue
        fp = hashlib.sha1(f"{publisher}|{title}|{published.date()}".encode()).hexdigest()[:12]
        quality = "trusted" if any(token in publisher.casefold() for token in trusted_tokens) else "secondary"
        items.append({
            "id": f"src-{fp}", "title": title[:240], "url": link, "publisher": publisher or "Google News",
            "published_at": published.isoformat(timespec="minutes"), "age_hours": round(age, 1), "quality": quality,
        })
        if len(items) >= limit:
            break
    return items


def config_value(key: str, default: Any) -> Any:
    try:
        return load_config().get(key, default)
    except Exception:
        return default


def gemini_analysis(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    import requests
    runtime = get_ai_runtime()
    if runtime.provider != "gemini" or not runtime.available:
        raise PublicationError("Gemini free-tier runtime is unavailable.")
    prompt = {
        "task": "Assess the short-horizon catalyst for each US equity candidate for the next 1-2 regular sessions. Use only the supplied sources; do not invent facts.",
        "rules": [
            "Every factual claim must be supported by source_ids from that candidate.",
            "No credible fresh catalyst means catalyst_score <= 35.",
            "Price momentum alone is not a catalyst.",
            "Be concise, decision-oriented and in English.",
        ],
        "candidates": [{
            "symbol": row["symbol"], "name": row["name"], "sector": row["sector"], "quant_pre_score": row["quant_pre_score"],
            "returns": row["returns"], "volume_ratio": row["volume_ratio"], "sources": row.get("sources", []),
        } for row in candidates],
        "output_schema": {"analyses": [{"symbol": "AAPL", "catalyst_score": 0, "thesis": "max 2 sentences", "why_now": "one sentence", "risk_factors": ["risk 1", "risk 2"], "source_ids": ["src-id"]}]},
    }
    payload = request_json_completion(
        post=requests.post, runtime=runtime,
        messages=[
            {"role": "system", "content": "You are a conservative US-equity analyst. Reject unsupported narratives."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        max_tokens=1800, temperature=0.1, timeout=45,
    )
    result: dict[str, dict[str, Any]] = {}
    for row in payload.get("analyses") or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "")
        candidate = next((c for c in candidates if c["symbol"] == symbol), None)
        if not candidate:
            continue
        allowed = {s["id"] for s in candidate.get("sources", [])}
        ids = [str(v) for v in row.get("source_ids") or [] if str(v) in allowed]
        score = clamp(float(row.get("catalyst_score") or 0))
        if not ids:
            score = min(score, 20)
        result[symbol] = {
            "catalyst_score": round2(score),
            "thesis": str(row.get("thesis") or "").strip()[:600],
            "why_now": str(row.get("why_now") or "").strip()[:400],
            "risk_factors": [str(v).strip()[:240] for v in (row.get("risk_factors") or [])[:4] if str(v).strip()],
            "source_ids": ids,
        }
    return result


def source_gate(candidate: dict[str, Any], analysis: dict[str, Any]) -> bool:
    ids = set(analysis.get("source_ids") or [])
    selected = [s for s in candidate.get("sources", []) if s["id"] in ids]
    publishers = {s["publisher"].casefold() for s in selected}
    safe = all(urllib.parse.urlsplit(s["url"]).scheme in {"http", "https"} for s in selected)
    return safe and bool(selected) and (any(s["quality"] == "trusted" for s in selected) or len(publishers) >= 2)


def composite(candidate: dict[str, Any], analysis: dict[str, Any], config: dict[str, Any]) -> float:
    scores = {**candidate["scores"], "catalyst": float(analysis["catalyst_score"])}
    return round2(sum(scores[k] * float(config["weights"][k]) for k in config["weights"]) / 100)


def gemini_review(candidate: dict[str, Any], analysis: dict[str, Any], score: float) -> dict[str, Any]:
    import requests
    runtime = get_ai_runtime()
    allowed_ids = {s["id"] for s in candidate.get("sources", [])}
    prompt = {
        "task": "Audit the integrity and risk plan of this already-ranked US Daily Stock candidate. Do not re-rank it. Veto only for evidence, direction, source-integrity or risk-plan defects.",
        "candidate": {
            "symbol": candidate["symbol"], "score": score, "reference_price": candidate["reference_price"],
            "entry_zone": candidate["entry_zone"], "stop": candidate["stop"], "target": candidate["target"],
            "reward_risk": candidate["reward_risk"], "analysis": analysis, "sources": candidate.get("sources", []),
        },
        "output_schema": {
            "evidence_supported": True, "direction_not_contradicted": True, "source_integrity_ok": True, "risk_plan_ok": True,
            "supported_source_ids": ["src-id"], "fatal_issues": [], "note": "brief reason"
        }
    }
    payload = request_json_completion(
        post=requests.post, runtime=runtime,
        messages=[
            {"role": "system", "content": "You are an integrity auditor, not a second stock selector. A weak catalyst is not by itself a fatal veto."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        max_tokens=900, temperature=0, review=True, timeout=45,
    )
    supported = [str(v) for v in payload.get("supported_source_ids") or [] if str(v) in allowed_ids]
    checks = {
        "evidence_supported": bool(payload.get("evidence_supported")),
        "direction_not_contradicted": bool(payload.get("direction_not_contradicted")),
        "source_integrity_ok": bool(payload.get("source_integrity_ok")),
        "risk_plan_ok": bool(payload.get("risk_plan_ok")),
    }
    return {
        "approved": all(checks.values()) and bool(supported),
        "reason": str(payload.get("note") or "").strip()[:500],
        "supported_source_ids": supported,
        "fatal_checks": checks,
        "fatal_issues": [str(v).strip()[:260] for v in (payload.get("fatal_issues") or [])[:4] if str(v).strip()],
        "provider": runtime.provider,
        "model": runtime.review_model,
        "review_policy": "integrity_not_second_ranking",
    }


def opening_snapshot(symbol: str, *, now: datetime) -> dict[str, Any]:
    params = urllib.parse.urlencode({"range": "1d", "interval": "1m", "events": "history", "includePrePost": "false"})
    encoded = urllib.parse.quote(symbol, safe="")
    failures: list[str] = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            payload = json.loads(request_bytes(f"https://{host}/v8/finance/chart/{encoded}?{params}"))
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not result:
                raise ValueError("empty chart")
            stamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            points: list[tuple[datetime, float, float, float, float, int]] = []
            for i, stamp in enumerate(stamps):
                vals = {k: (quote.get(k) or [None] * len(stamps))[i] for k in ("open", "high", "low", "close", "volume")}
                if any(vals[k] is None for k in ("open", "high", "low", "close")):
                    continue
                dt = datetime.fromtimestamp(int(stamp), NEW_YORK)
                if dt.date() != now.date() or dt.time() < clock_time(9, 30):
                    continue
                points.append((dt, float(vals["open"]), float(vals["high"]), float(vals["low"]), float(vals["close"]), int(vals["volume"] or 0)))
            if not points:
                raise ValueError("no regular-session points")
            first, last = points[0], points[-1]
            return {
                "provider": "Yahoo", "symbol": symbol, "date": now.date().isoformat(), "observed_at": last[0].isoformat(timespec="seconds"),
                "open": round2(first[1]), "high": round2(max(p[2] for p in points)), "low": round2(min(p[3] for p in points)),
                "last": round2(last[4]), "volume": sum(max(p[5], 0) for p in points), "status": "single_source",
            }
        except Exception as exc:
            failures.append(f"{host}:{type(exc).__name__}")
    raise PublicationError(f"Current-session quote unavailable for {symbol}: {' | '.join(failures)}")


def reprice(candidate: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    snapshot = opening_snapshot(candidate["symbol"], now=now)
    old_ref = float(candidate["reference_price"])
    old_stop = float(candidate["stop"])
    risk_fraction = max((old_ref - old_stop) / max(old_ref, 0.01), 0.011)
    risk_fraction = min(risk_fraction, float(load_config()["maximum_risk_percent"]))
    reference = float(snapshot["last"])
    stop = reference * (1 - risk_fraction)
    target = reference + (reference - stop) * float(candidate["reward_risk"])
    candidate = dict(candidate)
    candidate["reference_price"] = round2(reference)
    candidate["entry_zone"] = [round2(reference * 0.997), round2(reference * 1.006)]
    candidate["stop"] = round2(stop)
    candidate["target"] = round2(target)
    candidate["market_snapshot"] = snapshot
    return candidate


def metric_summary() -> dict[str, Any]:
    rows = []
    if HISTORY_DIR.exists():
        for path in sorted(HISTORY_DIR.glob("????-??-??.json")):
            try:
                rows.append(load_json(path))
            except Exception:
                pass
    resolved = [r.get("outcome") or {} for r in rows if (r.get("outcome") or {}).get("status") == "RESOLVED" and (r.get("outcome") or {}).get("activated")]
    wins = [o for o in resolved if float(o.get("return_percent", 0)) > 0]
    rvals = [float(o.get("r_multiple", 0)) for o in resolved]
    returns = [float(o.get("return_percent", 0)) for o in resolved]
    return {
        "resolved_trades": len(resolved), "win_rate": round(len(wins) / len(resolved), 4) if resolved else None,
        "average_r": round(statistics.fmean(rvals), 3) if rvals else None,
        "average_return_percent": round(statistics.fmean(returns), 3) if returns else None,
        "automatic_weight_changes": False,
    }


def base_payload(now: datetime, config: dict[str, Any], decision: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": "us-daily-stock-v1", "policy_version": config["policy_version"], "date": now.date().isoformat(),
        "generated_at": now.isoformat(timespec="seconds"), "timezone": "America/New_York", "decision": decision, "reason": reason,
        "locked": decision in {"TRADE", "NO_TRADE"}, "selection": None, "data_quality": {},
        "methodology": {
            "horizon": "1-2 US regular sessions", "weights": config["weights"], "target_score": config["target_score"],
            "minimum_reward_risk": config["minimum_reward_risk"], "universe_size": len(config["universe"]),
            "score_policy": "ranking_target_not_hard_gate", "hard_admission_gates": ["market_data", "liquidity_and_risk", "reward_risk", "source_evidence", "independent_review"],
            "ai_role": "Gemini Flash-Lite analyses evidence and independently audits integrity; deterministic code controls admission.",
        },
        "metrics": metric_summary(), "outcome": {"status": "PENDING", "activated": None},
        "disclaimer": "Research paper-trading material, not investment advice or a promise of performance.",
    }


def generate(now: datetime | None = None) -> dict[str, Any]:
    now = now or now_ny()
    config = load_config()
    if not is_session_day(now.date(), config):
        return base_payload(now, config, "NO_TRADE", "US cash market is closed today.")
    start = parse_clock(config["analysis_not_before"])
    cutoff = parse_clock(config["publication_cutoff"])
    if now.time() < start:
        payload = base_payload(now, config, "PENDING", f"Waiting for the US regular session and the {config['analysis_not_before']} ET analysis window.")
        payload["locked"] = False
        return payload
    if now.time() >= cutoff:
        current = load_json(PUBLIC_PATH)
        if isinstance(current, dict) and current.get("date") == now.date().isoformat() and current.get("decision") in {"TRADE", "NO_TRADE"}:
            return current
        return base_payload(now, config, "DATA_ERROR", f"No validated signal was completed before {config['publication_cutoff']} ET.")

    expected = previous_session(now.date(), config)
    valid_market = 0
    failures: dict[str, str] = {}
    providers: dict[str, str] = {}
    candidates: list[dict[str, Any]] = []
    for company in config["universe"]:
        symbol = company["symbol"]
        try:
            bars, meta = fetch_resilient_bars(symbol)
            completed = [b for b in bars if b.day <= expected]
            if not completed or completed[-1].day != expected:
                raise PublicationError(f"stale latest session {completed[-1].day if completed else 'none'}")
            valid_market += 1
            providers[symbol] = meta["provider"]
            candidate = build_candidate(company, bars, expected, config)
            if candidate:
                candidates.append(candidate)
        except Exception as exc:
            failures[symbol] = f"{type(exc).__name__}: {str(exc)[:240]}"
    ratio = valid_market / max(len(config["universe"]), 1)
    if ratio < float(config["minimum_data_completeness"]):
        payload = base_payload(now, config, "DATA_ERROR", f"Fresh US market-data completeness {ratio:.0%} is below the required threshold.")
        payload["data_quality"] = {"status": "failed", "complete_ratio": round(ratio, 4), "expected_session": expected.isoformat(), "provider_failures": failures}
        return payload
    if not candidates:
        payload = base_payload(now, config, "NO_TRADE", "Market data is healthy, but no stock passed liquidity and risk screening.")
        payload["data_quality"] = {"status": "healthy", "complete_ratio": round(ratio, 4), "expected_session": expected.isoformat(), "ranked_candidates": 0}
        return payload

    normalize_cross_section(candidates)
    shortlist = sorted(candidates, key=lambda item: item["quant_pre_score"], reverse=True)[: int(config["top_candidates_for_news"])]
    for row in shortlist:
        try:
            row["sources"] = news_items(row, now=now)
        except Exception:
            row["sources"] = []
    if not any(row.get("sources") for row in shortlist):
        payload = base_payload(now, config, "NO_TRADE", "No fresh verifiable catalyst was available for the quantitative shortlist.")
        payload["data_quality"] = {"status": "healthy", "complete_ratio": round(ratio, 4), "expected_session": expected.isoformat(), "ranked_candidates": len(candidates), "reviewed_candidates": len(shortlist)}
        return payload

    analyses = gemini_analysis(shortlist)
    eligible: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    rejects: dict[str, str] = {}
    for row in shortlist:
        analysis = analyses.get(row["symbol"])
        if not analysis:
            rejects[row["symbol"]] = "missing_analysis"
            continue
        if not source_gate(row, analysis):
            rejects[row["symbol"]] = "source_gate"
            continue
        score = composite(row, analysis, config)
        if row["reward_risk"] < float(config["minimum_reward_risk"]):
            rejects[row["symbol"]] = "reward_risk"
            continue
        eligible.append((score, row, analysis))
    eligible.sort(key=lambda item: item[0], reverse=True)
    if not eligible:
        payload = base_payload(now, config, "NO_TRADE", "No candidate passed evidence and risk gates.")
        payload["data_quality"] = {"status": "healthy", "complete_ratio": round(ratio, 4), "expected_session": expected.isoformat(), "analysis_rejections": rejects}
        return payload

    review_rejections = []
    approved = None
    for score, candidate, analysis in eligible:
        review = gemini_review(candidate, analysis, score)
        if review.get("approved"):
            approved = (score, candidate, analysis, review)
            break
        review_rejections.append({"symbol": candidate["symbol"], "score": score, "reason": review.get("reason")})
    if approved is None:
        payload = base_payload(now, config, "NO_TRADE", "All eligible candidates failed the independent integrity review.")
        payload["data_quality"] = {"status": "healthy", "complete_ratio": round(ratio, 4), "expected_session": expected.isoformat(), "review_rejections": review_rejections}
        return payload

    score, candidate, analysis, review = approved
    candidate = reprice(candidate, now=now)
    by_id = {s["id"]: s for s in candidate.get("sources", [])}
    approved_sources = [by_id[sid] for sid in review["supported_source_ids"] if sid in by_id]
    target = float(config["target_score"])
    conviction = "high" if score >= target + 8 else "solid" if score >= target else "moderate"
    payload = base_payload(now, config, "TRADE", (
        "The best available candidate passed hard data, liquidity/risk, reward/risk, source and independent-review gates. "
        + (f"Score {score:.2f} met the {target:.0f} target." if score >= target else f"Score {score:.2f} is below the {target:.0f} target, so conviction is moderate.")
    ))
    payload["locked"] = True
    payload["selection"] = {
        "symbol": candidate["symbol"], "ticker": candidate["symbol"], "name": candidate["name"], "sector": candidate["sector"],
        "score": score, "score_target": target, "score_target_met": score >= target, "conviction": conviction,
        "reference_price": candidate["reference_price"], "entry_zone": candidate["entry_zone"], "stop": candidate["stop"], "target": candidate["target"],
        "reward_risk": candidate["reward_risk"], "valid_until": add_sessions(now.date(), 2, config).isoformat(),
        "activation": "Post-open setup: enter only inside the stated zone; do not chase above the upper bound.",
        "thesis": analysis["thesis"], "why_now": analysis["why_now"], "risk_factors": analysis["risk_factors"],
        "scores": {**candidate["scores"], "catalyst": analysis["catalyst_score"]}, "sources": approved_sources,
        "review": review, "market_snapshot": candidate["market_snapshot"],
    }
    payload["data_quality"] = {
        "status": "healthy", "complete_ratio": round(ratio, 4), "expected_session": expected.isoformat(), "valid_market_symbols": valid_market,
        "ranked_candidates": len(candidates), "reviewed_candidates": len(shortlist), "eligible_candidates": len(eligible),
        "provider_failures": failures, "provider_usage": providers, "analysis_rejections": rejects, "review_rejections": review_rejections,
        "opening_quote": candidate["market_snapshot"]["status"],
    }
    return payload


def validate_payload(payload: dict[str, Any], *, require_today: bool = False, now: datetime | None = None) -> None:
    if not isinstance(payload, dict) or payload.get("decision") not in DECISIONS:
        raise PublicationError("Invalid US Daily Stock payload.")
    now = now or now_ny()
    if require_today and payload.get("date") != now.date().isoformat():
        raise PublicationError("US Daily Stock payload is not current-day data.")
    if payload.get("decision") == "TRADE":
        selection = payload.get("selection") or {}
        for key in ("symbol", "score", "reference_price", "entry_zone", "stop", "target", "reward_risk", "thesis", "why_now", "sources", "review", "market_snapshot"):
            if selection.get(key) is None:
                raise PublicationError(f"Missing trade field: {key}")
        if float(selection["reward_risk"]) < float(load_config()["minimum_reward_risk"]):
            raise PublicationError("Reward/risk is below the hard minimum.")
        if not selection["sources"] or selection["review"].get("approved") is not True:
            raise PublicationError("Trade lacks approved evidence/review.")
        if selection["market_snapshot"].get("date") != now.date().isoformat():
            raise PublicationError("Trade does not use a current-session execution snapshot.")


def publish(payload: dict[str, Any]) -> None:
    validate_payload(payload, now=now_ny())
    atomic_json(PUBLIC_PATH, payload)
    atomic_json(METRICS_PATH, payload.get("metrics") or {})
    if payload.get("decision") in {"TRADE", "NO_TRADE"}:
        atomic_json(HISTORY_DIR / f"{payload['date']}.json", payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "validate"), default="auto")
    args = parser.parse_args()
    now = now_ny()
    if args.mode == "validate":
        validate_payload(load_json(PUBLIC_PATH), require_today=True, now=now)
        print(f"OK: {PUBLIC_PATH.relative_to(ROOT)}")
        return 0
    try:
        payload = generate(now)
    except Exception as exc:
        config = load_config()
        payload = base_payload(now, config, "DATA_ERROR", f"Automated US Daily Stock generation stopped: {type(exc).__name__}.")
        payload["data_quality"] = {"status": "failed", "failed_stage": "publisher", "error": str(exc)[:500]}
        print(f"Fail-closed: {exc}")
    publish(payload)
    validate_payload(load_json(PUBLIC_PATH), require_today=True, now=now)
    print(f"Published {payload['decision']} for {payload['date']} ({now:%H:%M} ET).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
