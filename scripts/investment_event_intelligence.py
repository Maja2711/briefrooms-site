#!/usr/bin/env python3
"""Production geopolitical/event risk overlay for BriefRooms investments.

This is not a standalone trading engine. It is an evidence/risk overlay that:
- collects recent leader/state/conflict headlines from multiple news sources,
- normalizes authority, action strength, escalation/de-escalation and freshness,
- maps signed impact to EUR/USD, S&P 500, BTC and equity-sector exposures,
- blocks a newly published stock candidate when a fresh high-confidence event is
  materially adverse,
- requests an immediate governed close for weekly EUR/USD/S&P/BTC positions when
  a high-confidence event invalidates the held direction,
- closes canonical paper stock positions only through stock_trading_portfolio's
  existing close_position lifecycle when an event invalidates the thesis.

Important: event intelligence can veto/defend an entry, HOLD, REDUCE_RISK or CLOSE,
but it never opens a trade by itself. Existing model/risk gates retain entry authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
INVESTMENTS = ROOT / "data" / "investments"
OUTPUT_PATH = INVESTMENTS / "event_intelligence.json"
AUDIT_PATH = INVESTMENTS / "event_intelligence_audit.jsonl"
EVENT_REQUESTS_PATH = INVESTMENTS / "event_exit_requests.json"
STOCK_STATE_PATH = INVESTMENTS / "stock_trading_portfolio.json"
GPW_CANDIDATE_PATH = INVESTMENTS / "gpw_daily_pick.json"
US_CANDIDATE_PATH = INVESTMENTS / "us_daily_stock.json"
WEEKLY_DIR = INVESTMENTS / "weekly"

UTC = timezone.utc
SCHEMA = "investment-event-intelligence-v1"
VERSION = "production-event-overlay-v1"
USER_AGENT = "BriefRooms-Event-Intelligence/1.0 https://briefrooms.com"
LOOKBACK_HOURS = 30
MAX_EVENTS = 24
ENTRY_BLOCK_THRESHOLD = -0.55
REDUCE_RISK_THRESHOLD = -0.35
CLOSE_THRESHOLD = -0.72
MIN_ENTRY_CONFIDENCE = 0.64
MIN_CLOSE_CONFIDENCE = 0.72

GOOGLE_NEWS = "https://news.google.com/rss/search"
QUERIES = (
    '(president OR "prime minister" OR "supreme leader" OR "foreign minister" OR "defense minister") (attack OR strike OR ceasefire OR sanctions OR retaliation OR blockade OR war) when:1d',
    '("White House" OR "Secretary of State" OR Kremlin OR government) (Iran OR Israel OR Russia OR Ukraine OR China OR Taiwan OR sanctions OR tariff) when:1d',
    '(Iran OR Israel OR Hormuz OR "Red Sea") (president OR "supreme leader" OR minister OR military OR ceasefire OR retaliation) when:1d',
    '(Russia OR Ukraine OR China OR Taiwan) (president OR minister OR military OR ceasefire OR sanctions OR blockade) when:1d',
)

HIGH_QUALITY_DOMAINS = (
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "bbc.",
    "cnbc.com", "wsj.com", "nytimes.com", "theguardian.com", "politico.",
)
PRIMARY_DOMAIN_MARKERS = (".gov", ".mil", "europa.eu", "nato.int", "un.org")

AUTHORITY_RULES = (
    (0.98, "head_of_state", ("president", "prime minister", "supreme leader", "chancellor", "premier", "white house", "kremlin")),
    (0.93, "senior_cabinet", ("foreign minister", "defense minister", "finance minister", "secretary of state", "treasury secretary", "national security adviser")),
    (0.88, "state_institution", ("government", "ministry", "nato", "european commission", "central bank", "military chief", "armed forces")),
)

ESCALATION_TERMS: Mapping[str, float] = {
    "attack": 0.80, "attacks": 0.80, "strike": 0.85, "strikes": 0.85,
    "missile": 0.78, "retaliat": 0.82, "invasion": 1.00, "blockade": 0.90,
    "sanction": 0.60, "tariff": 0.45, "threat": 0.45, "warn": 0.32,
    "nuclear": 0.75, "mobiliz": 0.78, "military operation": 0.90,
    "close strait": 0.95, "oil facility": 0.85, "drone": 0.65,
}
DEESCALATION_TERMS: Mapping[str, float] = {
    "ceasefire": 1.00, "truce": 0.95, "peace deal": 1.00, "peace talks": 0.72,
    "negotiat": 0.55, "de-escalat": 0.90, "withdraw": 0.65, "agreement": 0.55,
    "lift sanctions": 0.72, "reopen": 0.45, "diplomatic": 0.40,
}
EXECUTED_TERMS = ("launched", "struck", "attacked", "imposed", "signed", "ordered", "approved", "closed", "blocked", "declared", "entered into force")
ANNOUNCED_TERMS = ("announced", "will impose", "will strike", "plans to", "agreed", "says it will", "to impose")
RHETORIC_TERMS = ("warned", "warns", "threatened", "threatens", "said", "says", "could", "may")

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "says", "said", "will",
    "after", "over", "amid", "into", "about", "their", "would", "could", "news",
}

ASSET_COEFFICIENTS = {
    "eurusd": -0.55,
    "sp500_futures": -0.95,
    "btcusd": -0.48,
}

SECTOR_COEFFICIENTS = (
    (0.65, ("defense", "defence", "aerospace", "zbrojeni", "obron")),
    (0.45, ("energy", "oil", "gas", "energia", "paliwa", "rafiner")),
    (-0.90, ("airline", "aviation", "lotnic", "air transport")),
    (-0.75, ("travel", "tourism", "hotel", "turyst")),
    (-0.62, ("semiconductor", "technology", "software", "tech", "technolog")),
    (-0.52, ("bank", "financial", "finance", "banki", "finans")),
    (-0.48, ("consumer", "retail", "handel", "konsumen")),
)


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(body)
        name = handle.name
    Path(name).replace(path)


def _append_audit(rows: Iterable[Mapping[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    try:
        text = str(value or "").replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").casefold()).strip()


def source_reliability(domain: str) -> float:
    d = normalize(domain)
    if any(marker in d for marker in PRIMARY_DOMAIN_MARKERS):
        return 0.98
    if any(marker in d for marker in HIGH_QUALITY_DOMAINS):
        return 0.92
    if d:
        return 0.72
    return 0.58


def authority(title: str) -> tuple[float, str]:
    text = normalize(title)
    for score, role, tokens in AUTHORITY_RULES:
        if any(token in text for token in tokens):
            return score, role
    return 0.58, "reported_actor"


def action_strength(title: str) -> tuple[float, str]:
    text = normalize(title)
    if any(token in text for token in EXECUTED_TERMS):
        return 1.0, "executed_action"
    if any(token in text for token in ANNOUNCED_TERMS):
        return 0.86, "policy_announcement"
    if any(token in text for token in RHETORIC_TERMS):
        return 0.62, "rhetoric_or_warning"
    return 0.72, "reported_event"


def event_pressure(title: str) -> tuple[float, float, str]:
    text = normalize(title)
    up = max((weight for token, weight in ESCALATION_TERMS.items() if token in text), default=0.0)
    down = max((weight for token, weight in DEESCALATION_TERMS.items() if token in text), default=0.0)
    if up == 0.0 and down == 0.0:
        return 0.0, 0.0, "neutral_or_unclassified"
    if down > up:
        return -1.0, down, "deescalation"
    return 1.0, up, "escalation"


def scenario_tags(title: str) -> list[str]:
    text = normalize(title)
    tags: list[str] = []
    if any(token in text for token in ("iran", "israel", "hormuz", "gaza", "red sea", "houthi")):
        tags.append("middle_east")
    if any(token in text for token in ("russia", "ukraine", "black sea", "kremlin")):
        tags.append("russia_ukraine")
    if any(token in text for token in ("china", "taiwan", "taiwan strait")):
        tags.append("china_taiwan")
    if "sanction" in text or "tariff" in text or "export control" in text:
        tags.append("trade_sanctions")
    if any(token in text for token in ("oil", "gas", "hormuz", "tanker", "pipeline")):
        tags.append("energy_supply")
    return tags or ["general_geopolitics"]


def title_tokens(title: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]{4,}", normalize(title))
        if token not in STOPWORDS
    }


def similar(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    ta, tb = title_tokens(str(a.get("title") or "")), title_tokens(str(b.get("title") or ""))
    if not ta or not tb:
        return False
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    return jaccard >= 0.46


def _http_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml,application/xml,text/xml"})
    with urllib.request.urlopen(request, timeout=18) as response:
        return response.read().decode("utf-8", "replace")


def fetch_query(query: str, now: datetime) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    root = ET.fromstring(_http_text(f"{GOOGLE_NEWS}?{params}"))
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    rows: list[dict[str, Any]] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published_raw = (item.findtext("pubDate") or "").strip()
        source_node = item.find("source")
        source = ((source_node.text if source_node is not None else "") or "").strip()
        domain = ((source_node.attrib.get("url") if source_node is not None else "") or "").strip()
        try:
            published = parsedate_to_datetime(published_raw).astimezone(UTC)
        except Exception:
            continue
        if not title or published < cutoff or published > now + timedelta(minutes=10):
            continue
        pressure, severity, event_type = event_pressure(title)
        auth, role = authority(title)
        strength, action_type = action_strength(title)
        if pressure == 0.0:
            continue
        stable = hashlib.sha256(f"{normalize(title)}|{normalize(source)}|{published.date().isoformat()}".encode("utf-8")).hexdigest()[:20]
        rows.append({
            "event_id": f"evt-{stable}",
            "title": title[:360],
            "source": source or domain or "Google News source",
            "source_domain": domain,
            "source_ref": link,
            "published_at": iso_z(published),
            "age_hours": round(max(0.0, (now - published).total_seconds() / 3600.0), 3),
            "authority": auth,
            "actor_role": role,
            "action_strength": strength,
            "action_type": action_type,
            "pressure": pressure,
            "severity": severity,
            "event_type": event_type,
            "source_reliability": source_reliability(domain),
            "scenario_tags": scenario_tags(title),
        })
    return rows


def collect_events(now: datetime) -> tuple[list[dict[str, Any]], list[str]]:
    raw: list[dict[str, Any]] = []
    errors: list[str] = []
    for query in QUERIES:
        try:
            raw.extend(fetch_query(query, now))
        except Exception as exc:
            errors.append(f"{type(exc).__name__}:{str(exc)[:120]}")
    by_id = {row["event_id"]: row for row in raw}
    ordered = sorted(by_id.values(), key=lambda row: str(row.get("published_at") or ""), reverse=True)

    # Corroboration is deliberately conservative: nearby paraphrases from independent
    # publishers strengthen confidence, repeated syndication does not create new alpha.
    for row in ordered:
        domains = {normalize(row.get("source_domain")) or normalize(row.get("source"))}
        for other in ordered:
            if other is row or abs(float(other.get("age_hours") or 0) - float(row.get("age_hours") or 0)) > 10:
                continue
            if similar(row, other):
                domains.add(normalize(other.get("source_domain")) or normalize(other.get("source")))
        independent = max(1, len({d for d in domains if d}))
        corroboration = 1.0 if independent >= 2 else 0.84
        decay = math.exp(-math.log(2.0) * float(row.get("age_hours") or 0.0) / 8.0)
        confidence = (
            float(row["authority"])
            * float(row["source_reliability"])
            * float(row["action_strength"])
            * corroboration
        )
        row["independent_sources"] = independent
        row["corroboration"] = round(corroboration, 4)
        row["time_decay"] = round(decay, 6)
        row["confidence"] = round(clamp(confidence, 0.0, 1.0), 4)
        row["event_strength"] = round(clamp(float(row["severity"]) * confidence * decay, 0.0, 1.0), 4)
    ordered.sort(key=lambda row: (float(row.get("event_strength") or 0.0), -float(row.get("age_hours") or 0.0)), reverse=True)
    return ordered[:MAX_EVENTS], errors


def stock_coefficient(sector: str | None) -> float:
    text = normalize(sector)
    for coefficient, tokens in SECTOR_COEFFICIENTS:
        if any(token in text for token in tokens):
            return coefficient
    return -0.45


def target_coefficient(target: Mapping[str, Any]) -> float:
    target_id = str(target.get("target_id") or "")
    if target_id in ASSET_COEFFICIENTS:
        return ASSET_COEFFICIENTS[target_id]
    return stock_coefficient(str(target.get("sector") or ""))


def score_target(target: Mapping[str, Any], events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    coefficient = target_coefficient(target)
    contributions: list[dict[str, Any]] = []
    for row in events:
        strength = float(row.get("event_strength") or 0.0)
        pressure = float(row.get("pressure") or 0.0)
        signed = pressure * coefficient * strength
        if abs(signed) < 0.035:
            continue
        contributions.append({
            "event_id": row.get("event_id"),
            "title": row.get("title"),
            "published_at": row.get("published_at"),
            "source": row.get("source"),
            "source_ref": row.get("source_ref"),
            "confidence": row.get("confidence"),
            "action_type": row.get("action_type"),
            "event_type": row.get("event_type"),
            "scenario_tags": row.get("scenario_tags"),
            "signed_impact": round(signed, 4),
        })
    contributions.sort(key=lambda row: abs(float(row.get("signed_impact") or 0.0)), reverse=True)
    # Avoid headline-count inflation. The strongest event dominates; two additional
    # independent signals can add bounded confirmation.
    top = contributions[:3]
    aggregate = 0.0
    for idx, row in enumerate(top):
        aggregate += float(row["signed_impact"]) * (1.0 if idx == 0 else 0.45 if idx == 1 else 0.25)
    aggregate = clamp(aggregate)
    confidence = max((float(row.get("confidence") or 0.0) for row in top), default=0.0)
    action = "HOLD"
    if aggregate <= CLOSE_THRESHOLD and confidence >= MIN_CLOSE_CONFIDENCE:
        action = "CLOSE"
    elif aggregate <= REDUCE_RISK_THRESHOLD:
        action = "REDUCE_RISK"
    elif aggregate >= 0.35:
        action = "SUPPORTIVE"
    return {
        "target_id": target.get("target_id"),
        "symbol": target.get("symbol"),
        "market": target.get("market"),
        "sector": target.get("sector"),
        "exposure_coefficient": coefficient,
        "normalized_impact": round(aggregate, 4),
        "score_delta": round(aggregate * 20.0, 2),
        "confidence": round(confidence, 4),
        "decision_overlay": action,
        "entry_blocked": aggregate <= ENTRY_BLOCK_THRESHOLD and confidence >= MIN_ENTRY_CONFIDENCE,
        "top_events": top,
    }


def current_week_path(now: datetime) -> Path | None:
    candidates = sorted(WEEKLY_DIR.glob("*.json"))
    if not candidates:
        return None
    # Prefer a file whose explicit window contains now; fallback to newest file.
    for path in reversed(candidates[-8:]):
        data = _load(path, {})
        start = parse_time((data.get("market_window") or {}).get("entry_target_local"))
        end = parse_time((data.get("market_window") or {}).get("exit_target_local"))
        if start and end and start - timedelta(days=3) <= now <= end + timedelta(days=8):
            return path
    return candidates[-1]


def build_targets(now: datetime) -> tuple[list[dict[str, Any]], dict[str, Any], Path | None, dict[str, Any]]:
    targets: list[dict[str, Any]] = [
        {"target_id": "eurusd", "symbol": "EURUSD=X", "market": "MULTI_ASSET", "sector": "fx"},
        {"target_id": "sp500_futures", "symbol": "ES=F", "market": "MULTI_ASSET", "sector": "equity index"},
        {"target_id": "btcusd", "symbol": "BTC-USD", "market": "MULTI_ASSET", "sector": "crypto"},
    ]
    stock_state = _load(STOCK_STATE_PATH, {})
    for market, market_row in (stock_state.get("markets") or {}).items():
        for position in (market_row or {}).get("open_positions") or []:
            if str(position.get("status") or "OPEN").upper() != "OPEN":
                continue
            targets.append({
                "target_id": f"stock:{market}:{position.get('symbol')}",
                "symbol": position.get("symbol"),
                "market": market,
                "sector": position.get("sector"),
                "position_id": position.get("position_id"),
            })
    for market, path in (("GPW", GPW_CANDIDATE_PATH), ("US", US_CANDIDATE_PATH)):
        payload = _load(path, {})
        selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
        if selection and str(payload.get("decision") or "") in {"TRANSAKCJA", "TRADE"}:
            targets.append({
                "target_id": f"candidate:{market}:{selection.get('symbol') or selection.get('ticker')}",
                "symbol": selection.get("symbol") or selection.get("ticker"),
                "market": market,
                "sector": selection.get("sector"),
                "candidate_payload": payload,
            })
    week_path = current_week_path(now)
    week = _load(week_path, {}) if week_path else {}
    return targets, stock_state, week_path, week


def _week_position(week: Mapping[str, Any], instrument_id: str) -> Mapping[str, Any] | None:
    for row in week.get("instruments") or []:
        if str(row.get("instrument_id") or "") == instrument_id:
            return row
    return None


def apply_weekly_requests(now: datetime, week: Mapping[str, Any], scores: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not week:
        return []
    data = _load(EVENT_REQUESTS_PATH, {"requests": []})
    if not isinstance(data, dict):
        data = {"requests": []}
    requests = data.setdefault("requests", [])
    existing = {
        (str(row.get("week_id") or ""), str(row.get("instrument_id") or ""), str((row.get("event") or {}).get("event_id") or ""))
        for row in requests if isinstance(row, dict) and str(row.get("status") or "") == "pending"
    }
    added: list[dict[str, Any]] = []
    week_id = str(week.get("week_id") or "")
    for instrument_id in ("eurusd", "sp500_futures", "btcusd"):
        item = _week_position(week, instrument_id)
        score = scores.get(instrument_id) or {}
        if not item or score.get("decision_overlay") != "CLOSE":
            continue
        side = str(item.get("direction") or "neutral")
        if side not in {"long", "short"} or item.get("entry_price") is None or item.get("exit_price") is not None:
            continue
        impact = float(score.get("normalized_impact") or 0.0)
        adverse = (side == "long" and impact < 0) or (side == "short" and impact > 0)
        if not adverse:
            continue
        top = (score.get("top_events") or [{}])[0]
        event_id = str(top.get("event_id") or "aggregate")
        key = (week_id, instrument_id, event_id)
        if key in existing:
            continue
        request = {
            "request_id": f"event-intel:{week_id}:{instrument_id}:{event_id}",
            "created_at": iso_z(now),
            "status": "pending",
            "action": "close_only",
            "week_id": week_id,
            "instrument_id": instrument_id,
            "reason": "high_confidence_geopolitical_event_thesis_invalidation",
            "event": {
                "event_id": event_id,
                "title": top.get("title"),
                "source": top.get("source"),
                "source_ref": top.get("source_ref"),
                "published_at": top.get("published_at"),
                "normalized_impact": impact,
                "score_delta": score.get("score_delta"),
                "confidence": score.get("confidence"),
            },
            "policy": {
                "engine": VERSION,
                "close_threshold": CLOSE_THRESHOLD,
                "minimum_confidence": MIN_CLOSE_CONFIDENCE,
                "market_confirmation_required": False,
                "entry_authority": False,
            },
        }
        requests.append(request)
        existing.add(key)
        added.append(request)
    if added:
        data["updated_at"] = iso_z(now)
        data["producer"] = VERSION
        _write(EVENT_REQUESTS_PATH, data)
    return added


def apply_candidate_blocks(stock_state: dict[str, Any], targets: Iterable[Mapping[str, Any]], scores: Mapping[str, Mapping[str, Any]], now: datetime) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    try:
        import stock_trading_portfolio as stock
    except Exception:
        return actions
    for target in targets:
        target_id = str(target.get("target_id") or "")
        if not target_id.startswith("candidate:"):
            continue
        score = scores.get(target_id) or {}
        if not score.get("entry_blocked"):
            continue
        market = str(target.get("market") or "").upper()
        payload = target.get("candidate_payload") if isinstance(target.get("candidate_payload"), dict) else {}
        row = ((stock_state.get("markets") or {}).get(market) or {})
        key = stock.candidate_key(payload)
        if not key:
            continue
        symbol = str(target.get("symbol") or "")
        if any(str(pos.get("symbol") or "") == symbol for pos in row.get("open_positions") or []):
            continue
        row["last_candidate_key"] = key
        row["last_candidate_decision"] = "CASH"
        row["last_candidate_reason"] = "event_intelligence_entry_block"
        row["last_candidate_event_overlay"] = {
            "blocked_at": iso_z(now),
            "normalized_impact": score.get("normalized_impact"),
            "score_delta": score.get("score_delta"),
            "confidence": score.get("confidence"),
            "top_events": score.get("top_events"),
        }
        actions.append({"action": "BLOCK_OPEN", "market": market, "symbol": symbol, "candidate_key": key, "overlay": score})
    return actions


def yahoo_last_price(symbol: str) -> float | None:
    if not symbol:
        return None
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=1d&interval=5m"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        result = ((payload.get("chart") or {}).get("result") or [None])[0] or {}
        closes = ((((result.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or [])
        values = [float(value) for value in closes if value is not None and math.isfinite(float(value))]
        return values[-1] if values else None
    except Exception:
        return None


def apply_stock_closes(stock_state: dict[str, Any], scores: Mapping[str, Mapping[str, Any]], now: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    actions: list[dict[str, Any]] = []
    try:
        import stock_trading_portfolio as stock
    except Exception:
        return stock_state, actions
    state = stock_state
    for market in ("GPW", "US"):
        positions = list((((state.get("markets") or {}).get(market) or {}).get("open_positions") or []))
        for position in positions:
            target_id = f"stock:{market}:{position.get('symbol')}"
            score = scores.get(target_id) or {}
            overlay = str(score.get("decision_overlay") or "HOLD")
            # Persist a compact event state even when no close is required.
            position["event_intelligence"] = {
                "reviewed_at": iso_z(now),
                "decision_overlay": overlay,
                "normalized_impact": score.get("normalized_impact"),
                "score_delta": score.get("score_delta"),
                "confidence": score.get("confidence"),
                "top_events": score.get("top_events"),
            }
            if overlay != "CLOSE":
                if overlay == "REDUCE_RISK":
                    actions.append({"action": "REDUCE_RISK", "market": market, "symbol": position.get("symbol"), "executed": False, "reason": "partial_position_sizing_not_supported"})
                continue
            price = yahoo_last_price(str(position.get("symbol") or ""))
            if price is None:
                try:
                    price = float(position.get("last_mark"))
                except (TypeError, ValueError):
                    price = None
            if price is None or not math.isfinite(price) or price <= 0:
                actions.append({"action": "DEFER_CLOSE", "market": market, "symbol": position.get("symbol"), "reason": "event_close_price_unavailable"})
                continue
            state, result = stock.close_position(
                state,
                market,
                str(position.get("position_id") or ""),
                now=now,
                exit_price=price,
                reason="event_intelligence_high_confidence_thesis_invalidation",
            )
            actions.append({"action": "CLOSE", "market": market, "symbol": position.get("symbol"), "price": price, "result": result, "overlay": score})
    return state, actions


def run(*, apply: bool = False, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    events, errors = collect_events(now)
    targets, stock_state, week_path, week = build_targets(now)
    scores = {str(target["target_id"]): score_target(target, events) for target in targets}
    status = "healthy" if events else "degraded_no_fresh_classified_events"
    actions: list[dict[str, Any]] = []
    weekly_requests: list[dict[str, Any]] = []

    if apply and events:
        block_actions = apply_candidate_blocks(stock_state, targets, scores, now)
        stock_state, stock_actions = apply_stock_closes(stock_state, scores, now)
        actions.extend(block_actions)
        actions.extend(stock_actions)
        if block_actions or stock_actions:
            stock_state["updated_at"] = now.isoformat(timespec="seconds")
            _write(STOCK_STATE_PATH, stock_state)
        weekly_requests = apply_weekly_requests(now, week, scores)
        actions.extend({"action": "REQUEST_CLOSE", "instrument_id": row.get("instrument_id"), "request_id": row.get("request_id")} for row in weekly_requests)

    payload = {
        "schema_version": SCHEMA,
        "engine_version": VERSION,
        "mode": "production_decision_overlay",
        "generated_at": iso_z(now),
        "status": status,
        "source_errors": errors,
        "controls": {
            "standalone_entry_authority": False,
            "existing_model_entry_authority_preserved": True,
            "entry_veto_enabled": True,
            "close_override_enabled": True,
            "partial_reduce_execution_enabled": False,
            "market_confirmation_is_input_not_mandatory_gate": True,
            "fail_closed_on_missing_event_feed": False,
            "no_event_data_means_no_overlay_change": True,
        },
        "thresholds": {
            "entry_block": ENTRY_BLOCK_THRESHOLD,
            "reduce_risk": REDUCE_RISK_THRESHOLD,
            "close": CLOSE_THRESHOLD,
            "minimum_entry_confidence": MIN_ENTRY_CONFIDENCE,
            "minimum_close_confidence": MIN_CLOSE_CONFIDENCE,
        },
        "events": events,
        "targets": list(scores.values()),
        "actions": actions,
        "weekly_state_path": str(week_path.relative_to(ROOT)) if week_path else None,
    }
    _write(OUTPUT_PATH, payload)
    if apply and actions:
        _append_audit({"recorded_at": iso_z(now), **row} for row in actions)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply production entry vetoes and governed close requests")
    args = parser.parse_args()
    payload = run(apply=args.apply)
    print(json.dumps({"status": payload["status"], "events": len(payload["events"]), "actions": len(payload["actions"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
