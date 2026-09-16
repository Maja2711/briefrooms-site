#!/usr/bin/env python3
"""Corporate / technology / crypto event radar for production Investment Intelligence.

This collector complements the geopolitical collector. It observes material company
and executive statements, earnings/guidance, product/strategy events and high-signal
technology/crypto developments. It never opens trades. Events are normalized into a
shared schema consumed by engine-specific decision weighting.

The static radar covers globally important technology and crypto entities while the
dynamic radar also follows companies currently held or selected by Stock Trading.
"""
from __future__ import annotations

import hashlib
import math
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Mapping

import investment_event_intelligence as event

RADAR_VERSION = "corporate-tech-crypto-radar-v1"
LOOKBACK_HOURS = 30
MAX_EVENTS = 28
MAX_DYNAMIC_ENTITIES = 10

# Roles are intentionally generic. Names are used as high-authority tracked actors,
# while query aliases such as "NVIDIA CEO" continue to work if management changes.
TRACKED_ENTITIES: tuple[dict[str, Any], ...] = (
    {"key": "nvidia", "name": "NVIDIA", "symbols": ["NVDA"], "aliases": ["NVIDIA", "Jensen Huang", "NVIDIA CEO"], "leaders": ["Jensen Huang"], "themes": ["semiconductor", "ai_infrastructure", "ai_market"], "tier": "mega_cap"},
    {"key": "microsoft", "name": "Microsoft", "symbols": ["MSFT"], "aliases": ["Microsoft", "Satya Nadella", "Microsoft CEO"], "leaders": ["Satya Nadella"], "themes": ["cloud", "software", "ai_market"], "tier": "mega_cap"},
    {"key": "apple", "name": "Apple", "symbols": ["AAPL"], "aliases": ["Apple", "Tim Cook", "Apple CEO"], "leaders": ["Tim Cook"], "themes": ["consumer_tech", "ai_market"], "tier": "mega_cap"},
    {"key": "alphabet", "name": "Alphabet", "symbols": ["GOOGL", "GOOG"], "aliases": ["Alphabet", "Google", "Sundar Pichai", "Demis Hassabis", "Google CEO"], "leaders": ["Sundar Pichai", "Demis Hassabis"], "themes": ["cloud", "software", "ai_market"], "tier": "mega_cap"},
    {"key": "meta", "name": "Meta Platforms", "symbols": ["META"], "aliases": ["Meta Platforms", "Mark Zuckerberg", "Meta CEO"], "leaders": ["Mark Zuckerberg"], "themes": ["software", "ai_market", "consumer_tech"], "tier": "mega_cap"},
    {"key": "amazon", "name": "Amazon", "symbols": ["AMZN"], "aliases": ["Amazon", "Andy Jassy", "Amazon CEO", "AWS"], "leaders": ["Andy Jassy"], "themes": ["cloud", "ai_infrastructure", "consumer_tech"], "tier": "mega_cap"},
    {"key": "tesla", "name": "Tesla", "symbols": ["TSLA"], "aliases": ["Tesla", "Elon Musk", "Tesla CEO"], "leaders": ["Elon Musk"], "themes": ["ai_market", "robotics", "automotive"], "tier": "mega_cap"},
    {"key": "broadcom", "name": "Broadcom", "symbols": ["AVGO"], "aliases": ["Broadcom", "Hock Tan", "Broadcom CEO"], "leaders": ["Hock Tan"], "themes": ["semiconductor", "ai_infrastructure"], "tier": "mega_cap"},
    {"key": "amd", "name": "AMD", "symbols": ["AMD"], "aliases": ["Advanced Micro Devices", "AMD", "Lisa Su", "AMD CEO"], "leaders": ["Lisa Su"], "themes": ["semiconductor", "ai_infrastructure"], "tier": "large_cap"},
    {"key": "oracle", "name": "Oracle", "symbols": ["ORCL"], "aliases": ["Oracle", "Larry Ellison", "Safra Catz", "Oracle CEO"], "leaders": ["Larry Ellison", "Safra Catz"], "themes": ["cloud", "software", "ai_infrastructure"], "tier": "large_cap"},
    {"key": "asml", "name": "ASML", "symbols": ["ASML"], "aliases": ["ASML", "Christophe Fouquet", "ASML CEO"], "leaders": ["Christophe Fouquet"], "themes": ["semiconductor", "ai_infrastructure"], "tier": "large_cap"},
    {"key": "tsmc", "name": "TSMC", "symbols": ["TSM"], "aliases": ["TSMC", "Taiwan Semiconductor", "C. C. Wei", "CC Wei", "TSMC CEO"], "leaders": ["C. C. Wei", "CC Wei"], "themes": ["semiconductor", "ai_infrastructure"], "tier": "large_cap"},
    {"key": "openai", "name": "OpenAI", "symbols": [], "aliases": ["OpenAI", "Sam Altman"], "leaders": ["Sam Altman"], "themes": ["ai_market", "ai_infrastructure"], "tier": "systemic_private"},
    {"key": "anthropic", "name": "Anthropic", "symbols": [], "aliases": ["Anthropic", "Dario Amodei"], "leaders": ["Dario Amodei"], "themes": ["ai_market", "ai_infrastructure"], "tier": "systemic_private"},
    {"key": "xai", "name": "xAI", "symbols": [], "aliases": ["xAI", "Grok"], "leaders": [], "themes": ["ai_market", "ai_infrastructure"], "tier": "systemic_private"},
    {"key": "coinbase", "name": "Coinbase", "symbols": ["COIN"], "aliases": ["Coinbase", "Brian Armstrong", "Coinbase CEO"], "leaders": ["Brian Armstrong"], "themes": ["crypto_market", "bitcoin", "ethereum"], "tier": "crypto_public"},
    {"key": "strategy", "name": "Strategy", "symbols": ["MSTR"], "aliases": ["Strategy", "MicroStrategy", "Michael Saylor"], "leaders": ["Michael Saylor"], "themes": ["crypto_market", "bitcoin"], "tier": "crypto_public"},
    {"key": "circle", "name": "Circle", "symbols": ["CRCL"], "aliases": ["Circle Internet", "Circle", "Jeremy Allaire"], "leaders": ["Jeremy Allaire"], "themes": ["crypto_market", "stablecoin"], "tier": "crypto_public"},
    {"key": "binance", "name": "Binance", "symbols": [], "aliases": ["Binance", "Richard Teng", "Changpeng Zhao", "CZ Binance"], "leaders": ["Richard Teng", "Changpeng Zhao"], "themes": ["crypto_market", "bitcoin", "ethereum"], "tier": "crypto_systemic"},
    {"key": "ethereum", "name": "Ethereum", "symbols": [], "aliases": ["Ethereum", "Vitalik Buterin"], "leaders": ["Vitalik Buterin"], "themes": ["crypto_market", "ethereum"], "tier": "crypto_protocol"},
    {"key": "solana", "name": "Solana", "symbols": [], "aliases": ["Solana", "Anatoly Yakovenko"], "leaders": ["Anatoly Yakovenko"], "themes": ["crypto_market", "solana"], "tier": "crypto_protocol"},
    {"key": "ripple", "name": "Ripple", "symbols": [], "aliases": ["Ripple", "Brad Garlinghouse", "XRP"], "leaders": ["Brad Garlinghouse"], "themes": ["crypto_market", "xrp"], "tier": "crypto_protocol"},
    {"key": "tether", "name": "Tether", "symbols": [], "aliases": ["Tether", "Paolo Ardoino", "USDT"], "leaders": ["Paolo Ardoino"], "themes": ["crypto_market", "stablecoin"], "tier": "crypto_systemic"},
)

CORPORATE_SIGNAL_TERMS = (
    "earnings", "guidance", "outlook", "forecast", "revenue", "profit", "margin",
    "demand", "orders", "backlog", "data center", "datacenter", "capex", "spending",
    "launch", "unveil", "roadmap", "chip", "gpu", "ai model", "artificial intelligence",
    "partnership", "contract", "deal", "acquisition", "merger", "antitrust",
    "investigation", "export", "restriction", "ban", "recall", "delay", "outage",
    "breach", "hack", "exploit", "upgrade", "protocol", "stablecoin", "bitcoin",
    "ethereum", "crypto", "etf", "reserves", "treasury", "regulation", "regulator",
)

POSITIVE_RULES: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    ("guidance_raise", 1.00, ("raises guidance", "raised guidance", "boosts guidance", "lifts guidance", "raises outlook", "raised outlook", "raises forecast", "raised forecast")),
    ("earnings_beat", 0.92, ("beats estimates", "beat estimates", "beats expectations", "beat expectations", "tops estimates", "above expectations", "better than expected")),
    ("demand_strength", 0.76, ("strong demand", "demand surges", "surging demand", "record demand", "orders surge", "record orders", "backlog grows", "sold out")),
    ("regulatory_approval", 0.82, ("wins approval", "approved by", "regulatory approval", "license approved", "licence approved")),
    ("major_contract", 0.68, ("wins contract", "major contract", "strategic partnership", "multi-year deal", "multiyear deal")),
    ("record_results", 0.80, ("record revenue", "record profit", "record sales")),
)

NEGATIVE_RULES: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    ("guidance_cut", 1.00, ("cuts guidance", "cut guidance", "lowers guidance", "lowered guidance", "slashes guidance", "cuts outlook", "lowers outlook", "cuts forecast", "lowers forecast")),
    ("earnings_miss", 0.92, ("misses estimates", "missed estimates", "misses expectations", "missed expectations", "below expectations", "worse than expected")),
    ("profit_warning", 0.94, ("profit warning", "revenue warning", "warns on profit", "warns on revenue")),
    ("demand_weakness", 0.82, ("weak demand", "demand weakens", "slowing demand", "demand slowdown", "orders fall", "order slowdown")),
    ("operational_disruption", 0.82, ("production halt", "production delay", "delays launch", "delayed launch", "plant closure", "recall", "major outage")),
    ("security_incident", 0.96, ("security breach", "data breach", "hacked", "hack attack", "exploit", "stolen funds", "drained funds")),
    ("regulatory_adverse", 0.86, ("antitrust probe", "investigation into", "regulator probes", "regulatory probe", "export ban", "export restriction", "license revoked", "licence revoked", "lawsuit against")),
)

NEUTRAL_EVENT_RULES: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    ("earnings_report", 0.66, ("reports earnings", "earnings report", "quarterly results", "reports quarterly")),
    ("guidance_statement", 0.66, ("guidance", "outlook", "forecast")),
    ("product_roadmap", 0.52, ("launches", "unveils", "roadmap", "new chip", "new gpu", "new model")),
    ("strategic_statement", 0.48, ("ceo says", "ceo sees", "ceo expects", "founder says", "executive says")),
    ("crypto_protocol_update", 0.58, ("protocol upgrade", "network upgrade", "hard fork", "mainnet upgrade")),
)

SECTOR_POSITIVE_TERMS = (
    "ai spending rises", "ai spending increases", "increase ai spending", "raises capex",
    "increase capex", "data center demand", "datacenter demand", "gpu demand",
    "cloud demand", "bitcoin adoption", "institutional demand", "crypto adoption",
    "protocol upgrade", "network upgrade", "etf approval",
)
SECTOR_NEGATIVE_TERMS = (
    "cuts capex", "reduce capex", "ai spending slows", "data center slowdown",
    "datacenter slowdown", "gpu demand weak", "crypto hack", "exchange hack",
    "protocol exploit", "network outage", "stablecoin depeg", "regulatory crackdown",
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _phrase(text: str, phrase: str) -> bool:
    escaped = re.escape(_norm(phrase)).replace(r"\ ", r"\s+")
    return bool(escaped and re.search(rf"(?<![\w]){escaped}(?![\w])", text))


def _entity_aliases(entity: Mapping[str, Any]) -> list[str]:
    return [str(alias) for alias in entity.get("aliases") or [] if str(alias).strip()]


def match_entity(title: str, entities: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any] | None:
    text = _norm(title)
    candidates = list(entities or TRACKED_ENTITIES)
    # Prefer longer aliases to avoid generic names winning over precise people/company names.
    ranked: list[tuple[int, dict[str, Any]]] = []
    for entity in candidates:
        matched = [alias for alias in _entity_aliases(entity) if _phrase(text, alias)]
        if matched:
            ranked.append((max(len(alias) for alias in matched), dict(entity)))
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def _has_signal(title: str) -> bool:
    text = _norm(title)
    return any(token in text for token in CORPORATE_SIGNAL_TERMS)


def classify_signal(title: str) -> dict[str, Any]:
    text = _norm(title)
    for kind, severity, tokens in NEGATIVE_RULES:
        if any(token in text for token in tokens):
            return {"event_kind": kind, "direct_impact": -1.0, "severity": severity, "action_strength": 1.0}
    for kind, severity, tokens in POSITIVE_RULES:
        if any(token in text for token in tokens):
            return {"event_kind": kind, "direct_impact": 1.0, "severity": severity, "action_strength": 1.0}
    for kind, severity, tokens in NEUTRAL_EVENT_RULES:
        if any(token in text for token in tokens):
            return {"event_kind": kind, "direct_impact": 0.0, "severity": severity, "action_strength": 0.78}
    return {"event_kind": "material_statement", "direct_impact": 0.0, "severity": 0.42, "action_strength": 0.68}


def sector_impact(title: str, entity: Mapping[str, Any], direct_impact: float) -> float:
    text = _norm(title)
    if any(token in text for token in SECTOR_NEGATIVE_TERMS):
        return -1.0
    if any(token in text for token in SECTOR_POSITIVE_TERMS):
        return 1.0
    # Direct earnings/guidance from a semiconductor or crypto-systemic entity has a
    # bounded read-through to its theme, but much weaker than the company effect.
    themes = set(str(theme) for theme in entity.get("themes") or [])
    if direct_impact and themes & {"semiconductor", "ai_infrastructure", "crypto_market"}:
        return 0.45 * float(direct_impact)
    return 0.0


def actor_authority(title: str, entity: Mapping[str, Any]) -> tuple[float, str, str | None]:
    text = _norm(title)
    for leader in entity.get("leaders") or []:
        if _phrase(text, str(leader)):
            return 0.97, "tracked_executive_or_founder", str(leader)
    if any(token in text for token in (" ceo ", " cfo ", " chief executive ", " founder ", " chair ", " chairman ")):
        return 0.94, "company_executive", None
    signal = classify_signal(title)
    if signal["event_kind"] in {"guidance_raise", "guidance_cut", "earnings_beat", "earnings_miss", "earnings_report", "profit_warning"}:
        return 0.92, "company_results_or_guidance", None
    return 0.82, "reported_company_or_industry_actor", None


def scenario_tags(entity: Mapping[str, Any]) -> list[str]:
    tags = [f"entity:{entity.get('key')}"]
    tags.extend(f"theme:{theme}" for theme in entity.get("themes") or [])
    themes = set(str(theme) for theme in entity.get("themes") or [])
    if "crypto_market" in themes:
        tags.append("crypto_market")
    if "bitcoin" in themes:
        tags.append("bitcoin")
    if "ethereum" in themes:
        tags.append("ethereum")
    if themes & {"ai_market", "ai_infrastructure"}:
        tags.append("ai_market")
    if "semiconductor" in themes:
        tags.append("semiconductor")
    return list(dict.fromkeys(tags))


def _dynamic_entities() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    state = event._load(event.STOCK_STATE_PATH, {})
    seen: set[tuple[str, str]] = set()

    def add(symbol: Any, name: Any, sector: Any = None) -> None:
        symbol_s = str(symbol or "").upper().strip()
        name_s = str(name or "").strip()
        if not symbol_s or not name_s:
            return
        key = (symbol_s, _norm(name_s))
        if key in seen:
            return
        seen.add(key)
        rows.append({
            "key": f"dynamic-{symbol_s.casefold()}",
            "name": name_s,
            "symbols": [symbol_s],
            "aliases": [name_s],
            "leaders": [],
            "themes": [str(sector or "").casefold()] if sector else [],
            "tier": "portfolio_dynamic",
        })

    for market_row in (state.get("markets") or {}).values():
        for position in (market_row or {}).get("open_positions") or []:
            add(position.get("symbol") or position.get("ticker"), position.get("name"), position.get("sector"))

    for path in (event.GPW_CANDIDATE_PATH, event.US_CANDIDATE_PATH):
        payload = event._load(path, {})
        selection = payload.get("selection") if isinstance(payload.get("selection"), Mapping) else {}
        add(selection.get("symbol") or selection.get("ticker"), selection.get("name"), selection.get("sector"))

    static_symbols = {symbol for entity in TRACKED_ENTITIES for symbol in entity.get("symbols") or []}
    static_names = {_norm(entity.get("name")) for entity in TRACKED_ENTITIES}
    return [row for row in rows if not (set(row["symbols"]) & static_symbols or _norm(row["name"]) in static_names)][:MAX_DYNAMIC_ENTITIES]


def entities() -> list[dict[str, Any]]:
    return [dict(row) for row in TRACKED_ENTITIES] + _dynamic_entities()


def _query_groups(all_entities: list[dict[str, Any]]) -> list[str]:
    static = [row for row in all_entities if row.get("tier") != "portfolio_dynamic"]
    dynamic = [row for row in all_entities if row.get("tier") == "portfolio_dynamic"]
    groups = [static[index:index + 5] for index in range(0, len(static), 5)]
    if dynamic:
        groups.extend(dynamic[index:index + 5] for index in range(0, len(dynamic), 5))
    queries: list[str] = []
    signal_clause = '(earnings OR guidance OR outlook OR forecast OR revenue OR demand OR "data center" OR capex OR launch OR roadmap OR partnership OR contract OR antitrust OR investigation OR export OR outage OR breach OR hack OR upgrade OR bitcoin OR ethereum OR crypto OR stablecoin OR regulation)'
    for group in groups:
        aliases: list[str] = []
        for entity in group:
            source_aliases = _entity_aliases(entity)
            aliases.extend(source_aliases[:3] if source_aliases else [str(entity.get("name") or "")])
        quoted = " OR ".join(f'"{alias}"' for alias in aliases if alias)
        if quoted:
            queries.append(f'({quoted}) {signal_clause} when:1d')
    return queries


def _source_reliability(domain: str, source: str, entity: Mapping[str, Any]) -> float:
    base = event.source_reliability(domain)
    source_text = _norm(source)
    entity_name = _norm(entity.get("name"))
    if entity_name and entity_name in source_text:
        base = max(base, 0.90)
    return base


def fetch_query(query: str, now: datetime, all_entities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    params = urllib.parse.urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    root = ET.fromstring(event._http_text(f"{event.GOOGLE_NEWS}?{params}"))
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published_raw = (item.findtext("pubDate") or "").strip()
        source_node = item.find("source")
        source = ((source_node.text if source_node is not None else "") or "").strip()
        domain = ((source_node.attrib.get("url") if source_node is not None else "") or "").strip()
        try:
            published = parsedate_to_datetime(published_raw).astimezone(event.UTC)
        except Exception:
            continue
        if not title or published < cutoff or published > now + timedelta(minutes=10):
            continue
        entity_row = match_entity(title, all_entities)
        if not entity_row:
            continue
        if not _has_signal(title):
            rejected.append({"title": title[:300], "reason": "entity_mention_without_material_signal", "event_domain": "corporate"})
            continue

        signal = classify_signal(title)
        authority, actor_role, actor_name = actor_authority(title, entity_row)
        direct = float(signal["direct_impact"])
        sector = sector_impact(title, entity_row, direct)
        stable = hashlib.sha256(f"{event.normalize(title)}|{event.normalize(source)}|{published.date().isoformat()}".encode("utf-8")).hexdigest()[:20]
        symbols = [str(symbol).upper() for symbol in entity_row.get("symbols") or []]
        market_index_relevant = entity_row.get("tier") == "mega_cap" and bool(symbols)
        accepted.append({
            "event_id": f"corp-{stable}",
            "event_domain": "crypto" if "crypto_market" in set(entity_row.get("themes") or []) else "technology" if set(entity_row.get("themes") or []) & {"ai_market", "ai_infrastructure", "semiconductor", "cloud", "software"} else "corporate",
            "event_kind": signal["event_kind"],
            "title": title[:360],
            "source": source or domain or "Google News source",
            "source_domain": domain,
            "source_ref": link,
            "published_at": event.iso_z(published),
            "age_hours": round(max(0.0, (now - published).total_seconds() / 3600.0), 3),
            "authority": authority,
            "actor_role": actor_role,
            "actor_name": actor_name,
            "action_strength": float(signal["action_strength"]),
            "action_type": "corporate_material_update" if signal["event_kind"] not in {"strategic_statement", "material_statement"} else "executive_statement",
            "pressure": 0.0,
            "direct_impact": direct,
            "sector_impact": sector,
            "index_impact": round(direct * 0.28, 4) if market_index_relevant else 0.0,
            "severity": float(signal["severity"]),
            "event_type": signal["event_kind"],
            "source_reliability": _source_reliability(domain, source, entity_row),
            "scenario_tags": scenario_tags(entity_row),
            "entity_key": entity_row.get("key"),
            "entity_name": entity_row.get("name"),
            "entity_symbols": symbols,
            "entity_themes": list(entity_row.get("themes") or []),
            "entity_tier": entity_row.get("tier"),
            "market_index_relevant": market_index_relevant,
            "collector": RADAR_VERSION,
        })
    return accepted, rejected


def _corroborate(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        domains = {event.normalize(row.get("source_domain")) or event.normalize(row.get("source"))}
        for other in rows:
            if other is row or row.get("entity_key") != other.get("entity_key"):
                continue
            if abs(float(other.get("age_hours") or 0) - float(row.get("age_hours") or 0)) > 10:
                continue
            if event.similar(row, other):
                domains.add(event.normalize(other.get("source_domain")) or event.normalize(other.get("source")))
        independent = max(1, len({domain for domain in domains if domain}))
        corroboration = 1.0 if independent >= 2 else 0.86
        decay = math.exp(-math.log(2.0) * float(row.get("age_hours") or 0.0) / 10.0)
        confidence = (
            float(row.get("authority") or 0.0)
            * float(row.get("source_reliability") or 0.0)
            * float(row.get("action_strength") or 0.0)
            * corroboration
        )
        row["independent_sources"] = independent
        row["corroboration"] = round(corroboration, 4)
        row["time_decay"] = round(decay, 6)
        row["confidence"] = round(event.clamp(confidence, 0.0, 1.0), 4)
        row["event_strength"] = round(event.clamp(float(row.get("severity") or 0.0) * confidence * decay, 0.0, 1.0), 4)
        row["quality_guard"] = "corporate_materiality_v1"


def collect_events(now: datetime) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    all_entities = entities()
    raw: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    errors: list[str] = []
    for query in _query_groups(all_entities):
        try:
            rows, rejected_rows = fetch_query(query, now, all_entities)
            raw.extend(rows)
            rejected.extend(rejected_rows)
        except Exception as exc:
            errors.append(f"corporate:{type(exc).__name__}:{str(exc)[:120]}")

    by_id = {row["event_id"]: row for row in raw}
    ordered = sorted(by_id.values(), key=lambda row: str(row.get("published_at") or ""), reverse=True)
    _corroborate(ordered)
    ordered.sort(key=lambda row: (float(row.get("event_strength") or 0.0), -float(row.get("age_hours") or 0.0)), reverse=True)
    return ordered[:MAX_EVENTS], errors, rejected[:40]


def public_config() -> dict[str, Any]:
    return {
        "version": RADAR_VERSION,
        "static_entities": len(TRACKED_ENTITIES),
        "dynamic_entity_limit": MAX_DYNAMIC_ENTITIES,
        "lookback_hours": LOOKBACK_HOURS,
        "max_events": MAX_EVENTS,
        "domains": ["corporate", "technology", "crypto"],
    }
