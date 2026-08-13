#!/usr/bin/env python3
"""Event-driven evidence layer for the Polish GPW 1-2 session outlook.

The core GPW engine remains deterministic and fail-closed.  This module adds a
separate evidence channel for issuer reports (ESPI/EBI surfaced by PAP),
classifies material events, attaches the latest completed-session price/volume
reaction and learns *boundedly* from resolved paper trades with the same event
type.

Important governance rules:
* official reports are evidence, not an automatic buy/sell signal;
* Gemini still has to cite source ids and the second review can veto a trade;
* event learning cannot mutate base strategy weights;
* event-history adjustment is zero until the configured sample exists and is
  bounded thereafter.
"""
from __future__ import annotations

import hashlib
import re
import statistics
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

try:
    from scripts import gpw_daily_pick as gpw
except ModuleNotFoundError:  # GitHub Actions uses PYTHONPATH=scripts
    import gpw_daily_pick as gpw


DEFAULT_OFFICIAL_LOOKBACK_HOURS = 120
DEFAULT_GENERAL_LOOKBACK_HOURS = 84
DEFAULT_EVENT_MIN_SAMPLE = 6
DEFAULT_EVENT_PRIOR_STRENGTH = 8.0
DEFAULT_MAX_EVENT_ADJUSTMENT = 8.0

# Order matters: more specific event families are matched first.
EVENT_RULES: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("earnings", 5, ("wyniki", "wyników", "zysk", "strata", "ebitda", "przychod", "raport okresowy", "raport kwartalny", "raport półroczny", "raport roczny", "szacunki wynik")),
    ("guidance", 5, ("prognoz", "guidance", "outlook", "cele finansowe", "strategia finansowa", "podnosi prognozę", "obniża prognozę")),
    ("contract", 4, ("kontrakt", "umowa", "zamówienie", "przetarg", "list intencyjny", "portfel zamówień")),
    ("ma", 5, ("przejęcie", "akwizyc", "połączenie", "fuzj", "merger", "nabycie akcji", "nabycie udziałów", "sprzedaż aktyw", "wezwanie")),
    ("regulatory", 5, ("uokik", "knf", "kara", "postępowanie", "regulator", "koncesj", "decyzja administracyjna", "spór", "pozew", "roszczen", "sąd")),
    ("capital", 4, ("emisja", "obligac", "finansowanie", "kredyt", "pożyczk", "podwyższenie kapitału", "warranty", "refinans")),
    ("buyback", 4, ("skup akcji", "buyback", "akcje własne")),
    ("dividend", 4, ("dywidend", "zaliczka na poczet dywidendy")),
    ("insider", 3, ("art. 19", "osoby pełniącej obowiązki zarządcze", "osoby pełniacej obowiązki zarządcze", "transakcja osoby", "transakcji osoby")),
    ("shareholding", 3, ("próg", "znaczny pakiet", "5% głos", "akcjonariusz", "zmiana udziału")),
    ("management", 3, ("zarząd", "rady nadzorczej", "rada nadzorcza", "rezygnacja", "powołanie", "odwołanie")),
    ("calendar", 2, ("termin publikacji", "zmiana terminu", "walne zgromadzenie", "zwołanie zwyczajnego", "zwołanie nadzwyczajnego")),
)

EVENT_LABELS = {
    "earnings": "wyniki / raport okresowy",
    "guidance": "prognoza / guidance",
    "contract": "kontrakt / zamówienie",
    "ma": "M&A / zmiana właścicielska",
    "regulatory": "regulacja / spór prawny",
    "capital": "finansowanie / emisja",
    "buyback": "skup akcji",
    "dividend": "dywidenda",
    "insider": "transakcja insidera",
    "shareholding": "akcjonariat",
    "management": "zarząd / rada nadzorcza",
    "calendar": "kalendarz korporacyjny",
    "other": "inne zdarzenie",
}


def classify_event(title: str) -> dict[str, Any]:
    lowered = re.sub(r"\s+", " ", str(title or "").casefold())
    for event_type, materiality, tokens in EVENT_RULES:
        if any(token.casefold() in lowered for token in tokens):
            return {
                "event_type": event_type,
                "event_label": EVENT_LABELS[event_type],
                "materiality": materiality,
            }
    return {"event_type": "other", "event_label": EVENT_LABELS["other"], "materiality": 2}


def _rss_items(
    query: str,
    *,
    now: datetime,
    max_age_hours: int,
    source_kind: str,
    channel: str,
    force_primary: bool,
    limit: int,
) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=pl&gl=PL&ceid=PL:pl"
    root = ET.fromstring(gpw.request_bytes(url, timeout=18, attempts=2))
    rows: list[dict[str, Any]] = []
    for element in root.findall("./channel/item"):
        title = (element.findtext("title") or "").strip()
        link = (element.findtext("link") or "").strip()
        published_raw = (element.findtext("pubDate") or "").strip()
        source_element = element.find("source")
        publisher = ((source_element.text if source_element is not None else "") or "").strip()
        try:
            published = parsedate_to_datetime(published_raw).astimezone(gpw.WARSAW)
        except Exception:
            continue
        age_hours = (now - published).total_seconds() / 3600
        if not title or not link or age_hours < -1 or age_hours > max_age_hours:
            continue
        if force_primary:
            marker = f"{publisher} {title}".casefold()
            if not any(token in marker for token in ("pap", "espi", "ebi", "raport bieżący", "raport biezacy")):
                continue
        event = classify_event(title)
        fingerprint = hashlib.sha1(
            f"{channel}|{publisher}|{title}|{published.isoformat()}".encode("utf-8")
        ).hexdigest()[:12]
        rows.append(
            {
                "id": f"src-{fingerprint}",
                "title": title[:280],
                "url": link,
                "publisher": publisher or ("PAP / ESPI-EBI" if force_primary else "Google News"),
                "published_at": published.isoformat(timespec="minutes"),
                "age_hours": round(age_hours, 1),
                "quality": "pierwotne" if force_primary else "wtórne",
                "source_kind": source_kind,
                "channel": channel,
                "event_type": event["event_type"],
                "event_label": event["event_label"],
                "materiality": event["materiality"],
            }
        )
        if len(rows) >= limit:
            break
    return rows


def official_report_items(company: dict[str, Any], *, now: datetime, limit: int = 6) -> list[dict[str, Any]]:
    """Discover recent issuer reports from PAP-hosted ESPI/EBI publication surfaces.

    Google News is used only as the discovery transport.  The query is restricted
    to PAP publication domains and returned rows are explicitly marked as
    issuer-report evidence.  A discovery failure is non-fatal; the independent
    news channel remains available.
    """
    name = str(company.get("name") or "").strip()
    ticker = str(company.get("symbol") or "").removesuffix(".WA")
    queries = (
        f'site:pap-mediaroom.pl/biznes-i-finanse "{name}" (ESPI OR EBI OR "Raport bieżący" OR "Raport okresowy") when:5d',
        f'site:biznes.pap.pl "{name}" (ESPI OR EBI OR "raport bieżący") when:5d',
    )
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                _rss_items,
                query,
                now=now,
                max_age_hours=DEFAULT_OFFICIAL_LOOKBACK_HOURS,
                source_kind="issuer_report",
                channel="ESPI_EBI_PAP",
                force_primary=True,
                limit=limit,
            )
            for query in queries
        ]
        for future in as_completed(futures):
            try:
                rows.extend(future.result())
            except Exception:
                continue
    # The ticker is useful for auditing even though the source is keyed by issuer name.
    for row in rows:
        row["issuer_ticker"] = ticker
    return _dedupe(rows, limit=limit)


def _enrich_general(source: dict[str, Any]) -> dict[str, Any]:
    row = dict(source)
    event = classify_event(str(row.get("title") or ""))
    row.update(event)
    row.setdefault("source_kind", "news")
    row.setdefault("channel", "NEWS")
    return row


def _dedupe(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    ordered = sorted(
        rows,
        key=lambda row: (
            0 if row.get("source_kind") == "issuer_report" else 1,
            -int(row.get("materiality") or 0),
            float(row.get("age_hours") or 9999),
        ),
    )
    for row in ordered:
        title_key = re.sub(r"[^a-z0-9ąćęłńóśźż]+", " ", str(row.get("title") or "").casefold()).strip()
        if not title_key or title_key in seen:
            continue
        seen.add(title_key)
        unique.append(row)
        if len(unique) >= limit:
            break
    return unique


def combined_news_items(company: dict[str, Any], *, now: datetime, limit: int = 10) -> list[dict[str, Any]]:
    """Merge issuer reports and independent news without letting either channel block the other."""
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(official_report_items, company, now=now, limit=6): "official",
            pool.submit(gpw.news_items, company, now=now, limit=8): "news",
        }
        for future in as_completed(futures):
            kind = futures[future]
            try:
                value = future.result()
            except Exception:
                value = []
            if kind == "news":
                rows.extend(_enrich_general(row) for row in value)
            else:
                rows.extend(value)
    return _dedupe(rows, limit=limit)


def market_reaction(candidate: dict[str, Any]) -> dict[str, Any]:
    returns = candidate.get("returns") or {}
    return {
        "measurement": "latest_completed_session",
        "return_1d_percent": round(float(returns.get("1d") or 0.0) * 100, 3),
        "return_5d_percent": round(float(returns.get("5d") or 0.0) * 100, 3),
        "relative_5d_percent": round(float(candidate.get("relative_5d") or 0.0) * 100, 3),
        "volume_ratio_20d": round(float(candidate.get("volume_ratio") or 0.0), 3),
        "reference_price": candidate.get("reference_price"),
        "note": "Reakcja z ostatniej zakończonej sesji; nie jest traktowana jako samodzielny katalizator.",
    }


def _resolved_same_event(history: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    rows = []
    for row in history:
        selection = row.get("selection") or {}
        outcome = row.get("outcome") or {}
        context = selection.get("event_context") or {}
        if (
            row.get("decision") == "TRANSAKCJA"
            and outcome.get("status") == "RESOLVED"
            and outcome.get("activated") is True
            and context.get("primary_type") == event_type
        ):
            rows.append(row)
    return rows


def event_history_adjustment(
    history: list[dict[str, Any]],
    event_type: str,
    *,
    minimum_sample: int = DEFAULT_EVENT_MIN_SAMPLE,
    prior_strength: float = DEFAULT_EVENT_PRIOR_STRENGTH,
    max_adjustment: float = DEFAULT_MAX_EVENT_ADJUSTMENT,
) -> dict[str, Any]:
    rows = _resolved_same_event(history, event_type)
    sample = len(rows)
    if not rows:
        average_r = None
    else:
        average_r = statistics.fmean(float(row["outcome"].get("r_multiple", 0.0)) for row in rows)
    if sample < minimum_sample or average_r is None:
        adjustment = 0.0
        shrunk_r = 0.0
    else:
        shrunk_r = average_r * sample / (sample + max(prior_strength, 0.0))
        adjustment = max(-max_adjustment, min(max_adjustment, shrunk_r * 8.0))
    return {
        "sample": sample,
        "minimum_sample": minimum_sample,
        "active": sample >= minimum_sample,
        "average_r": round(average_r, 3) if average_r is not None else None,
        "shrunk_r": round(shrunk_r, 3),
        "catalyst_adjustment": round(adjustment, 2),
    }


def build_event_context(
    candidate: dict[str, Any],
    analysis: dict[str, Any],
    history: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    selected_ids = set(str(value) for value in (analysis.get("source_ids") or []))
    selected = [source for source in candidate.get("sources", []) if source.get("id") in selected_ids]
    selected.sort(
        key=lambda source: (
            0 if source.get("source_kind") == "issuer_report" else 1,
            -int(source.get("materiality") or 0),
            float(source.get("age_hours") or 9999),
        )
    )
    primary = selected[0] if selected else None
    event_config = config.get("event_layer") or {}
    event_type = str((primary or {}).get("event_type") or "other")
    learning = event_history_adjustment(
        history,
        event_type,
        minimum_sample=int(event_config.get("minimum_resolved_events_for_adaptation", DEFAULT_EVENT_MIN_SAMPLE)),
        prior_strength=float(event_config.get("prior_strength", DEFAULT_EVENT_PRIOR_STRENGTH)),
        max_adjustment=float(event_config.get("max_catalyst_score_adjustment", DEFAULT_MAX_EVENT_ADJUSTMENT)),
    )
    return {
        "primary_type": event_type,
        "primary_label": EVENT_LABELS.get(event_type, EVENT_LABELS["other"]),
        "primary_source_id": (primary or {}).get("id"),
        "primary_source_kind": (primary or {}).get("source_kind"),
        "primary_channel": (primary or {}).get("channel"),
        "materiality": int((primary or {}).get("materiality") or 0),
        "official_report_used": bool((primary or {}).get("source_kind") == "issuer_report"),
        "selected_event_source_ids": [str(source.get("id")) for source in selected if source.get("id")],
        "market_reaction": market_reaction(candidate),
        "event_learning": learning,
    }


def public_event_learning(history: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    event_types = sorted(
        {
            str((row.get("selection") or {}).get("event_context", {}).get("primary_type") or "")
            for row in history
            if (row.get("selection") or {}).get("event_context")
        }
        - {""}
    )
    event_config = config.get("event_layer") or {}
    result = []
    for event_type in event_types:
        stats = event_history_adjustment(
            history,
            event_type,
            minimum_sample=int(event_config.get("minimum_resolved_events_for_adaptation", DEFAULT_EVENT_MIN_SAMPLE)),
            prior_strength=float(event_config.get("prior_strength", DEFAULT_EVENT_PRIOR_STRENGTH)),
            max_adjustment=float(event_config.get("max_catalyst_score_adjustment", DEFAULT_MAX_EVENT_ADJUSTMENT)),
        )
        result.append({"event_type": event_type, "label": EVENT_LABELS.get(event_type, event_type), **stats})
    return result
