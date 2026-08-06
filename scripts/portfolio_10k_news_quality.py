#!/usr/bin/env python3
"""Rank and refresh portfolio headlines for the public 10K analytics view.

The methodology separates two jobs:
- ``recent_news`` is a ranked context feed of fresh, entity-specific reporting;
- ``material_candidate`` marks only source-backed factual events that may feed BRACE.

Opinion pieces, generic ETF comparisons and syndicated duplicates are excluded.
Missing news is never interpreted as a positive signal.
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping
from urllib.parse import urlparse

import pandas as pd

import portfolio_10k_weekly as base

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "investments" / "portfolio_10k.json"
METHODOLOGY_VERSION = "analysis-news-v2"
DEFAULT_LIMIT = 5
DEFAULT_MAX_AGE_DAYS = 14

ENTITY_TERMS: dict[str, tuple[str, ...]] = {
    "fwia": ("invesco ftse all-world", "ftse all-world ucits", "fwia.de", "fwia"),
    "zprv": ("spdr msci usa small cap value", "zprv.de", "zprv"),
    "googl": ("alphabet", "google", "youtube", "gemini", "waymo"),
    "amzn": ("amazon", "amazon.com", "aws", "prime video"),
    "tsm": ("tsmc", "taiwan semiconductor"),
    "visa": ("visa inc", "visa earnings", "visa stock", "visa shares", "visa payments", "visa antitrust"),
    "spgi": ("s&p global", "spgi", "s&p ratings", "standard & poor's"),
    "novo": ("novo nordisk", "wegovy", "ozempic", "cagrisema", "novob.dk"),
}
STRICT_ENTITY_IDS = {"fwia", "zprv"}

PRIMARY_SOURCE_RE = re.compile(
    r"\b(sec|fda|ftc|doj|justice department|european commission|company filing|"
    r"investor relations|press release|nasdaq|nyse|london stock exchange)\b",
    re.I,
)
TOP_SOURCE_RE = re.compile(
    r"\b(reuters|bloomberg|financial times|wall street journal|associated press|"
    r"ap news|cnbc|bbc|the guardian|nikkei|dow jones)\b",
    re.I,
)
SOLID_SOURCE_RE = re.compile(
    r"\b(morningstar|marketwatch|barron'?s|business insider|techcrunch|the verge|"
    r"seeking alpha|yahoo finance|investing\.com|fortune)\b",
    re.I,
)
LOW_SOURCE_RE = re.compile(
    r"\b(aol|24/7 wall st|motley fool|benzinga|tipranks|investorplace|"
    r"simply wall st|stockstory|zacks)\b",
    re.I,
)

OPINION_RE = re.compile(
    r"\b("
    r"is .{0,45} (a )?(buy|sell|hold)|should you buy|better buy|best stock|"
    r"fairly valued|price prediction|where will .{0,35} be in|"
    r"billionaire .{0,50} bought|here'?s why|why i (bought|sold)|"
    r"top \d+ stocks?|stocks? to buy|outshines|could (soar|double|rally)|"
    r"what investors should know|worth buying"
    r")\b",
    re.I,
)
GENERIC_BAD_PREFIXES = ("etfs investing in ", "funds holding ")

EVENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GUIDANCE", re.compile(r"\b(raises?|cuts?|lowers?|withdraws?|reaffirms?) (?:its )?(guidance|outlook|forecast)\b", re.I)),
    ("EARNINGS", re.compile(r"\b(quarterly results?|annual results?|reports? (?:q[1-4]|quarter)|earnings (?:beat|miss)|revenue (?:rose|fell|grew|declined)|profit (?:rose|fell|grew|declined))\b", re.I)),
    ("ANALYST_CHANGE", re.compile(r"\b(upgrade[sd]?|downgrade[sd]?|price target (?:raised|cut|lowered)|rating (?:raised|cut|lowered))\b", re.I)),
    ("REGULATORY", re.compile(r"\b(antitrust|regulator|regulatory|investigation|probe|fine[sd]?|lawsuit|court ruling|approval|approved|blocked|ban(?:ned)?)\b", re.I)),
    ("OPERATIONS", re.compile(r"\b(phase [123]|clinical trial|endpoint|recall|cyberattack|data breach|outage|production halt|supply disruption|factory shutdown)\b", re.I)),
    ("PRODUCT", re.compile(r"\b(launch(?:es|ed)?|unveil(?:s|ed)?|product approval|drug approval|new service|new chip)\b", re.I)),
    ("DIVIDEND", re.compile(r"\b(dividend (?:increase|cut|declared|suspended)|special dividend|special distribution)\b", re.I)),
    ("BUYBACK", re.compile(r"\b(buyback|share repurchase)\b", re.I)),
)

NEGATIVE_RE = re.compile(
    r"\b(cut|cuts|lower|miss|misses|failed?|falls?|fell|decline[sd]?|"
    r"investigation|probe|fine|lawsuit|downgrade[sd]?|recall|pressure|"
    r"slowdown|weak|slump|drop|blocked|ban(?:ned)?|breach|outage)\b",
    re.I,
)
POSITIVE_RE = re.compile(
    r"\b(beat|beats|raise[sd]?|approval|approved|record|buyback|partnership|"
    r"growth|jump|surge|upgrade[sd]?|expansion|strong|launch(?:es|ed)?)\b",
    re.I,
)
WORD_RE = re.compile(r"[a-z0-9]+", re.I)


def _published(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _https(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    return parsed.scheme.lower() == "https" and bool(parsed.netloc)


def headline_key(title: str) -> str:
    """Normalize a title and remove a final publisher suffix."""
    base_title = re.sub(r"\s+-\s+[^-]+$", "", str(title or "").strip())
    return re.sub(r"[^a-z0-9]+", " ", base_title.lower()).strip()


def _tokens(title: str) -> set[str]:
    return {token.lower() for token in WORD_RE.findall(headline_key(title)) if len(token) > 2}


def _near_duplicate(left: str, right: str) -> bool:
    if headline_key(left) == headline_key(right):
        return True
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.72


def entity_strength(position_id: str, title: str) -> int:
    lower = str(title or "").lower().strip()
    if lower.startswith(GENERIC_BAD_PREFIXES):
        return 0
    terms = ENTITY_TERMS.get(position_id, ())
    matches = [term for term in terms if term in lower]
    if not matches:
        return 0 if terms else 1
    if position_id in STRICT_ENTITY_IDS:
        return 3
    strongest = max(matches, key=len)
    return 3 if len(strongest) >= 6 else 2


def relevant(position_id: str, title: str) -> bool:
    return entity_strength(position_id, title) > 0


def source_tier(source: str, link: Any = "") -> int:
    combined = f"{source} {urlparse(str(link or '')).netloc}".lower()
    if PRIMARY_SOURCE_RE.search(combined):
        return 4
    if TOP_SOURCE_RE.search(combined):
        return 3
    if SOLID_SOURCE_RE.search(combined):
        return 2
    if LOW_SOURCE_RE.search(combined):
        return 1
    return 2 if str(source or "").strip() else 1


def classify_event(title: str) -> str | None:
    return next((name for name, pattern in EVENT_PATTERNS if pattern.search(title)), None)


def impact_for(title: str, item: Mapping[str, Any] | None = None) -> str:
    item = item or {}
    if item.get("risk_keywords") or NEGATIVE_RE.search(title):
        return "NEGATIVE"
    if item.get("positive_keywords") or POSITIVE_RE.search(title):
        return "POSITIVE"
    return "NEUTRAL"


def annotate_item(
    position_id: str,
    item: Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> Dict[str, Any] | None:
    title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
    link = item.get("link")
    source = str(item.get("source") or "").strip()
    if not title or not _https(link):
        return None

    strength = entity_strength(position_id, title)
    if strength <= 0:
        return None

    published = _published(item.get("published_at") or item.get("published"))
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_days: float | None = None
    if published is not None:
        age_days = max(0.0, (now - published).total_seconds() / 86400.0)
        if age_days > max_age_days:
            return None

    tier = source_tier(source, link)
    event_type = classify_event(title)
    opinion = bool(OPINION_RE.search(title))
    impact = impact_for(title, item)

    material = bool(
        event_type
        and not opinion
        and strength >= 2
        and tier >= 2
        and (event_type != "ANALYST_CHANGE" or tier >= 3)
        and published is not None
        and (age_days is None or age_days <= 10)
    )

    recency_points = 0
    if age_days is not None:
        recency_points = max(0, 18 - int(age_days * 2))
    quality_score = (
        tier * 14
        + strength * 10
        + recency_points
        + (18 if event_type else 0)
        + (8 if material else 0)
        - (55 if opinion else 0)
        - (8 if published is None else 0)
    )
    if opinion and tier <= 2:
        return None
    if quality_score < 38:
        return None

    result = dict(item)
    result.update(
        {
            "title": title,
            "published_at": published.isoformat().replace("+00:00", "Z") if published else None,
            "source_tier": tier,
            "entity_match_strength": strength,
            "event_type": event_type,
            "impact": impact,
            "material_candidate": material,
            "quality_score": int(quality_score),
            "information_role": "material_candidate" if material else "context",
            "methodology_version": METHODOLOGY_VERSION,
        }
    )
    return result


def clean_news(
    position_id: str,
    items: Iterable[Dict[str, Any]],
    limit: int = DEFAULT_LIMIT,
    *,
    now: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> List[Dict[str, Any]]:
    ranked: List[Dict[str, Any]] = []
    for item in items:
        annotated = annotate_item(position_id, item, now=now, max_age_days=max_age_days)
        if annotated is not None:
            ranked.append(annotated)

    ranked.sort(
        key=lambda item: (
            int(bool(item.get("material_candidate"))),
            int(item.get("quality_score") or 0),
            str(item.get("published_at") or ""),
        ),
        reverse=True,
    )

    cleaned: List[Dict[str, Any]] = []
    for item in ranked:
        if any(_near_duplicate(str(item.get("title")), str(existing.get("title"))) for existing in cleaned):
            continue
        cleaned.append(item)
        if len(cleaned) >= limit:
            break
    return cleaned


def market_record(position: Dict[str, Any]) -> base.MarketRecord:
    return base.MarketRecord(
        symbol=str(position.get("market_symbol") or position.get("broker_symbol") or ""),
        price=base.finite(position.get("current_price")) or 0.0,
        market_date=str(position.get("market_date") or ""),
        currency=str(position.get("currency") or ""),
        ma50=base.finite(position.get("ma50")),
        ma200=base.finite(position.get("ma200")),
        return_6m=base.finite(position.get("return_6m")),
        drawdown_52w=base.finite(position.get("drawdown_52w")),
        volatility_20d=base.finite(position.get("volatility_20d")),
        history=pd.DataFrame(),
        next_earnings_date=position.get("next_earnings_date"),
    )


def refresh_position(
    position: Dict[str, Any],
    incoming: Iterable[Dict[str, Any]] | None = None,
    *,
    limit: int = DEFAULT_LIMIT,
) -> None:
    items = clean_news(
        str(position.get("id") or ""),
        incoming if incoming is not None else position.get("recent_news") or [],
        limit=limit,
    )
    score, positives, risks = base.technical_score(market_record(position), items)
    negative_material = sum(
        1
        for item in items
        if item.get("material_candidate") and item.get("impact") == "NEGATIVE"
    )
    weight = base.finite(position.get("current_weight"))
    position["recent_news"] = items
    position["model_score"] = score
    position["positive_signals"] = positives
    position["risk_signals"] = risks
    position["review_flag"] = base.review_flag(position, score, weight, negative_material)


def update_latest_history(data: Dict[str, Any]) -> None:
    positions = data.get("positions", [])
    by_id = {p.get("id"): p for p in positions}
    if data.get("snapshots"):
        snapshot = data["snapshots"][-1]
        for position_id, entry in (snapshot.get("positions") or {}).items():
            position = by_id.get(position_id)
            if position:
                entry["review_flag"] = position.get("review_flag")
    if data.get("weekly_reviews"):
        review = data["weekly_reviews"][-1]
        summary = {
            "total_value_pln": data.get("total_value_pln"),
            "cash_pln": data.get("cash_pln"),
            "dividends_pln": data.get("dividends_pln"),
            "total_return_pln": data.get("total_return_pln"),
            "total_return_percent": data.get("total_return_percent"),
            "benchmark_value_pln": data.get("benchmark_value_pln"),
            "benchmark_return_percent": data.get("benchmark_return_percent"),
        }
        review["portfolio"] = summary
        review["summary_pl"] = base.weekly_summary_text(data, summary, "pl")
        review["summary_en"] = base.weekly_summary_text(data, summary, "en")
        review["position_flags"] = [
            {
                "id": p.get("id"),
                "broker_symbol": p.get("broker_symbol"),
                "flag": p.get("review_flag"),
                "model_score": p.get("model_score"),
                "current_weight": p.get("current_weight"),
                "target_weight": p.get("target_weight"),
                "next_earnings_date": p.get("next_earnings_date"),
                "risk_signals": p.get("risk_signals", []),
            }
            for p in positions
        ]


def _refresh_due(data: Mapping[str, Any], max_age_hours: float, force: bool) -> bool:
    if force:
        return True
    last = _published(data.get("analysis_news_refreshed_at"))
    if last is None:
        return True
    age_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600.0
    return age_hours >= max_age_hours


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Fetch a fresh source-headline pool.")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--max-age-hours", type=float, default=4.0)
    args = parser.parse_args()

    data = base.load_json(DATA_PATH)
    perform_refresh = args.refresh and _refresh_due(data, args.max_age_hours, args.force_refresh)
    refreshed_any = False

    for position in data.get("positions", []):
        if position.get("status") not in {None, "active"}:
            continue
        incoming = None
        if perform_refresh:
            query = str(position.get("news_query") or position.get("label") or "")
            fetched = base.rss_news(query, 24)
            if fetched:
                incoming = fetched
                refreshed_any = True
        refresh_position(position, incoming, limit=max(1, min(args.limit, 8)))

    update_latest_history(data)
    if perform_refresh:
        data["analysis_news_refreshed_at"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        data["analysis_news_refresh_status"] = "updated" if refreshed_any else "preserved_last_verified"

    data["analysis_news_methodology"] = {
        "version": METHODOLOGY_VERSION,
        "recent_information": {
            "freshness_days": DEFAULT_MAX_AGE_DAYS,
            "ranking": ["material event", "source quality", "entity match", "recency"],
            "excludes": ["opinion/listicle headlines", "generic ETF comparisons", "near duplicates"],
        },
        "material_reports": {
            "requires": [
                "specific factual event",
                "HTTPS source and parseable timestamp",
                "strong entity match",
                "source tier >= 2",
            ],
            "analyst_change_requires_source_tier": 3,
        },
    }
    data["news_quality_note_pl"] = (
        "Najnowsze informacje są oceniane według świeżości, jakości źródła, "
        "zgodności z instrumentem i znaczenia dla tezy. Opinie typu „czy kupić”, "
        "ogólne porównania ETF-ów i duplikaty są odrzucane."
    )
    data["news_quality_note_en"] = (
        "Recent information is ranked by freshness, source quality, entity match "
        "and thesis relevance. Buy/sell opinion pieces, generic ETF comparisons "
        "and syndicated duplicates are excluded."
    )
    base.write_json_atomic(DATA_PATH, data)
    print(
        f"Portfolio analytics news methodology {METHODOLOGY_VERSION} applied; "
        f"network_refresh={perform_refresh}, refreshed_any={refreshed_any}"
    )


if __name__ == "__main__":
    main()
