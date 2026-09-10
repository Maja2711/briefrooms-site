#!/usr/bin/env python3
"""ECB policy-event guard for the Daily EUR/USD engine.

Uses only verifiable ECB evidence plus observed EUR/USD price action. The
module never invents market consensus, OIS repricing or a guidance score.
Directional convention: +1 is EUR/USD bullish; -1 is EUR/USD bearish.
"""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

ECB_MEETING_CALENDAR_URL = "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"
ECB_PRESS_CONFERENCE_URL = "https://www.ecb.europa.eu/press/press_conference/html/index.en.html"
ECB_PRESS_RSS_URL = "https://www.ecb.europa.eu/rss/press.html"
USER_AGENT = "BriefRooms-EURUSD-Policy/1.0 (+https://briefrooms.com)"

BERLIN = ZoneInfo("Europe/Berlin")
PRE_EVENT_MINUTES = 60
POST_EVENT_HOURS = 18
ECB_DECISION_LOCAL_TIME = time(14, 15)
POLICY_VETO_THRESHOLD = 0.35
FX_REACTION_NORMALIZER = 0.0025

# Official ECB monetary-policy meeting Day-2 / press-conference dates. The
# online conference page remains a fallback for dates outside the bundled
# schedule. Keeping a local schedule makes event-day safety fail closed even
# if the ECB website is temporarily unavailable.
ECB_POLICY_DATES = frozenset({
    date(2026, 2, 5), date(2026, 3, 19), date(2026, 4, 30), date(2026, 6, 11),
    date(2026, 7, 23), date(2026, 9, 10), date(2026, 10, 29), date(2026, 12, 17),
    date(2027, 2, 4), date(2027, 3, 18), date(2027, 4, 29), date(2027, 6, 10),
    date(2027, 7, 22), date(2027, 9, 9), date(2027, 10, 28), date(2027, 12, 16),
})
KNOWN_SCHEDULE_YEARS = frozenset({2026, 2027})

Fetcher = Callable[[str], str]


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _fetch_text(url: str, timeout: int = 12) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _visible_text(raw_html: str) -> str:
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", raw_html)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _english_date(day: date) -> str:
    return f"{day.day} {day.strftime('%B %Y')}"


def _conference_page_confirms(day: date, fetcher: Fetcher) -> bool:
    try:
        text = _visible_text(fetcher(ECB_PRESS_CONFERENCE_URL))
    except Exception:
        return False
    return _english_date(day) in text and "Monetary policy decisions" in text


def _event_datetime(day: date) -> datetime:
    return datetime.combine(day, ECB_DECISION_LOCAL_TIME, tzinfo=BERLIN)


def _scheduled_event_for(observed_at: datetime, fetcher: Fetcher) -> tuple[datetime | None, str]:
    local = observed_at.astimezone(BERLIN)
    for day in (local.date(), local.date() - timedelta(days=1)):
        decision = _event_datetime(day)
        in_window = decision - timedelta(minutes=PRE_EVENT_MINUTES) <= local <= decision + timedelta(hours=POST_EVENT_HOURS)
        if not in_window:
            continue
        if day in ECB_POLICY_DATES:
            return decision, "official_calendar_fallback"
        if day.year in KNOWN_SCHEDULE_YEARS:
            continue
        if day == local.date() and _conference_page_confirms(day, fetcher):
            return decision, "official_conference_page"
    return None, "no_active_event"


def _rss_items(xml_text: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    rows: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_raw = (item.findtext("pubDate") or "").strip()
        published: datetime | None = None
        if pub_raw:
            try:
                published = parsedate_to_datetime(pub_raw)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                published = None
        rows.append({"title": title, "link": link, "published": published})
    return rows


def _decision_link_for_day(day: date, fetcher: Fetcher) -> str | None:
    try:
        rows = _rss_items(fetcher(ECB_PRESS_RSS_URL))
    except Exception:
        return None
    for row in rows:
        if "monetary policy decisions" not in str(row.get("title") or "").lower():
            continue
        published = row.get("published")
        if isinstance(published, datetime) and published.astimezone(BERLIN).date() == day:
            link = str(row.get("link") or "").strip()
            return urllib.parse.urljoin(ECB_PRESS_RSS_URL, link) if link else None
    return None


def parse_ecb_decision(raw_html: str) -> dict[str, Any]:
    """Parse only explicit rate-action language from an ECB decision page."""
    text = _visible_text(raw_html)
    lowered = text.lower()
    action: str | None = None
    action_score: float | None = None

    # Anchor the action to the Governing Council's explicit decision wording;
    # words such as "inflation increased" elsewhere must never look like a hike.
    action_match = re.search(
        r"decided(?:\s+today)?\s+to\s+(raise|increase|lower|reduce|cut|keep)\b.{0,220}?interest rates?",
        lowered,
    )
    if action_match:
        verb = action_match.group(1)
        if verb in {"raise", "increase"}:
            action, action_score = "HIKE", 1.0
        elif verb in {"lower", "reduce", "cut"}:
            action, action_score = "CUT", -1.0
        elif verb == "keep" and "unchanged" in lowered[action_match.start():action_match.start() + 420]:
            action, action_score = "UNCHANGED", 0.0

    if action_score is None and re.search(r"decided.{0,120}keep.{0,160}interest rates?.{0,100}unchanged", lowered):
        action, action_score = "UNCHANGED", 0.0

    basis_points = None
    if action_score in {-1.0, 1.0}:
        action_window = lowered[action_match.start():action_match.start() + 500] if action_match else lowered[:1200]
        match = re.search(r"(\d+(?:[.,]\d+)?)\s+basis points?", action_window)
        if match:
            basis_points = float(match.group(1).replace(",", "."))

    deposit_rate = None
    match = re.search(r"deposit facility.{0,220}?(\d+(?:[.,]\d+)?)\s*%", lowered)
    if match:
        deposit_rate = float(match.group(1).replace(",", "."))

    return {
        "parsed": action_score is not None,
        "action": action,
        "action_score": action_score,
        "basis_points": basis_points,
        "deposit_facility_rate_percent": deposit_rate,
        "text_fingerprint": {
            "explicit_rate_action_found": action_score is not None,
            "consensus_claimed": False,
            "guidance_score_claimed": False,
        },
    }


def _fx_reaction(rows: Sequence[Any], decision_time: datetime, observed_at: datetime) -> dict[str, Any]:
    if not rows:
        return {"available": False, "reason": "eurusd_bars_missing"}
    decision_utc = decision_time.astimezone(timezone.utc)
    observed_utc = observed_at.astimezone(timezone.utc)
    before = [row for row in rows if getattr(row, "timestamp", None) is not None and row.timestamp.astimezone(timezone.utc) <= decision_utc]
    after = [row for row in rows if getattr(row, "timestamp", None) is not None and decision_utc < row.timestamp.astimezone(timezone.utc) <= observed_utc]
    if not before or not after:
        return {"available": False, "reason": "post_decision_bar_not_available"}
    baseline = before[-1]
    latest = after[-1]
    baseline_price = float(baseline.close)
    latest_price = float(latest.close)
    if baseline_price <= 0:
        return {"available": False, "reason": "invalid_baseline_price"}
    change = latest_price / baseline_price - 1.0
    return {
        "available": True,
        "baseline_at": baseline.timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "latest_at": latest.timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "baseline_price": round(baseline_price, 6),
        "latest_price": round(latest_price, 6),
        "return": round(change, 6),
        "score": round(_clamp(change / FX_REACTION_NORMALIZER), 6),
        "normalizer_return": FX_REACTION_NORMALIZER,
    }


def build_ecb_policy_context(observed_at: datetime, fx_rows: Sequence[Any], *, fetcher: Fetcher | None = None) -> dict[str, Any]:
    """Build auditable ECB context for the current EUR/USD decision cycle."""
    fetcher = fetcher or _fetch_text
    decision_time, calendar_source = _scheduled_event_for(observed_at, fetcher)
    context: dict[str, Any] = {
        "schema_version": "eurusd-ecb-policy-context-v1",
        "enabled": True,
        "central_bank": "ECB",
        "observed_at": observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event_active": False,
        "coverage": False,
        "must_block_entry": False,
        "status": "NO_ACTIVE_ECB_EVENT",
        "calendar_source": calendar_source,
        "official_sources": {
            "meeting_calendar": ECB_MEETING_CALENDAR_URL,
            "conference_page": ECB_PRESS_CONFERENCE_URL,
            "press_rss": ECB_PRESS_RSS_URL,
        },
        "consensus": {
            "available": False,
            "claimed": False,
            "note": "No reliable machine-readable consensus source is configured; no consensus is invented.",
        },
        "guidance": {
            "directional_score_available": False,
            "claimed": False,
            "note": "Guidance tone is not scored without a validated parser.",
        },
        "ois_repricing": {
            "available": False,
            "claimed": False,
            "note": "No direct EUR OIS feed is configured in v1.7.",
        },
        "veto_threshold": POLICY_VETO_THRESHOLD,
    }
    if decision_time is None:
        return context

    local_now = observed_at.astimezone(BERLIN)
    context.update({
        "event_active": True,
        "event_date": decision_time.date().isoformat(),
        "decision_time": decision_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "ECB_EVENT_WINDOW",
    })

    if local_now < decision_time:
        context.update({
            "must_block_entry": True,
            "status": "ECB_DECISION_PENDING",
            "block_reason": "ecb_policy_decision_pending",
        })
        return context

    link = _decision_link_for_day(decision_time.date(), fetcher)
    if not link:
        context.update({
            "must_block_entry": True,
            "status": "ECB_DECISION_SOURCE_UNAVAILABLE",
            "block_reason": "ecb_policy_event_data_unavailable",
        })
        return context

    try:
        decision = parse_ecb_decision(fetcher(link))
    except Exception as exc:
        context.update({
            "must_block_entry": True,
            "status": "ECB_DECISION_FETCH_FAILED",
            "block_reason": "ecb_policy_event_data_unavailable",
            "source_error": type(exc).__name__,
            "decision_url": link,
        })
        return context

    context["decision_url"] = link
    context["rate_decision"] = decision
    if not decision.get("parsed"):
        context.update({
            "must_block_entry": True,
            "status": "ECB_DECISION_UNPARSED",
            "block_reason": "ecb_policy_event_data_unavailable",
        })
        return context

    reaction = _fx_reaction(fx_rows, decision_time, observed_at)
    context["market_reaction"] = reaction
    if not reaction.get("available"):
        context.update({
            "must_block_entry": True,
            "status": "ECB_POST_DECISION_REACTION_PENDING",
            "block_reason": "ecb_post_decision_reaction_not_available",
        })
        return context

    action_score = float(decision["action_score"])
    reaction_score = float(reaction["score"])
    policy_score = 0.60 * action_score + 0.40 * reaction_score
    context.update({
        "coverage": True,
        "must_block_entry": False,
        "status": "ECB_POLICY_COVERED",
        "policy_score": round(_clamp(policy_score), 6),
        "policy_score_components": {
            "explicit_rate_action": round(action_score, 6),
            "post_decision_eurusd_reaction": round(reaction_score, 6),
        },
        "policy_score_weights": {
            "explicit_rate_action": 0.60,
            "post_decision_eurusd_reaction": 0.40,
        },
        "directional_interpretation": (
            "EURUSD_BULLISH" if policy_score >= POLICY_VETO_THRESHOLD
            else "EURUSD_BEARISH" if policy_score <= -POLICY_VETO_THRESHOLD
            else "NEUTRAL_OR_MIXED"
        ),
    })
    return context


def policy_conflicts(direction: str, context: dict[str, Any]) -> bool:
    if not context.get("coverage"):
        return False
    score = context.get("policy_score")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return False
    normalized = str(direction or "").upper()
    if normalized == "SHORT":
        return float(score) >= POLICY_VETO_THRESHOLD
    if normalized == "LONG":
        return float(score) <= -POLICY_VETO_THRESHOLD
    return False
