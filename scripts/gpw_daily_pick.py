#!/usr/bin/env python3
"""Fail-closed publisher for the Polish BriefRooms GPW daily paper trade.

The ranking is deterministic. Gemini may explain and challenge a catalyst, but
it cannot bypass freshness, liquidity, risk/reward or source-evidence gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

try:  # Ensure the repository's Gemini-first adapter is active outside Actions.
    import sitecustomize  # noqa: F401
except ImportError:  # pragma: no cover - PYTHONPATH in Actions makes it available.
    pass

try:
    from comment_quality import get_ai_runtime, request_json_completion
except ModuleNotFoundError:
    from scripts.comment_quality import get_ai_runtime, request_json_completion


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/gpw_daily_pick_config.json"
PUBLIC_PATH = ROOT / "data/investments/gpw_daily_pick.json"
METRICS_PATH = ROOT / "data/investments/gpw_daily_pick_metrics.json"
HISTORY_DIR = ROOT / "data/investments/gpw_daily_pick_history"
AUDIT_DIR = ROOT / "data/internal/gpw_daily_pick_audit"
WARSAW = ZoneInfo("Europe/Warsaw")
DECISIONS = {"TRANSAKCJA", "BRAK_TRANSAKCJI", "AWARIA_DANYCH"}
USER_AGENT = "BriefRooms-GPW-Daily-Pick/1.0"


class PublicationError(RuntimeError):
    """An input or provider failed a mandatory publication gate."""


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


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(body)
        temporary = Path(handle.name)
    temporary.replace(path)


def now_warsaw() -> datetime:
    return datetime.now(WARSAW)


def load_config() -> dict[str, Any]:
    config = load_json(CONFIG_PATH)
    if not isinstance(config, dict) or not config.get("universe"):
        raise PublicationError("Brak poprawnej konfiguracji uniwersum GPW.")
    weights = config.get("weights") or {}
    if sum(float(value) for value in weights.values()) != 100:
        raise PublicationError("Wagi rankingu GPW nie sumują się do 100.")
    return config


def easter_sunday(year: int) -> date:
    """Gregorian Easter, used for GPW movable non-session dates."""
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


def standard_gpw_holidays(year: int) -> set[date]:
    easter = easter_sunday(year)
    return {
        date(year, 1, 1),
        date(year, 1, 6),
        easter - timedelta(days=2),
        easter + timedelta(days=1),
        date(year, 5, 1),
        date(year, 5, 3),
        easter + timedelta(days=60),
        date(year, 8, 15),
        date(year, 11, 1),
        date(year, 11, 11),
        date(year, 12, 24),
        date(year, 12, 25),
        date(year, 12, 26),
        date(year, 12, 31),
    }


def is_session_day(day: date, config: dict[str, Any]) -> bool:
    configured = set(config["non_session_dates"])
    return day.weekday() < 5 and day not in standard_gpw_holidays(day.year) and day.isoformat() not in configured


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


def cutoff_for(day: date, config: dict[str, Any]) -> datetime:
    hour, minute = (int(value) for value in config["publication_cutoff"].split(":"))
    return datetime.combine(day, clock_time(hour, minute), tzinfo=WARSAW)


def request_bytes(url: str, *, timeout: int = 20, attempts: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json, application/xml, text/xml, */*",
                    "Cache-Control": "no-cache",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # network error classes differ between runners
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise PublicationError(f"Źródło danych nie odpowiedziało: {type(last_error).__name__}")


def fetch_yahoo_bars(symbol: str, *, range_value: str = "6mo") -> list[Bar]:
    encoded = urllib.parse.quote(symbol, safe="")
    params = urllib.parse.urlencode(
        {"range": range_value, "interval": "1d", "events": "history", "includeAdjustedClose": "true"}
    )
    errors: list[str] = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            payload = json.loads(request_bytes(f"https://{host}/v8/finance/chart/{encoded}?{params}"))
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not result:
                raise ValueError(payload.get("chart", {}).get("error") or "empty chart")
            timestamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            bars: list[Bar] = []
            for index, stamp in enumerate(timestamps):
                values = {key: (quote.get(key) or [None] * len(timestamps))[index] for key in ("open", "high", "low", "close", "volume")}
                if any(values[key] is None for key in ("open", "high", "low", "close")):
                    continue
                bars.append(
                    Bar(
                        datetime.fromtimestamp(int(stamp), WARSAW).date(),
                        float(values["open"]),
                        float(values["high"]),
                        float(values["low"]),
                        float(values["close"]),
                        int(values["volume"] or 0),
                    )
                )
            if len(bars) < 60:
                raise ValueError(f"only {len(bars)} valid sessions")
            return bars
        except Exception as exc:
            errors.append(f"{host}: {type(exc).__name__}")
    raise PublicationError(f"Brak pełnej historii {symbol} ({'; '.join(errors)}).")


def percentile_score(value: float, lower: float, upper: float) -> float:
    if upper <= lower:
        return 50.0
    return clamp((value - lower) * 100.0 / (upper - lower))


def true_range(bars: list[Bar], window: int = 14) -> float:
    values: list[float] = []
    for previous, current in zip(bars[-window - 1 : -1], bars[-window:]):
        values.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return statistics.fmean(values) if values else 0.0


def return_over(bars: list[Bar], sessions: int) -> float:
    if len(bars) <= sessions or not bars[-sessions - 1].close:
        return 0.0
    return bars[-1].close / bars[-sessions - 1].close - 1.0


def history_expectancy_score(history: list[dict[str, Any]], sector: str) -> tuple[float, int]:
    resolved = [
        item
        for item in history
        if item.get("selection", {}).get("sector") == sector
        and item.get("outcome", {}).get("status") == "RESOLVED"
    ]
    if len(resolved) < 8:
        return 50.0, len(resolved)
    expectancy = statistics.fmean(float(item["outcome"].get("r_multiple", 0)) for item in resolved)
    return clamp(50.0 + expectancy * 22.0), len(resolved)


def build_quant_candidate(
    company: dict[str, str],
    bars: list[Bar],
    expected_day: date,
    config: dict[str, Any],
    history: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if bars[-1].day < expected_day:
        return None
    close = bars[-1].close
    turnover = statistics.median(bar.close * bar.volume for bar in bars[-20:])
    if turnover < float(config["minimum_median_turnover_pln"]):
        return None
    atr = true_range(bars)
    if close <= 0 or atr <= 0:
        return None
    atr_percent = atr / close
    ret_1 = return_over(bars, 1)
    ret_5 = return_over(bars, 5)
    ret_20 = return_over(bars, 20)
    volume_average = statistics.fmean(max(bar.volume, 0) for bar in bars[-21:-1]) or 1.0
    volume_ratio = bars[-1].volume / volume_average
    ma20 = statistics.fmean(bar.close for bar in bars[-20:])
    ma50 = statistics.fmean(bar.close for bar in bars[-50:])

    momentum = clamp(50 + ret_5 * 330 + ret_20 * 125 + (8 if close > ma20 else -8) + (6 if ma20 > ma50 else -6))
    liquidity = clamp(38 + math.log10(max(turnover, 1_000_000) / 1_000_000) * 32 + math.log(max(volume_ratio, 0.25)) * 16)
    context = clamp(50 + ret_1 * 180 + ret_5 * 90)

    risk = max(atr * 1.1, close * 0.012)
    risk_percent = risk / close
    reward_risk = 1.8
    risk_score = clamp(92 - abs(atr_percent - 0.025) * 1250)
    if risk_percent > float(config["maximum_risk_percent"]):
        return None
    historical, historical_n = history_expectancy_score(history, company["sector"])
    return {
        **company,
        "last_session": bars[-1].day.isoformat(),
        "reference_price": round2(close),
        "entry_zone": [round2(close * 0.995), round2(close * 1.015)],
        "stop": round2(close - risk),
        "target": round2(close + risk * reward_risk),
        "risk_percent": round(risk_percent, 4),
        "reward_risk": reward_risk,
        "returns": {"1d": round(ret_1, 5), "5d": round(ret_5, 5), "20d": round(ret_20, 5)},
        "median_turnover_pln": round(turnover),
        "volume_ratio": round(volume_ratio, 3),
        "raw_momentum": momentum,
        "scores": {
            "relative_momentum": round2(momentum),
            "volume_liquidity": round2(liquidity),
            "market_context": round2(context),
            "risk_reward": round2(risk_score),
            "historical_expectancy": round2(historical),
        },
        "historical_sample": historical_n,
    }


def normalize_cross_section(candidates: list[dict[str, Any]]) -> None:
    returns = [candidate["returns"]["5d"] for candidate in candidates]
    lower, upper = min(returns), max(returns)
    median_return = statistics.median(returns)
    for candidate in candidates:
        relative = candidate["returns"]["5d"] - median_return
        cross = percentile_score(candidate["returns"]["5d"], lower, upper)
        candidate["relative_5d"] = round(relative, 5)
        candidate["scores"]["relative_momentum"] = round2(
            0.55 * candidate["raw_momentum"] + 0.45 * cross
        )
        scores = candidate["scores"]
        candidate["quant_pre_score"] = round2(
            (
                scores["relative_momentum"] * 20
                + scores["volume_liquidity"] * 15
                + scores["market_context"] * 15
                + scores["risk_reward"] * 15
                + scores["historical_expectancy"] * 10
            )
            / 75
        )


def news_items(company: dict[str, str], *, now: datetime, limit: int = 8) -> list[dict[str, Any]]:
    query = urllib.parse.quote_plus(f'"{company["name"]}" GPW when:3d')
    url = f"https://news.google.com/rss/search?q={query}&hl=pl&gl=PL&ceid=PL:pl"
    root = ET.fromstring(request_bytes(url, timeout=18, attempts=2))
    items: list[dict[str, Any]] = []
    for element in root.findall("./channel/item"):
        title = (element.findtext("title") or "").strip()
        link = (element.findtext("link") or "").strip()
        published_raw = (element.findtext("pubDate") or "").strip()
        source_element = element.find("source")
        source = ((source_element.text if source_element is not None else "") or "").strip()
        try:
            published = parsedate_to_datetime(published_raw).astimezone(WARSAW)
        except Exception:
            continue
        age_hours = (now - published).total_seconds() / 3600
        if not title or not link or age_hours < -1 or age_hours > 84:
            continue
        fingerprint = hashlib.sha1(f"{source}|{title}|{published.date()}".encode()).hexdigest()[:12]
        source_lower = source.lower()
        quality = "pierwotne" if any(token in source_lower for token in ("pap", "reuters", "gpw", "orlen", "pzu", "bank pekao", "pkobp")) else "wtórne"
        items.append(
            {
                "id": f"src-{fingerprint}",
                "title": title[:240],
                "url": link,
                "publisher": source or "Google News",
                "published_at": published.isoformat(timespec="minutes"),
                "age_hours": round(age_hours, 1),
                "quality": quality,
            }
        )
        if len(items) >= limit:
            break
    return items


def all_history() -> list[dict[str, Any]]:
    if not HISTORY_DIR.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(HISTORY_DIR.glob("????-??-??.json")):
        try:
            value = load_json(path)
            if isinstance(value, dict):
                rows.append(value)
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def gemini_analysis(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    import requests

    runtime = get_ai_runtime()
    if runtime.provider != "gemini" or not runtime.available:
        raise PublicationError("GEMINI_API_KEY nie jest dostępny; publikacja została zatrzymana.")
    input_rows = []
    for candidate in candidates:
        input_rows.append(
            {
                "symbol": candidate["symbol"],
                "name": candidate["name"],
                "sector": candidate["sector"],
                "quant_pre_score": candidate["quant_pre_score"],
                "returns": candidate["returns"],
                "volume_ratio": candidate["volume_ratio"],
                "sources": candidate.get("sources", []),
            }
        )
    prompt = {
        "task": "Oceń krótkoterminowy katalizator dla kandydatów GPW na 1-2 sesje. Korzystaj wyłącznie z przekazanych źródeł. Nie dopowiadaj faktów.",
        "rules": [
            "Każde twierdzenie faktyczne musi mieć source_id z listy danego emitenta.",
            "Brak wiarygodnego, świeżego katalizatora oznacza catalyst_score <= 35.",
            "Nie traktuj samego wzrostu ceny jako katalizatora.",
            "Odpowiedź wyłącznie po polsku i jako JSON.",
        ],
        "candidates": input_rows,
        "output_schema": {
            "analyses": [
                {
                    "symbol": "SYMBOL",
                    "catalyst_score": 0,
                    "thesis": "maksymalnie 2 zdania",
                    "why_now": "jedno zdanie",
                    "risk_factors": ["ryzyko 1", "ryzyko 2"],
                    "source_ids": ["src-id"]
                }
            ]
        },
    }
    payload = request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=[
            {"role": "system", "content": "Jesteś konserwatywnym analitykiem GPW. Odrzucasz narracje bez dowodów."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        max_tokens=1800,
        temperature=0.1,
        timeout=45,
    )
    result: dict[str, dict[str, Any]] = {}
    for row in payload.get("analyses") or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "")
        candidate = next((item for item in candidates if item["symbol"] == symbol), None)
        if not candidate:
            continue
        allowed = {source["id"] for source in candidate.get("sources", [])}
        source_ids = [str(value) for value in row.get("source_ids") or [] if str(value) in allowed]
        score = clamp(float(row.get("catalyst_score") or 0))
        if not source_ids:
            score = min(score, 20)
        result[symbol] = {
            "catalyst_score": round2(score),
            "thesis": str(row.get("thesis") or "").strip()[:600],
            "why_now": str(row.get("why_now") or "").strip()[:400],
            "risk_factors": [str(value).strip()[:240] for value in (row.get("risk_factors") or [])[:4] if str(value).strip()],
            "source_ids": source_ids,
        }
    return result


def source_gate(candidate: dict[str, Any], analysis: dict[str, Any]) -> bool:
    selected_ids = set(analysis.get("source_ids") or [])
    selected = [source for source in candidate.get("sources", []) if source["id"] in selected_ids]
    publishers = {source["publisher"].casefold() for source in selected}
    safe_urls = all(urllib.parse.urlsplit(source["url"]).scheme in {"http", "https"} for source in selected)
    return safe_urls and bool(selected) and (any(source["quality"] == "pierwotne" for source in selected) or len(publishers) >= 2)


def composite(candidate: dict[str, Any], analysis: dict[str, Any], config: dict[str, Any]) -> float:
    scores = {**candidate["scores"], "catalyst": float(analysis["catalyst_score"])}
    return round2(sum(scores[key] * float(config["weights"][key]) for key in config["weights"]) / 100)


def gemini_review(candidate: dict[str, Any], analysis: dict[str, Any], score: float) -> dict[str, Any]:
    import requests

    runtime = get_ai_runtime()
    prompt = {
        "task": "Niezależnie skrytykuj plan paper trade GPW na 1-2 sesje. Zatwierdź tylko, gdy teza wynika ze źródeł, a ryzyka i warunki są spójne.",
        "candidate": {
            "symbol": candidate["symbol"],
            "composite_score": score,
            "entry_zone": candidate["entry_zone"],
            "stop": candidate["stop"],
            "target": candidate["target"],
            "reward_risk": candidate["reward_risk"],
            "analysis": analysis,
            "sources": candidate.get("sources", []),
        },
        "output_schema": {"approved": False, "reason": "krótko", "supported_source_ids": [], "contradictions": []},
    }
    payload = request_json_completion(
        post=requests.post,
        runtime=runtime,
        messages=[
            {"role": "system", "content": "Jesteś drugim, sceptycznym recenzentem. Jedna istotna luka oznacza approved=false."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        max_tokens=700,
        temperature=0,
        review=True,
        timeout=45,
    )
    allowed = set(analysis.get("source_ids") or [])
    supported = [str(value) for value in payload.get("supported_source_ids") or [] if str(value) in allowed]
    return {
        "approved": bool(payload.get("approved")) and bool(supported),
        "reason": str(payload.get("reason") or "").strip()[:400],
        "supported_source_ids": supported,
        "contradictions": [str(value).strip()[:240] for value in (payload.get("contradictions") or [])[:4] if str(value).strip()],
        "provider": runtime.provider,
        "model": runtime.review_model,
    }


def metric_summary(history: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = [row.get("outcome") or {} for row in history]
    resolved = [row for row in outcomes if row.get("status") == "RESOLVED"]
    activated = [row for row in resolved if row.get("activated")]
    wins = [row for row in activated if float(row.get("return_percent", 0)) > 0]
    r_values = [float(row.get("r_multiple", 0)) for row in activated]
    returns = [float(row.get("return_percent", 0)) for row in activated]
    return {
        "resolved_trades": len(activated),
        "not_activated": sum(1 for row in resolved if not row.get("activated")),
        "win_rate": round(len(wins) / len(activated), 4) if activated else None,
        "average_r": round(statistics.fmean(r_values), 3) if r_values else None,
        "average_return_percent": round(statistics.fmean(returns), 3) if returns else None,
        "automatic_weight_changes": False,
    }


def common_payload(now: datetime, config: dict[str, Any], decision: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": "gpw-daily-pick-v1",
        "policy_version": config["policy_version"],
        "date": now.date().isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": "Europe/Warsaw",
        "publication_cutoff": config["publication_cutoff"],
        "decision": decision,
        "reason": reason,
        "locked": now >= cutoff_for(now.date(), config),
        "selection": None,
        "data_quality": {},
        "methodology": {
            "horizon": "1-2 sesje GPW",
            "weights": config["weights"],
            "minimum_score": config["minimum_composite_score"],
            "minimum_reward_risk": config["minimum_reward_risk"],
            "universe_size": len(config["universe"]),
            "gemini_role": "analiza katalizatora i niezależna recenzja; decyzję końcową blokują reguły kodu",
        },
        "metrics": metric_summary(all_history()),
        "disclaimer": "Materiał badawczy i paper trading. To nie jest rekomendacja inwestycyjna ani obietnica wyniku.",
    }


def publish(payload: dict[str, Any]) -> bool:
    validate_payload(payload, require_today=False)
    history_path = HISTORY_DIR / f"{payload['date']}.json"
    existing = load_json(history_path)
    if isinstance(existing, dict) and existing.get("decision") != "AWARIA_DANYCH":
        atomic_json(PUBLIC_PATH, existing)
        return False
    atomic_json(history_path, payload)
    atomic_json(PUBLIC_PATH, payload)
    atomic_json(METRICS_PATH, payload["metrics"])
    audit = {
        "date": payload["date"],
        "generated_at": payload["generated_at"],
        "decision": payload["decision"],
        "policy_version": payload["policy_version"],
        "payload_sha256": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
    }
    atomic_json(AUDIT_DIR / f"{payload['date']}.json", audit)
    return True


def failure_payload(now: datetime, config: dict[str, Any], reason: str, stage: str) -> dict[str, Any]:
    payload = common_payload(now, config, "AWARIA_DANYCH", reason)
    payload["data_quality"] = {"status": "failed", "failed_stage": stage}
    return payload


def generate(
    *,
    now: datetime | None = None,
    market_fetcher: Callable[[str], list[Bar]] = fetch_yahoo_bars,
    news_fetcher: Callable[..., list[dict[str, Any]]] = news_items,
) -> dict[str, Any]:
    now = now or now_warsaw()
    config = load_config()
    if not is_session_day(now.date(), config):
        payload = common_payload(now, config, "BRAK_TRANSAKCJI", "Dziś nie ma sesji GPW.")
        payload["locked"] = True
        payload["data_quality"] = {"status": "not_applicable", "complete_ratio": 1.0}
        return payload
    if now >= cutoff_for(now.date(), config):
        return failure_payload(now, config, "Nie utworzono nowego sygnału po godzinie 08:30.", "cutoff")

    expected = previous_session(now.date(), config)
    history = all_history()
    candidates: list[dict[str, Any]] = []
    failures: list[str] = []
    for company in config["universe"]:
        try:
            candidate = build_quant_candidate(company, market_fetcher(company["symbol"]), expected, config, history)
            if candidate:
                candidates.append(candidate)
            else:
                failures.append(company["symbol"])
        except Exception:
            failures.append(company["symbol"])
    complete_ratio = len(candidates) / len(config["universe"])
    if complete_ratio < float(config["minimum_data_completeness"]):
        payload = failure_payload(now, config, f"Kompletność danych {complete_ratio:.0%} jest poniżej wymaganego progu.", "market_data")
        payload["data_quality"].update({"complete_ratio": round(complete_ratio, 4), "expected_session": expected.isoformat(), "failed_symbols": failures})
        return payload

    normalize_cross_section(candidates)
    shortlist = sorted(candidates, key=lambda item: item["quant_pre_score"], reverse=True)[: int(config["top_candidates_for_news"])]
    for candidate in shortlist:
        try:
            candidate["sources"] = news_fetcher(candidate, now=now)
        except Exception:
            candidate["sources"] = []
    if not any(candidate["sources"] for candidate in shortlist):
        payload = common_payload(now, config, "BRAK_TRANSAKCJI", "Brak świeżego, możliwego do zweryfikowania katalizatora dla kandydatów.")
        payload["data_quality"] = {"status": "healthy", "complete_ratio": round(complete_ratio, 4), "expected_session": expected.isoformat(), "ranked_candidates": len(candidates)}
        return payload

    analyses = gemini_analysis(shortlist)
    eligible: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for candidate in shortlist:
        analysis = analyses.get(candidate["symbol"])
        if not analysis or not source_gate(candidate, analysis):
            continue
        score = composite(candidate, analysis, config)
        if score >= float(config["minimum_composite_score"]) and candidate["reward_risk"] >= float(config["minimum_reward_risk"]):
            eligible.append((score, candidate, analysis))
    eligible.sort(key=lambda item: item[0], reverse=True)

    payload = common_payload(now, config, "BRAK_TRANSAKCJI", "Żaden kandydat nie przeszedł pełnego progu jakości i ryzyka.")
    payload["data_quality"] = {
        "status": "healthy",
        "complete_ratio": round(complete_ratio, 4),
        "expected_session": expected.isoformat(),
        "ranked_candidates": len(candidates),
        "reviewed_candidates": len(shortlist),
        "failed_symbols": failures,
    }
    if not eligible:
        return payload

    score, candidate, analysis = eligible[0]
    review = gemini_review(candidate, analysis, score)
    if not review["approved"]:
        payload["reason"] = f"Najlepszy kandydat został odrzucony przez drugi przegląd: {review['reason'] or 'brak pełnego potwierdzenia.'}"
        payload["review"] = review
        return payload
    if now_warsaw() >= cutoff_for(now.date(), config):
        return failure_payload(now_warsaw(), config, "Analiza zakończyła się po godzinie 08:30; sygnał nie został opublikowany.", "cutoff_after_review")

    valid_until = add_sessions(now.date(), 2, config)
    sources_by_id = {source["id"]: source for source in candidate["sources"]}
    approved_sources = [sources_by_id[source_id] for source_id in review["supported_source_ids"] if source_id in sources_by_id]
    payload.update(
        {
            "decision": "TRANSAKCJA",
            "reason": "Kandydat przeszedł ranking, bramki źródłowe, kontrolę ryzyka i niezależną recenzję Gemini.",
            "locked": True,
            "selection": {
                "symbol": candidate["symbol"],
                "ticker": candidate["symbol"].removesuffix(".WA"),
                "name": candidate["name"],
                "sector": candidate["sector"],
                "score": score,
                "reference_price": candidate["reference_price"],
                "entry_zone": candidate["entry_zone"],
                "activation": "Paper trade aktywuje się na oficjalnym otwarciu tylko przy cenie w strefie wejścia; brak aktywacji oznacza brak transakcji.",
                "stop": candidate["stop"],
                "target": candidate["target"],
                "reward_risk": candidate["reward_risk"],
                "valid_until": valid_until.isoformat(),
                "thesis": analysis["thesis"],
                "why_now": analysis["why_now"],
                "risk_factors": analysis["risk_factors"],
                "scores": {**candidate["scores"], "catalyst": analysis["catalyst_score"]},
                "sources": approved_sources,
                "review": review,
            },
            "outcome": {"status": "PENDING", "activated": None},
        }
    )
    return payload


def validate_payload(payload: dict[str, Any], *, require_today: bool = True, now: datetime | None = None) -> None:
    now = now or now_warsaw()
    required = {"schema_version", "policy_version", "date", "generated_at", "timezone", "decision", "reason", "locked", "methodology", "metrics", "disclaimer"}
    missing = sorted(required - payload.keys())
    if missing:
        raise PublicationError(f"Brak pól publikacji: {', '.join(missing)}")
    if payload["decision"] not in DECISIONS:
        raise PublicationError("Nieznany stan decyzji.")
    if require_today and payload["date"] != now.date().isoformat():
        raise PublicationError("Publiczny wybór GPW nie pochodzi z dzisiejszej daty warszawskiej.")
    generated = datetime.fromisoformat(payload["generated_at"])
    if generated.tzinfo is None:
        raise PublicationError("generated_at musi zawierać strefę czasową.")
    if payload["decision"] == "TRANSAKCJA":
        selection = payload.get("selection")
        if not isinstance(selection, dict):
            raise PublicationError("Transakcja nie ma kompletnej sekcji selection.")
        for key in ("symbol", "score", "entry_zone", "stop", "target", "valid_until", "thesis", "risk_factors", "sources", "review"):
            if key not in selection:
                raise PublicationError(f"Brak pola selection.{key}")
        if float(selection["score"]) < float(payload["methodology"]["minimum_score"]):
            raise PublicationError("Wynik transakcji jest poniżej progu publikacji.")
        if float(selection["reward_risk"]) < float(payload["methodology"]["minimum_reward_risk"]):
            raise PublicationError("Relacja zysku do ryzyka jest zbyt niska.")
        if not selection["sources"] or not selection["review"].get("approved"):
            raise PublicationError("Transakcja nie ma zatwierdzonych źródeł i recenzji.")
        if any(urllib.parse.urlsplit(source.get("url", "")).scheme not in {"http", "https"} for source in selection["sources"]):
            raise PublicationError("Transakcja zawiera niedozwolony adres źródła.")


def settle_history() -> int:
    config = load_config()
    today = now_warsaw().date()
    changed = 0
    for path in sorted(HISTORY_DIR.glob("????-??-??.json")):
        payload = load_json(path)
        if not isinstance(payload, dict) or payload.get("decision") != "TRANSAKCJA":
            continue
        if payload.get("outcome", {}).get("status") != "PENDING":
            continue
        selection = payload["selection"]
        expiry = date.fromisoformat(selection["valid_until"])
        if today <= expiry:
            continue
        bars = fetch_yahoo_bars(selection["symbol"], range_value="3mo")
        start = date.fromisoformat(payload["date"])
        trade_bars = [bar for bar in bars if start <= bar.day <= expiry]
        if not trade_bars:
            continue
        entry_low, entry_high = (float(value) for value in selection["entry_zone"])
        first = trade_bars[0]
        if not entry_low <= first.open <= entry_high:
            payload["outcome"] = {"status": "RESOLVED", "activated": False, "reason": "Otwarcie poza strefą aktywacji."}
        else:
            entry = first.open
            stop = float(selection["stop"])
            target = float(selection["target"])
            exit_price = trade_bars[-1].close
            exit_reason = "koniec_horyzontu"
            for bar in trade_bars:
                if bar.low <= stop:
                    exit_price, exit_reason = stop, "stop"
                    break
                if bar.high >= target:
                    exit_price, exit_reason = target, "target"
                    break
            risk = max(entry - stop, 0.01)
            gross_return = (exit_price / entry - 1) * 100
            net_return = gross_return - 0.38  # two-sided paper cost/slippage assumption
            mfe = (max(bar.high for bar in trade_bars) / entry - 1) * 100
            mae = (min(bar.low for bar in trade_bars) / entry - 1) * 100
            payload["outcome"] = {
                "status": "RESOLVED",
                "activated": True,
                "entry_price": round2(entry),
                "exit_price": round2(exit_price),
                "exit_reason": exit_reason,
                "return_percent": round(net_return, 3),
                "r_multiple": round((exit_price - entry) / risk, 3),
                "mfe_percent": round(mfe, 3),
                "mae_percent": round(mae, 3),
                "cost_assumption_percent": 0.38,
                "resolved_at": now_warsaw().isoformat(timespec="seconds"),
            }
        atomic_json(path, payload)
        changed += 1
    history = all_history()
    metrics = metric_summary(history)
    atomic_json(METRICS_PATH, metrics)
    current = load_json(PUBLIC_PATH)
    if isinstance(current, dict):
        current["metrics"] = metrics
        atomic_json(PUBLIC_PATH, current)
    return changed


def scheduled_window(mode: str, now: datetime) -> bool:
    minutes = now.hour * 60 + now.minute
    if mode == "morning":
        return 7 * 60 + 25 <= minutes <= 7 * 60 + 55
    if mode == "watchdog":
        return 8 * 60 + 5 <= minutes <= 8 * 60 + 25
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("morning", "watchdog", "settle", "validate"), default="morning")
    parser.add_argument("--force", action="store_true", help="Regenerate an error state inside the publication window.")
    parser.add_argument("--allow-outside-window", action="store_true", help="For manual diagnostics; the 08:30 cutoff still applies.")
    args = parser.parse_args()
    now = now_warsaw()
    config = load_config()

    if args.mode == "validate":
        validate_payload(load_json(PUBLIC_PATH), require_today=True, now=now)
        print(f"OK: {PUBLIC_PATH.relative_to(ROOT)}")
        return 0
    if args.mode == "settle":
        print(f"Resolved history records: {settle_history()}")
        return 0
    if not args.allow_outside_window and not scheduled_window(args.mode, now):
        print(f"Skip: {args.mode} is outside its Europe/Warsaw execution window ({now:%H:%M}).")
        return 0
    current = load_json(PUBLIC_PATH)
    if args.mode == "watchdog" and isinstance(current, dict) and current.get("date") == now.date().isoformat() and current.get("decision") != "AWARIA_DANYCH":
        validate_payload(current, require_today=True, now=now)
        print("Watchdog: today's locked publication is healthy.")
        return 0

    try:
        payload = generate(now=now)
        completed_at = now_warsaw()
        if (
            is_session_day(now.date(), config)
            and payload["decision"] != "AWARIA_DANYCH"
            and completed_at >= cutoff_for(now.date(), config)
        ):
            payload = failure_payload(
                completed_at,
                config,
                "Analiza zakończyła się po godzinie 08:30; sygnał nie został opublikowany.",
                "cutoff_after_generation",
            )
        else:
            payload["generated_at"] = completed_at.isoformat(timespec="seconds")
        publish(payload)
    except Exception as exc:
        failed_at = now_warsaw()
        payload = failure_payload(failed_at, config, f"Automatyczny wybór został zatrzymany: {type(exc).__name__}.", "publisher")
        publish(payload)
        print(f"Fail-closed publication: {exc}", file=sys.stderr)
    validate_payload(load_json(PUBLIC_PATH), require_today=True, now=now)
    print(f"Published {load_json(PUBLIC_PATH)['decision']} for {now.date().isoformat()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
