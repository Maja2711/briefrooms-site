#!/usr/bin/env python3
"""Authority-weighted Deep Evidence Engine for Stock Trading v2.

This module enriches the quantitative Opportunity Frontier with recent primary
and secondary evidence while remaining strictly shadow-only. Primary filings or
issuer reports are evidence, never automatic trade instructions. The engine
also creates a deterministic *research* SL/TP geometry from completed-session
features; it is explicitly not execution-ready and must be revalidated against
a fresh quote before any future production admission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date, datetime, time as clock_time, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts import stock_trading_v2_contracts as contracts
    from scripts import stock_trading_v2_discovery as discovery
except ModuleNotFoundError:  # pragma: no cover
    import stock_trading_v2_contracts as contracts
    import stock_trading_v2_discovery as discovery

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data/investments/stock_trading_v2_evidence_config.json"
FRONTIER_ROOT = ROOT / "data/investments/stock_trading_v2_frontier"
OUTPUT_ROOT = ROOT / "data/investments/stock_trading_v2_evidence"
SCHEMA_VERSION = "stock-trading-v2-deep-evidence-v1"

POSITIVE_TOKENS = (
    "raises guidance", "raised guidance", "beats estimates", "beat estimates",
    "record revenue", "record profit", "new contract", "contract award",
    "buyback", "share repurchase", "podnosi prognoz", "rekordow", "nowy kontrakt",
    "wygrywa przetarg", "skup akcji", "wzrost zysku", "wzrost przychod",
)
NEGATIVE_TOKENS = (
    "cuts guidance", "cut guidance", "profit warning", "misses estimates",
    "investigation", "lawsuit", "restatement", "default", "bankruptcy",
    "obniża prognoz", "obniza prognoz", "ostrzeżenie", "strata netto",
    "postępowanie", "postepowanie", "kara", "pozew", "restrukturyzac",
)
EVENT_RULES: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("earnings", 5, ("earnings", "results", "10-q", "10-k", "wyniki", "zysk", "przychod", "raport okresowy")),
    ("guidance", 5, ("guidance", "outlook", "forecast", "prognoz", "cele finansowe")),
    ("ma", 5, ("acquisition", "merger", "takeover", "przeję", "przejec", "akwizyc", "fuzj", "wezwanie")),
    ("regulatory", 5, ("investigation", "regulator", "lawsuit", "sec charges", "uokik", "knf", "kara", "postępow", "postepow", "pozew")),
    ("contract", 4, ("contract", "order", "award", "kontrakt", "umowa", "zamów", "zamow", "przetarg")),
    ("capital", 4, ("offering", "financing", "debt", "credit facility", "emisja", "obligac", "finansowanie", "kredyt")),
    ("buyback", 4, ("buyback", "repurchase", "skup akcji", "akcje własne", "akcje wlasne")),
    ("dividend", 4, ("dividend", "dywidend")),
    ("management", 3, ("ceo", "cfo", "management", "zarząd", "zarzad", "rada nadzorcza", "rezygnacja", "powołanie", "powolanie")),
    ("ownership", 3, ("insider", "shareholding", "beneficial ownership", "akcjonariusz", "znaczny pakiet", "art. 19")),
)


class EvidenceError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise contracts.ContractError(f"{path} must contain a JSON object")
    return payload


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalise_ticker(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = _read_json(path)
    if config.get("schema_version") != "stock-trading-v2-evidence-config-v1":
        raise contracts.ContractError("deep evidence configuration schema mismatch")
    if (config.get("governance") or {}).get("production_decision_influence") is not False:
        raise contracts.ContractError("deep evidence configuration must remain shadow-only")
    return config


def request_bytes(
    url: str,
    *,
    user_agent: str,
    timeout: int = 15,
    attempts: int = 2,
    accept: str = "application/json,text/xml,application/xml,text/html,*/*",
) -> bytes:
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": user_agent,
                    "Accept": accept,
                    "Accept-Language": "en-US,en;q=0.9,pl;q=0.8",
                    "Cache-Control": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - fixed/constructed public endpoints
                return response.read()
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.7 * (attempt + 1))
    raise EvidenceError(f"provider request failed: {type(last).__name__}: {last}")


def classify_title(title: str) -> dict[str, Any]:
    text = " ".join(str(title or "").casefold().split())
    event_type = "other"
    materiality = 2
    for name, level, tokens in EVENT_RULES:
        if any(token.casefold() in text for token in tokens):
            event_type, materiality = name, level
            break
    positive = sum(1 for token in POSITIVE_TOKENS if token.casefold() in text)
    negative = sum(1 for token in NEGATIVE_TOKENS if token.casefold() in text)
    direction = 1 if positive > negative else -1 if negative > positive else 0
    return {"event_type": event_type, "materiality": materiality, "direction": direction}


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _evidence_id(provider: str, symbol: str, title: str, published_at: str) -> str:
    raw = f"{provider}|{symbol}|{title}|{published_at}".encode("utf-8")
    return "ev-" + hashlib.sha256(raw).hexdigest()[:20]


def _news_rss(
    *,
    query: str,
    symbol: str,
    now: datetime,
    lookback_hours: int,
    user_agent: str,
    timeout: int,
    attempts: int,
    limit: int = 6,
) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    raw = request_bytes(url, user_agent=user_agent, timeout=timeout, attempts=attempts, accept="application/rss+xml,application/xml,text/xml,*/*")
    root = ET.fromstring(raw)
    rows: list[dict[str, Any]] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = _parse_dt(item.findtext("pubDate"))
        source_el = item.find("source")
        publisher = ((source_el.text if source_el is not None else "") or "").strip() or "Google News"
        if not title or not published:
            continue
        age_hours = max(0.0, (now - published).total_seconds() / 3600.0)
        if age_hours > lookback_hours:
            continue
        classification = classify_title(title)
        published_at = _iso(published)
        rows.append(
            {
                "evidence_id": _evidence_id(publisher, symbol, title, published_at),
                "provider": publisher,
                "authority": "secondary",
                "source_kind": "news",
                "title": title[:320],
                "url": link,
                "published_at": published_at,
                "age_hours": round(age_hours, 3),
                **classification,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def fetch_sec_ticker_map(*, config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    network = config.get("network") or {}
    raw = request_bytes(
        "https://www.sec.gov/files/company_tickers.json",
        user_agent=str(network.get("user_agent") or "BriefRooms Stock Trading v2 research contact@briefrooms.com"),
        timeout=int(network.get("timeout_seconds") or 15),
        attempts=int(network.get("attempts") or 2),
    )
    payload = json.loads(raw.decode("utf-8"))
    result: dict[str, dict[str, Any]] = {}
    for row in payload.values() if isinstance(payload, dict) else []:
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("ticker") or "").upper().strip()
        cik = row.get("cik_str")
        if ticker and cik is not None:
            result[_normalise_ticker(ticker)] = {"ticker": ticker, "cik": int(cik), "title": row.get("title")}
    return result


def fetch_sec_evidence(
    candidate: Mapping[str, Any],
    *,
    now: datetime,
    config: Mapping[str, Any],
    ticker_map: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    market_cfg = (config.get("markets") or {}).get("US") or {}
    network = config.get("network") or {}
    symbol = str(candidate.get("symbol") or "").upper()
    mapped = ticker_map.get(_normalise_ticker(symbol))
    if not mapped:
        return [], {"provider": "SEC_EDGAR", "ok": False, "reason": "ticker_not_mapped"}
    cik = int(mapped["cik"])
    url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
    raw = request_bytes(
        url,
        user_agent=str(network.get("user_agent") or "BriefRooms Stock Trading v2 research contact@briefrooms.com"),
        timeout=int(network.get("timeout_seconds") or 15),
        attempts=int(network.get("attempts") or 2),
    )
    payload = json.loads(raw.decode("utf-8"))
    recent = (((payload.get("filings") or {}).get("recent")) or {}) if isinstance(payload, dict) else {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accepted = recent.get("acceptanceDateTime") or []
    accessions = recent.get("accessionNumber") or []
    documents = recent.get("primaryDocument") or []
    descriptions = recent.get("primaryDocDescription") or []
    allowed = {str(value).upper() for value in market_cfg.get("sec_forms") or []}
    lookback = int(market_cfg.get("lookback_hours") or 168)
    rows: list[dict[str, Any]] = []
    for index, form_raw in enumerate(forms):
        form = str(form_raw or "").upper().strip()
        if allowed and form not in allowed:
            continue
        accepted_dt = _parse_dt(accepted[index] if index < len(accepted) else None)
        if accepted_dt is None and index < len(dates):
            try:
                filing_day = date.fromisoformat(str(dates[index]))
                accepted_dt = datetime.combine(filing_day, clock_time(12, 0), tzinfo=timezone.utc)
            except ValueError:
                continue
        if accepted_dt is None:
            continue
        age_hours = max(0.0, (now - accepted_dt).total_seconds() / 3600.0)
        if age_hours > lookback:
            continue
        accession = str(accessions[index] if index < len(accessions) else "").replace("-", "")
        document = str(documents[index] if index < len(documents) else "")
        filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}" if accession and document else url
        description = str(descriptions[index] if index < len(descriptions) else "").strip()
        title = f"SEC {form}" + (f" — {description}" if description else "")
        classification = classify_title(title)
        # SEC form taxonomy is itself a materiality signal even if the short
        # document label contains no directional language.
        if form in {"8-K", "10-Q", "10-K", "6-K", "20-F"}:
            classification["materiality"] = max(5, int(classification["materiality"]))
        elif form in {"S-3", "S-4", "424B3", "424B5"}:
            classification["materiality"] = max(4, int(classification["materiality"]))
        published_at = _iso(accepted_dt)
        rows.append(
            {
                "evidence_id": _evidence_id("SEC_EDGAR", symbol, title, published_at),
                "provider": "SEC_EDGAR",
                "authority": "primary",
                "source_kind": "regulatory_filing",
                "form": form,
                "title": title[:320],
                "url": filing_url,
                "published_at": published_at,
                "age_hours": round(age_hours, 3),
                **classification,
            }
        )
    rows.sort(key=lambda row: (float(row.get("age_hours") or 0.0), -int(row.get("materiality") or 0)))
    return rows[:8], {"provider": "SEC_EDGAR", "ok": True, "cik": cik, "recent_relevant": len(rows)}


def fetch_us_evidence(
    candidate: Mapping[str, Any],
    *,
    now: datetime,
    config: Mapping[str, Any],
    ticker_map: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    market_cfg = (config.get("markets") or {}).get("US") or {}
    network = config.get("network") or {}
    primary_rows: list[dict[str, Any]] = []
    secondary_rows: list[dict[str, Any]] = []
    primary_meta: dict[str, Any]
    secondary_meta: dict[str, Any]
    try:
        primary_rows, primary_meta = fetch_sec_evidence(candidate, now=now, config=config, ticker_map=ticker_map)
    except Exception as exc:
        primary_meta = {"provider": "SEC_EDGAR", "ok": False, "reason": f"{type(exc).__name__}: {exc}"[:500]}
    symbol = str(candidate.get("symbol") or "").upper()
    name = str(candidate.get("name") or symbol).strip()
    query = f'("{name}" OR "{symbol}") stock when:7d'
    try:
        secondary_rows = _news_rss(
            query=query,
            symbol=symbol,
            now=now,
            lookback_hours=int(market_cfg.get("lookback_hours") or 168),
            user_agent=str(network.get("user_agent") or "BriefRooms Stock Trading v2 research contact@briefrooms.com"),
            timeout=int(network.get("timeout_seconds") or 15),
            attempts=int(network.get("attempts") or 2),
        )
        secondary_meta = {"provider": "GOOGLE_NEWS_RSS", "ok": True, "recent_relevant": len(secondary_rows)}
    except Exception as exc:
        secondary_meta = {"provider": "GOOGLE_NEWS_RSS", "ok": False, "reason": f"{type(exc).__name__}: {exc}"[:500]}
    return primary_rows + secondary_rows, {"primary": primary_meta, "secondary": secondary_meta}


def fetch_gpw_evidence(
    candidate: Mapping[str, Any],
    *,
    now: datetime,
    config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        try:
            from scripts import gpw_event_layer as gpw_events
        except ModuleNotFoundError:  # pragma: no cover
            import gpw_event_layer as gpw_events
    except Exception as exc:
        return [], {
            "primary": {"provider": "ESPI_EBI_PAP", "ok": False, "reason": f"import:{type(exc).__name__}"},
            "secondary": {"provider": "INDEPENDENT_NEWS", "ok": False, "reason": "event_layer_unavailable"},
        }

    company = {"symbol": candidate.get("market_data_symbol") or candidate.get("symbol"), "name": candidate.get("name")}
    primary_raw: list[dict[str, Any]] = []
    secondary_raw: list[dict[str, Any]] = []
    try:
        primary_raw = gpw_events.official_report_items(company, now=now.astimezone(gpw_events.gpw.WARSAW), limit=6)
        primary_meta = {"provider": "ESPI_EBI_PAP", "ok": True, "recent_relevant": len(primary_raw)}
    except Exception as exc:
        primary_meta = {"provider": "ESPI_EBI_PAP", "ok": False, "reason": f"{type(exc).__name__}: {exc}"[:500]}
    try:
        secondary_raw = gpw_events.gpw.news_items(company, now=now.astimezone(gpw_events.gpw.WARSAW), limit=6)
        secondary_meta = {"provider": "INDEPENDENT_NEWS", "ok": True, "recent_relevant": len(secondary_raw)}
    except Exception as exc:
        secondary_meta = {"provider": "INDEPENDENT_NEWS", "ok": False, "reason": f"{type(exc).__name__}: {exc}"[:500]}

    symbol = str(candidate.get("symbol") or "").upper()
    rows: list[dict[str, Any]] = []
    for authority, source_kind, items in (
        ("primary", "issuer_report", primary_raw),
        ("secondary", "news", secondary_raw),
    ):
        for item in items:
            published = _parse_dt(item.get("published_at"))
            if published is None:
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            classification = classify_title(title)
            if item.get("event_type"):
                classification["event_type"] = item.get("event_type")
            if item.get("materiality") is not None:
                classification["materiality"] = int(item.get("materiality") or classification["materiality"])
            published_at = _iso(published)
            rows.append(
                {
                    "evidence_id": _evidence_id(str(item.get("publisher") or "PAP"), symbol, title, published_at),
                    "provider": str(item.get("publisher") or ("ESPI_EBI_PAP" if authority == "primary" else "NEWS")),
                    "authority": authority,
                    "source_kind": source_kind,
                    "title": title[:320],
                    "url": item.get("url"),
                    "published_at": published_at,
                    "age_hours": round(max(0.0, (now - published).total_seconds() / 3600.0), 3),
                    **classification,
                }
            )
    return rows, {"primary": primary_meta, "secondary": secondary_meta}


def provider_status(meta: Mapping[str, Any]) -> str:
    primary_ok = bool((meta.get("primary") or {}).get("ok"))
    secondary_ok = bool((meta.get("secondary") or {}).get("ok"))
    if primary_ok and secondary_ok:
        return "COMPLETE"
    if primary_ok or secondary_ok:
        return "DEGRADED"
    return "DATA_ERROR"


def _dedupe_evidence(rows: Sequence[Mapping[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            0 if row.get("authority") == "primary" else 1,
            -int(row.get("materiality") or 0),
            float(row.get("age_hours") or 999999),
        ),
    )
    for row in ordered:
        key = str(row.get("evidence_id") or "") or contracts.payload_sha256(row)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
        if len(result) >= limit:
            break
    return result


def evidence_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    status: str,
    config: Mapping[str, Any],
    returns: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    scoring = config.get("scoring") or {}
    neutral = float(scoring.get("neutral_evidence_quality") or 50.0)
    primary_weight = float(scoring.get("primary_authority_weight") or 1.0)
    secondary_weight = float(scoring.get("secondary_authority_weight") or 0.55)
    max_dir_overlay = float(scoring.get("maximum_directional_overlay_points") or 8.0)
    max_reaction_overlay = float(scoring.get("maximum_reaction_overlay_points") or 3.0)
    degraded_penalty = float(scoring.get("degraded_provider_penalty_points") or 2.0)

    if not rows:
        quality = neutral - (degraded_penalty if status == "DEGRADED" else 0.0)
        if status == "DATA_ERROR":
            quality = 0.0
        return {
            "evidence_quality_score": round(_clamp(quality), 4),
            "directional_overlay_points": 0.0,
            "reaction_overlay_points": 0.0,
            "total_overlay_points": 0.0,
            "primary_count": 0,
            "secondary_count": 0,
            "maximum_materiality": 0,
            "material_event_present": False,
            "explicit_directional_event_present": False,
        }

    primary_count = sum(1 for row in rows if row.get("authority") == "primary")
    secondary_count = len(rows) - primary_count
    maximum_materiality = max(int(row.get("materiality") or 0) for row in rows)
    recency_values = [max(0.0, 1.0 - float(row.get("age_hours") or 0.0) / 168.0) for row in rows]
    recency = sum(recency_values) / max(1, len(recency_values))
    quality = neutral
    quality += min(18.0, primary_count * 6.0)
    quality += min(8.0, secondary_count * 2.0)
    quality += max(0.0, maximum_materiality - 2) * 4.0
    quality += recency * 8.0
    if status == "DEGRADED":
        quality -= degraded_penalty
    elif status == "DATA_ERROR":
        quality = 0.0

    directional_numerator = 0.0
    directional_denominator = 0.0
    explicit_directional = False
    for row in rows:
        direction = int(row.get("direction") or 0)
        if direction == 0:
            continue
        explicit_directional = True
        authority = primary_weight if row.get("authority") == "primary" else secondary_weight
        freshness = max(0.15, 1.0 - float(row.get("age_hours") or 0.0) / 168.0)
        weight = authority * max(1.0, float(row.get("materiality") or 1.0)) * freshness
        directional_numerator += direction * weight
        directional_denominator += weight
    directional_overlay = 0.0
    if directional_denominator > 0:
        directional_overlay = max_dir_overlay * directional_numerator / directional_denominator

    reaction_overlay = 0.0
    material_event = maximum_materiality >= 4
    returns = returns or {}
    r1 = _finite(returns.get("1"))
    r5 = _finite(returns.get("5"))
    if material_event and not explicit_directional and r1 is not None and r5 is not None:
        if r1 > 0 and r5 > 0:
            reaction_overlay = max_reaction_overlay
        elif r1 < 0 and r5 < 0:
            reaction_overlay = -max_reaction_overlay
    total_overlay = max(-max_dir_overlay, min(max_dir_overlay, directional_overlay + reaction_overlay))
    return {
        "evidence_quality_score": round(_clamp(quality), 4),
        "directional_overlay_points": round(directional_overlay, 4),
        "reaction_overlay_points": round(reaction_overlay, 4),
        "total_overlay_points": round(total_overlay, 4),
        "primary_count": primary_count,
        "secondary_count": secondary_count,
        "maximum_materiality": maximum_materiality,
        "material_event_present": material_event,
        "explicit_directional_event_present": explicit_directional,
    }


def research_risk_plan(candidate: Mapping[str, Any], *, market: str, config: Mapping[str, Any]) -> dict[str, Any]:
    market_cfg = (config.get("markets") or {}).get(market) or {}
    risk_cfg = market_cfg.get("risk") or {}
    features = candidate.get("features") or {}
    entry = _finite(features.get("last_close"))
    atr_fraction = _finite(features.get("atr_fraction"))
    if entry is None or entry <= 0 or atr_fraction is None or atr_fraction <= 0:
        return {"status": "INVALID", "reason": "missing_reference_price_or_atr", "execution_ready": False}
    atr_multiple = float(risk_cfg.get("atr_multiple") or 1.0)
    floor = float(risk_cfg.get("risk_floor_percent") or 0.01)
    maximum = float(risk_cfg.get("maximum_risk_percent") or 0.07)
    rr = float(risk_cfg.get("research_target_reward_risk") or 2.0)
    risk_fraction = max(atr_fraction * atr_multiple, floor)
    if risk_fraction > maximum:
        return {
            "status": "INVALID",
            "reason": "risk_above_maximum",
            "risk_percent": round(risk_fraction, 6),
            "maximum_risk_percent": maximum,
            "execution_ready": False,
        }
    risk_amount = entry * risk_fraction
    stop = entry - risk_amount
    target = entry + risk_amount * rr
    return {
        "status": "VALID_RESEARCH_REFERENCE",
        "reference_price": round(entry, 8),
        "stop": round(stop, 8),
        "target": round(target, 8),
        "risk_percent": round(risk_fraction, 6),
        "reward_risk": round(rr, 4),
        "atr_fraction": round(atr_fraction, 8),
        "execution_ready": False,
        "execution_requirement": "fresh_quote_and_intraday_risk_revalidation",
        "holding_policy": "OPEN_ENDED_MODEL_CONTROLLED",
    }


def build_deep_evidence(
    frontier: Mapping[str, Any],
    evidence_by_symbol: Mapping[str, tuple[Sequence[Mapping[str, Any]], Mapping[str, Any]]],
    config: Mapping[str, Any],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    discovery.validate_frontier(frontier)
    market = str(frontier.get("market") or "").upper()
    if market not in contracts.SUPPORTED_MARKETS:
        raise contracts.ContractError("deep evidence market unsupported")
    generated = generated_at or _now()
    limit = int(config.get("frontier_candidates_to_enrich") or 10)
    rows: list[dict[str, Any]] = []
    for candidate in list(frontier.get("candidates") or [])[:limit]:
        symbol = str(candidate.get("symbol") or "").upper()
        raw_evidence, provider_meta = evidence_by_symbol.get(symbol, ([], {"primary": {"ok": False}, "secondary": {"ok": False}}))
        evidence = _dedupe_evidence(raw_evidence)
        status = provider_status(provider_meta)
        metrics = evidence_metrics(
            evidence,
            status=status,
            config=config,
            returns=(candidate.get("features") or {}).get("returns") or {},
        )
        base_score = float(candidate.get("opportunity_score") or 0.0)
        data_penalty = float((config.get("scoring") or {}).get("degraded_provider_penalty_points") or 2.0) if status == "DEGRADED" else 0.0
        deep_score = _clamp(base_score + float(metrics["total_overlay_points"]) - data_penalty)
        if status == "DATA_ERROR":
            deep_score = max(0.0, base_score - 10.0)
        rows.append(
            {
                "symbol": symbol,
                "market_data_symbol": candidate.get("market_data_symbol"),
                "name": candidate.get("name"),
                "frontier_rank": candidate.get("frontier_rank"),
                "opportunity_score": round(base_score, 6),
                "deep_opportunity_score": round(deep_score, 6),
                "evidence_status": status,
                "evidence_metrics": metrics,
                "provider_health": deepcopy(dict(provider_meta)),
                "evidence": evidence,
                "liquidity": deepcopy(candidate.get("liquidity") or {}),
                "freshness": deepcopy(candidate.get("freshness") or {}),
                "features": {
                    "latest_session": (candidate.get("features") or {}).get("latest_session"),
                    "last_close": (candidate.get("features") or {}).get("last_close"),
                    "returns": deepcopy((candidate.get("features") or {}).get("returns") or {}),
                    "atr_fraction": (candidate.get("features") or {}).get("atr_fraction"),
                    "median_turnover_20d": (candidate.get("features") or {}).get("median_turnover_20d"),
                    "volume_ratio_20d": (candidate.get("features") or {}).get("volume_ratio_20d"),
                },
                "research_risk_plan": research_risk_plan(candidate, market=market, config=config),
                "admission": {
                    "status": "PENDING_SHADOW_PORTFOLIO_COMPARISON",
                    "production_decision_influence": False,
                },
            }
        )
    rows.sort(key=lambda row: (float(row["deep_opportunity_score"]), float(row["opportunity_score"])), reverse=True)
    for rank, row in enumerate(rows, start=1):
        row["deep_rank"] = rank
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "market": market,
        "generated_at": _iso(generated),
        "source_frontier_sha256": frontier.get("frontier_sha256"),
        "source_frontier_generated_at": frontier.get("generated_at"),
        "candidate_count": len(rows),
        "candidates": rows,
        "governance": {
            "production_decision_influence": False,
            "automatic_portfolio_admission": False,
            "primary_sources_are_evidence_not_trade_signals": True,
            "research_risk_plan_execution_ready": False,
            "no_forced_trade": True,
        },
    }
    payload["evidence_sha256"] = contracts.payload_sha256(payload)
    validate_deep_evidence(payload)
    return payload


def validate_deep_evidence(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise contracts.ContractError("deep evidence schema mismatch")
    if payload.get("market") not in contracts.SUPPORTED_MARKETS:
        raise contracts.ContractError("deep evidence market unsupported")
    governance = payload.get("governance") or {}
    if governance.get("production_decision_influence") is not False or governance.get("automatic_portfolio_admission") is not False:
        raise contracts.ContractError("deep evidence escaped shadow governance")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or int(payload.get("candidate_count", -1)) != len(candidates):
        raise contracts.ContractError("deep evidence candidate count mismatch")
    seen: set[str] = set()
    last_score = float("inf")
    for rank, row in enumerate(candidates, start=1):
        if not isinstance(row, Mapping):
            raise contracts.ContractError("deep evidence candidate must be an object")
        symbol = str(row.get("symbol") or "")
        if not symbol or symbol in seen:
            raise contracts.ContractError("deep evidence duplicate/missing symbol")
        seen.add(symbol)
        if int(row.get("deep_rank") or 0) != rank:
            raise contracts.ContractError("deep evidence rank mismatch")
        score = float(row.get("deep_opportunity_score") or 0.0)
        if score > last_score + 1e-9:
            raise contracts.ContractError("deep evidence candidates are not sorted")
        last_score = score
        if row.get("evidence_status") not in {"COMPLETE", "DEGRADED", "DATA_ERROR"}:
            raise contracts.ContractError("deep evidence invalid provider status")
        if (row.get("admission") or {}).get("production_decision_influence") is not False:
            raise contracts.ContractError("deep evidence candidate escaped shadow governance")
        risk = row.get("research_risk_plan") or {}
        if risk.get("status") == "VALID_RESEARCH_REFERENCE":
            entry = _finite(risk.get("reference_price"))
            stop = _finite(risk.get("stop"))
            target = _finite(risk.get("target"))
            if entry is None or stop is None or target is None or not stop < entry < target:
                raise contracts.ContractError("deep evidence invalid research risk geometry")
            if risk.get("execution_ready") is not False:
                raise contracts.ContractError("research risk plan cannot be execution-ready")
    body = dict(payload)
    stored = str(body.pop("evidence_sha256", ""))
    if not stored or stored != contracts.payload_sha256(body):
        raise contracts.ContractError("deep evidence hash mismatch")


def collect_evidence(frontier: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, tuple[list[dict[str, Any]], dict[str, Any]]]:
    market = str(frontier.get("market") or "").upper()
    candidates = list(frontier.get("candidates") or [])[: int(config.get("frontier_candidates_to_enrich") or 10)]
    now = _now()
    ticker_map: dict[str, dict[str, Any]] = {}
    ticker_error: str | None = None
    if market == "US":
        try:
            ticker_map = fetch_sec_ticker_map(config=config)
        except Exception as exc:
            ticker_error = f"{type(exc).__name__}: {exc}"[:500]

    result: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
    workers = min(max(1, int((config.get("network") or {}).get("workers") or 4)), max(1, len(candidates)))

    def one(candidate: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        symbol = str(candidate.get("symbol") or "").upper()
        if market == "US":
            if ticker_error:
                rows, meta = fetch_us_evidence(candidate, now=now, config=config, ticker_map={})
                meta["primary"] = {"provider": "SEC_EDGAR", "ok": False, "reason": ticker_error}
                return symbol, rows, meta
            rows, meta = fetch_us_evidence(candidate, now=now, config=config, ticker_map=ticker_map)
            return symbol, rows, meta
        rows, meta = fetch_gpw_evidence(candidate, now=now, config=config)
        return symbol, rows, meta

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, candidate) for candidate in candidates]
        for future in as_completed(futures):
            try:
                symbol, rows, meta = future.result()
            except Exception as exc:
                # A single issuer/provider failure must not destroy the broad
                # frontier. It becomes an auditable DATA_ERROR for that name.
                symbol = "UNKNOWN"
                rows = []
                meta = {"primary": {"ok": False, "reason": str(exc)[:500]}, "secondary": {"ok": False, "reason": str(exc)[:500]}}
            if symbol != "UNKNOWN":
                result[symbol] = (rows, meta)
    return result


def run_market(
    market: str,
    *,
    frontier_path: Path | None = None,
    config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    market = str(market).upper()
    config = load_config(config_path)
    frontier_path = frontier_path or (FRONTIER_ROOT / f"{market.lower()}.json")
    frontier = _read_json(frontier_path)
    discovery.validate_frontier(frontier)
    if str(frontier.get("market") or "").upper() != market:
        raise contracts.ContractError("frontier/evidence market mismatch")
    evidence = collect_evidence(frontier, config)
    return build_deep_evidence(frontier, evidence, config)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["US", "GPW", "us", "gpw"], required=True)
    parser.add_argument("--frontier", type=Path)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    market = str(args.market).upper()
    payload = run_market(market, frontier_path=args.frontier, config_path=args.config)
    output = args.output or (OUTPUT_ROOT / f"{market.lower()}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "market": market,
        "candidate_count": payload["candidate_count"],
        "top_symbol": (payload.get("candidates") or [{}])[0].get("symbol") if payload.get("candidates") else None,
        "complete": sum(1 for row in payload.get("candidates") or [] if row.get("evidence_status") == "COMPLETE"),
        "degraded": sum(1 for row in payload.get("candidates") or [] if row.get("evidence_status") == "DEGRADED"),
        "data_error": sum(1 for row in payload.get("candidates") or [] if row.get("evidence_status") == "DATA_ERROR"),
        "production_decision_influence": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
