#!/usr/bin/env python3
"""One bounded, as-of Fed/ECB publication wire for both EURUSD engines.

No trading writes, no live-speech claim, no price inference. Snapshots are
content-addressed; prospective publication versions retain their first receipt.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import subprocess
import re
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from functools import lru_cache
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Mapping, Sequence, TypedDict

from eurusd_central_bank_news_interpreter import classify, document_text, interpret_document

SCHEMA_VERSION = "eurusd-central-bank-wire-v1"
OFFICIAL_RSS = {
    "fed_monetary": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "fed_speeches": "https://www.federalreserve.gov/feeds/speeches.xml",
    "fed_speeches_testimony": "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml",
    "ecb_press_speeches": "https://www.ecb.europa.eu/rss/press.html",
}
SECONDARY_RSS = "https://news.google.com/rss/search?q=" + urllib.parse.quote('(FOMC OR "Federal Reserve Chair" OR "ECB President" OR "Governing Council") ("press conference" OR "rate decision" OR says) when:1d') + "&hl=en-US&gl=US&ceid=US:en"
OFFICIAL_HOSTS = frozenset({"federalreserve.gov", "www.federalreserve.gov", "ecb.europa.eu", "www.ecb.europa.eu"})
ALL_HOSTS = OFFICIAL_HOSTS | {"news.google.com"}
MAX_XML_BYTES = 1_000_000
MAX_DOCUMENT_BYTES = 600_000
MAX_ITEMS_PER_FEED = 40
MAX_EVENTS = 1000
MAX_DOCUMENT_FETCHES = 12
SOURCE_SLA_SECONDS = 900
MAX_UNRESOLVED_WINDOWS = 32
MAX_UNRESOLVED_BYTES = 30_000
# Retain proof through the longest cooldown currently allowed by risk policy.
UNRESOLVED_END_COOLDOWN_MINUTES = 120
UTC = timezone.utc


@lru_cache(maxsize=1)
def runtime_commit() -> str | None:
    """Actual checkout identity; workflow trigger SHA may refer to older code."""
    try:
        value=subprocess.check_output(['git','rev-parse','HEAD'],cwd=Path(__file__).resolve().parents[1],
                                      stderr=subprocess.DEVNULL,text=True,timeout=3).strip()
        return value if re.fullmatch(r'[a-f0-9]{40,64}',value) else None
    except (OSError,subprocess.SubprocessError):
        return None


class WireSnapshot(TypedDict):
    schema_version: str
    generated_at: str
    as_of: str
    overall_status: str
    sources: list[dict[str, Any]]
    events: list[dict[str, Any]]
    event_window: dict[str, Any]
    integrity: dict[str, Any]


def dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    else:
        if not value:
            return None
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            try:
                result = parsedate_to_datetime(str(value))
            except (ValueError, TypeError, OverflowError):
                return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(UTC)


def iso(value: datetime) -> str:
    parsed = dt(value)
    if parsed is None:
        raise ValueError("timezone-aware datetime required")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(snapshot))
    payload.setdefault("integrity", {}).pop("snapshot_sha256", None)
    return digest(payload)


def canonical_url(url: str, *, secondary: bool = False) -> str:
    parts = urllib.parse.urlsplit(str(url))
    hosts = ALL_HOSTS if secondary else OFFICIAL_HOSTS
    if parts.scheme.lower() != "https" or parts.hostname not in hosts or parts.port not in {None, 443} or parts.username or parts.password:
        raise ValueError("URL outside explicit HTTPS source allowlist")
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit(("https", parts.netloc.lower(), parts.path or "/", urllib.parse.urlencode(sorted(query)), ""))


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Check BEFORE the next HTTP request; checking response.geturl is too late.
        canonical_url(newurl, secondary=(urllib.parse.urlsplit(req.full_url).hostname == "news.google.com"))
        old_host = urllib.parse.urlsplit(req.full_url).hostname or ""
        new_host = urllib.parse.urlsplit(newurl).hostname or ""
        if old_host.removeprefix("www.") != new_host.removeprefix("www."):
            raise ValueError("cross-bank/source redirect rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: bytes
    url: str
    headers: Mapping[str, str]
    received_at: str | None = None


class HttpClient:
    def __init__(self, *, timeout: float = 8, retries: int = 2, sleep=time.sleep, jitter=random.random):
        self.timeout = timeout
        self.retries = max(0, min(int(retries), 2))
        self.sleep, self.jitter = sleep, jitter
        # Bundled Windows Python can lack an intermediate root used by ECB.
        # Use the installed CA bundle when available, retaining full TLS checks.
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            context = ssl.create_default_context()
        self.opener = urllib.request.build_opener(_Redirects(), urllib.request.HTTPSHandler(context=context))

    def get(self, url: str, *, headers: Mapping[str, str] | None = None,
            max_bytes: int = MAX_XML_BYTES) -> HttpResult:
        canonical_url(url, secondary=urllib.parse.urlsplit(url).hostname == "news.google.com")
        request_headers = {"User-Agent": "BriefRooms-EURUSD-CB-Wire/1.0 (+https://briefrooms.com)",
                           "Accept": "application/xml,text/xml,text/html,application/atom+xml;q=0.9",
                           "Accept-Encoding": "identity", **dict(headers or {})}
        for attempt in range(self.retries + 1):
            try:
                with self.opener.open(urllib.request.Request(url, headers=request_headers), timeout=self.timeout) as response:
                    canonical_url(response.geturl(), secondary=urllib.parse.urlsplit(url).hostname == "news.google.com")
                    length = response.headers.get("Content-Length")
                    if length and int(length) > max_bytes:
                        raise ValueError("response exceeds byte cap")
                    body = response.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        raise ValueError("response exceeds byte cap")
                    return HttpResult(response.status, body, response.geturl(), dict(response.headers.items()), iso(datetime.now(UTC)))
            except urllib.error.HTTPError as error:
                if error.code == 304:
                    return HttpResult(304, b"", url, dict(error.headers.items()), iso(datetime.now(UTC)))
                if error.code not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    raise
                retry_after = error.headers.get("Retry-After", "")
                wait = min(float(retry_after), 3) if retry_after.isdigit() else min(2 ** attempt + self.jitter(), 3)
                self.sleep(wait)
            except (urllib.error.URLError, TimeoutError):
                if attempt == self.retries:
                    raise
                self.sleep(min(2 ** attempt + self.jitter(), 3))
        raise RuntimeError("unreachable")


def parse_feed(body: bytes, *, source_id: str, now: datetime) -> list[dict[str, Any]]:
    if len(body) > MAX_XML_BYTES or b"\x00" in body or re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", body, re.I):
        raise ValueError("oversized or unsafe XML")
    root = ET.fromstring(body)
    if root.tag.rsplit("}", 1)[-1].lower() not in {"rss", "feed", "rdf"}:
        raise ValueError("response is not RSS/Atom")
    if sum(1 for _ in root.iter()) > 12_000:
        raise ValueError("XML element cap exceeded")
    nodes = list(root.findall(".//item")) or list(root.findall(".//{*}entry"))
    rows: list[dict[str, Any]] = []
    for item in nodes[:MAX_ITEMS_PER_FEED]:
        def text(*names):
            for name in names:
                node = item.find(name)
                if node is None:
                    node = item.find("{*}" + name)
                if node is not None and node.text:
                    return node.text.strip()
            return ""
        title = document_text(text("title"))[:500]
        url = text("link")
        if not url:
            links = item.findall("{*}link")
            url = next((link.attrib.get("href", "") for link in links if link.attrib.get("rel", "alternate") == "alternate"), "")
        published = dt(text("pubDate", "published", "date"))
        # Updated is a correction timestamp, not an invented original publication.
        updated = dt(text("updated"))
        if not title or published is None or published > now or published < now - timedelta(days=14):
            continue
        secondary = source_id == "google_news"
        try:
            url = canonical_url(url, secondary=secondary)
        except (ValueError, TypeError):
            continue
        bank = "FED" if source_id.startswith("fed_") else "ECB" if source_id.startswith("ecb_") else (
            "ECB" if re.search(r"\b(?:ECB|European Central Bank|Governing Council)\b", title, re.I) else "FED")
        required_host = "federalreserve.gov" if bank == "FED" else "ecb.europa.eu"
        if not secondary and urllib.parse.urlsplit(url).hostname.removeprefix("www.") != required_host:
            continue
        publisher = text("source")
        if secondary:
            from news_source_architecture import source_profile
            if source_profile(publisher).tier not in {"wire", "premium", "quality"}:
                continue
        rows.append({"headline": title, "source_url": url, "published_at": iso(published),
                     "updated_at": iso(updated) if updated and updated <= now else None,
                     "guid": text("guid", "id")[:500], "summary": document_text(text("description", "summary", "content"))[:1500],
                     "bank": bank, "publisher": publisher or None, "source_id": source_id})
    return rows


def _calendar_bank(event: Mapping[str, Any]) -> str | None:
    title = str(event.get("title") or event.get("headline") or "").lower()
    bank = str(event.get("bank") or "").upper()
    if bank in {"FED", "ECB"}:
        return bank
    if "fomc" in title or "federal open market" in title:
        return "FED"
    if "ecb" in title or "monetary policy" in title and "europa.eu" in str(event.get("source_ref", "")):
        return "ECB"
    return None


def _calendar_match(row: Mapping[str, Any], calendar: Sequence[Mapping[str, Any]]) -> str | None:
    published = dt(row.get("published_at"))
    if published is None:
        return None
    for event in calendar:
        scheduled = dt(event.get("event_at") or event.get("scheduled_at"))
        if _calendar_bank(event) == row.get("bank") and scheduled and scheduled.date() == published.date():
            return str(event.get("uid") or event.get("event_id") or digest(event))
    return None


def event_window(calendar_events: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]], now: datetime) -> dict[str, Any]:
    candidates = []
    for event in calendar_events:
        bank = _calendar_bank(event)
        scheduled = dt(event.get("event_at") or event.get("scheduled_at"))
        source = str(event.get("source_ref") or event.get("source_url") or "")
        try:
            canonical_url(source)
        except ValueError:
            continue
        observed = dt(event.get("calendar_checked_at") or event.get("first_seen_at") or event.get("observed_at"))
        if bank and scheduled and observed and timedelta(0) <= now - observed <= timedelta(hours=24) and -24 * 3600 <= (scheduled - now).total_seconds() <= 120 * 60:
            candidates.append((abs((scheduled - now).total_seconds()), event, bank, scheduled))
    if not candidates:
        releases = _unscheduled_release_windows(calendar_events, events, now)
        if releases:
            return releases[0]
        return {"event_id": None, "scheduled_at": None, "phase": "UNKNOWN", "bank": None,
                "last_authoritative_update_at": None, "conference_status": "UNKNOWN", "calendar_verified": False}
    _, event, bank, scheduled = min(candidates, key=lambda pair: pair[0])
    source = str(event.get("source_ref") or event.get("source_url"))
    observed = dt(event.get("calendar_checked_at") or event.get("first_seen_at") or event.get("observed_at"))
    first_seen = dt(event.get("first_seen_at") or event.get("observed_at") or event.get("calendar_checked_at"))
    event_id = str(event.get("uid") or event.get("event_id") or digest(event))
    related = [row for row in events if row.get("related_calendar_event_id") == event_id and dt(row.get("ingested_at")) and dt(row["ingested_at"]) <= now]
    official = [row for row in related if row.get("statement_verified")]
    conferences = [row for row in related if row.get("event_type") == "PRESS_CONFERENCE" and row.get("source_class") != "SECONDARY_HEADLINE"]
    # Publication of a transcript does NOT prove the conference ended or was
    # known live. Only explicit verified status evidence may release the guard.
    status = str(event.get("conference_status") or "UNKNOWN")
    status_seen = dt(event.get("conference_status_verified_at"))
    status_source = str(event.get("conference_status_source_url") or "")
    try:
        canonical_url(status_source)
        verified_status_source = urllib.parse.urlsplit(status_source).hostname.removeprefix("www.") == ("federalreserve.gov" if bank == "FED" else "ecb.europa.eu")
    except ValueError:
        verified_status_source = False
    if status not in {"ANNOUNCED", "IN_PROGRESS", "ENDED"} or status_seen is None or status_seen > now or not verified_status_source:
        status = "UNKNOWN"
    phase = "PRE_EVENT" if now < scheduled else "PRESS_CONFERENCE" if status == "IN_PROGRESS" else "RELEASED" if official else "UNKNOWN"
    if status == "ENDED":
        phase = "POST_EVENT"
    return {"event_id": event_id, "scheduled_at": iso(scheduled), "bank": bank,
            "event_type": "FOMC_DECISION" if bank == "FED" else "ECB_DECISION", "phase": phase,
            "conference_status": status, "conference_ended_at": event.get("conference_ended_at") if status == "ENDED" else None,
            "conference_status_verified_at": iso(status_seen) if status_seen else None,
            "conference_status_source_url": status_source if status != "UNKNOWN" else None,
            "conference_end_evidence": copy.deepcopy(event.get("conference_end_evidence")) if status == "ENDED" else None,
            "calendar_verified": True, "calendar_source_url": source, "source_url": source,
            "calendar_checked_at": iso(observed), "first_seen_at": iso(first_seen),
            "calendar_time_precision": event.get("time_precision", "unknown"),
            "time_precision": event.get("time_precision", "unknown"),
            "release_published_at": max((row["published_at"] for row in official), default=None),
            "last_authoritative_update_at": max((row["ingested_at"] for row in official + conferences), default=None)}


def _unscheduled_release_windows(calendar_events: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]], now: datetime) -> list[dict]:
    """A verified release is an event even when no calendar predicted it."""
    known = {str(row.get("uid") or row.get("event_id")) for row in calendar_events
             if _calendar_bank(row) and dt(row.get("event_at") or row.get("scheduled_at"))
             and dt(row.get("calendar_checked_at") or row.get("observed_at") or row.get("first_seen_at"))}
    windows = {}
    for row in sorted(events, key=lambda value: (str(value.get("ingested_at", "")), str(value.get("event_id", "")))):
        if row.get("event_type") not in {"FOMC_DECISION", "ECB_DECISION"} or row.get("statement_verified") is not True or row.get("source_class") == "SECONDARY_HEADLINE":
            continue
        published, first, received = (dt(row.get(key)) for key in ("published_at", "first_seen_at", "ingested_at"))
        if not published or not first or not received or not published <= first <= received <= now or now - published > timedelta(hours=24):
            continue
        if row.get("related_calendar_event_id") in known:
            continue
        try:
            url = canonical_url(row["source_url"])
        except (ValueError, KeyError):
            continue
        if urllib.parse.urlsplit(url).hostname.removeprefix("www.") != ("federalreserve.gov" if row.get("bank") == "FED" else "ecb.europa.eu"):
            continue
        identity = "cb-release:" + str(row.get("dedup_key") or digest({"url": url}))[:32]
        proof = {key: row.get(key) for key in ("event_id", "source_url", "published_at", "first_seen_at", "ingested_at", "document_sha256", "statement_verified")}
        windows[identity] = {"event_id": identity, "bank": row["bank"], "event_type": row["event_type"],
            "scheduled_at": row["published_at"], "phase": "RELEASED", "schedule_kind": "UNSCHEDULED_RELEASE",
            "release_event_id": row["event_id"], "release_published_at": row["published_at"], "release_proof": proof,
            "source_url": url, "first_seen_at": row["first_seen_at"], "observed_at": row["ingested_at"],
            "calendar_verified": False, "time_precision": "exact", "conference_status": "UNKNOWN",
            "conference_ended_at": None, "last_authoritative_update_at": row["ingested_at"]}
    return sorted(windows.values(), key=lambda row: (row["scheduled_at"], row["event_id"]), reverse=True)


def _verified_conference_end(window: Mapping[str, Any], now: datetime) -> bool:
    proof = window.get("conference_end_evidence")
    ended = dt(window.get("conference_ended_at"))
    if window.get("conference_status") != "ENDED" or not isinstance(proof, Mapping) or not ended:
        return False
    published, first, received = (dt(proof.get(key)) for key in ("published_at", "first_seen_at", "ingested_at"))
    if not published or not first or not received or not ended <= published <= first <= received <= now or proof.get("statement_verified") is not True or proof.get("event_id") != window.get("event_id"):
        return False
    try:
        source = canonical_url(proof["source_url"])
    except (ValueError, KeyError):
        return False
    return urllib.parse.urlsplit(source).hostname.removeprefix("www.") == ("federalreserve.gov" if window.get("bank") == "FED" else "ecb.europa.eu")


def _compact_window(window: Mapping[str, Any]) -> dict:
    fields = ("event_id", "bank", "event_type", "scheduled_at", "phase", "schedule_kind", "release_event_id", "release_published_at",
              "source_url", "calendar_source_url", "calendar_verified", "calendar_checked_at", "calendar_time_precision", "time_precision",
              "first_seen_at", "observed_at", "conference_status", "conference_ended_at", "conference_status_verified_at",
              "conference_status_source_url", "last_authoritative_update_at")
    result = {key: copy.deepcopy(window[key]) for key in fields if key in window and isinstance(window[key], (str, bool, type(None)))}
    for key in ("release_proof", "conference_end_evidence"):
        proof = window.get(key)
        if isinstance(proof, Mapping):
            result[key] = {field: proof[field] for field in ("event_id", "source_url", "published_at", "first_seen_at", "ingested_at", "document_sha256", "statement_verified")
                           if field in proof and isinstance(proof[field], (str, bool, type(None)))}
    return result


def _unresolved_windows(previous: Mapping[str, Any], current: Sequence[Mapping[str, Any]], now: datetime) -> tuple[list[dict], bool]:
    """Prospectively retain UNKNOWN conferences; clock expiry is not evidence."""
    merged = {}
    old_windows = list(previous.get("unresolved_event_windows") or [])
    if isinstance(previous.get("event_window"), Mapping):
        old_windows.append(previous["event_window"])
    overflow = previous.get("unresolved_event_windows_overflow") is True
    for raw in [*old_windows, *current]:
        if not isinstance(raw, Mapping) or not raw.get("event_id") or not dt(raw.get("scheduled_at")):
            continue
        # Retire prior false ECB calendar windows produced from website nav.
        # These have a canonical HTML-calendar UID, no separate release proof,
        # and cannot be mistaken for verified official monetary decisions.
        uid = str(raw.get("event_id") or "")
        if (raw.get("bank") == "ECB" and raw.get("schedule_kind") != "UNSCHEDULED_RELEASE"
                and raw.get("calendar_source_url") == "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"
                and uid.startswith("eu:European Central Bank:")
                and not ("governing council" in uid.lower() and
                         ("monetary policy meeting" in uid.lower() or "press conference following" in uid.lower())
                         and "(day 1)" not in uid.lower())):
            continue
        row = _compact_window(raw)
        if len(json.dumps(row, ensure_ascii=False).encode()) > MAX_UNRESOLVED_BYTES:
            overflow = True
            continue
        identity = row["event_id"]
        old = merged.get(identity)
        if old:
            # A routine calendar refresh says UNKNOWN; it cannot erase a real
            # recorded end. Conversely, a new valid end supersedes old UNKNOWN.
            if _verified_conference_end(old, now) and not _verified_conference_end(row, now):
                for key in ("conference_status", "conference_ended_at", "conference_status_verified_at", "conference_status_source_url", "conference_end_evidence"):
                    if key in old:
                        row[key] = old[key]
            first = [dt(value) for value in (old.get("first_seen_at"), row.get("first_seen_at")) if dt(value)]
            if first:
                row["first_seen_at"] = iso(min(first))
        merged[identity] = row
    retained = []
    for row in sorted(merged.values(), key=lambda row: (row["scheduled_at"], row["event_id"])):
        # Keep resolved proof beyond the source calendar's one-day lookback:
        # a fresh UNKNOWN calendar row must not resurrect an already ended event.
        if _verified_conference_end(row, now) and now >= dt(row["conference_ended_at"]) + timedelta(days=7):
            continue
        candidate = [*retained, row]
        if len(candidate) > MAX_UNRESOLVED_WINDOWS or len(json.dumps(candidate, ensure_ascii=False).encode()) > MAX_UNRESOLVED_BYTES:
            overflow = True
            continue
        retained.append(row)
    return retained, overflow


def _empty(now: datetime, reason: str = "NOT_COLLECTED") -> dict[str, Any]:
    payload = {"schema_version": SCHEMA_VERSION, "generated_at": iso(now), "as_of": iso(now),
               "overall_status": "UNAVAILABLE", "sources": [], "events": [],
               "event_window": event_window((), (), now), "consumers": {"weekly": {"last_seen_at": None}, "daily": {"last_seen_at": None}},
               "integrity": {"source_count": 0, "fresh_source_count": 0}, "health_reasons": [reason],
               "unresolved_event_windows": [], "unresolved_event_windows_overflow": False,
               "producer_sha": runtime_commit(),
               "producer_code_sha256": digest({name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                   for name in ('eurusd_central_bank_wire.py','eurusd_central_bank_news_interpreter.py')}),
               "latency_claim": "BEST_EFFORT_POLLING_NOT_LIVE_SPEECH"}
    payload["integrity"]["snapshot_sha256"] = snapshot_hash(payload)
    return payload


def _parse_wire_fomc_calendar(text: str, *, now: datetime):
    """Scope the inherited parser to this year's section, including descending pages.

    Fed lists multiple years. A previous-year heading must end this section too;
    its November/December dates must never become invented current-year events.
    This wrapper changes only the EURUSD wire, not other calendar consumers.
    """
    from belief_macro_calendar_adapter import _strip_html, parse_fomc_calendar, NY
    year=now.astimezone(NY).year
    clean=_strip_html(text)
    start=re.search(rf'\b{year}\s+FOMC\s+Meetings\b',clean)
    if not start:return []
    tail=clean[start.end():]
    end=re.search(r'\b20\d{2}\s+FOMC\s+Meetings\b',tail)
    return parse_fomc_calendar(f'{year} FOMC Meetings '+(tail[:end.start()] if end else tail),now=now)


def collect_calendars(*, now: datetime, client: HttpClient | None = None,
                      previous: Mapping[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reuse canonical calendar parsers with this wire's bounded HTTP transport.

    Only Fed and ECB sources are fetched; the general multi-asset adapter is
    never run and no other instrument's state or Evidence is written.
    """
    from belief_macro_calendar_adapter import (ECB_CALENDAR, FOMC_CALENDAR,
        MacroEventCalendarAdapter, parse_eu_calendar_text, parse_fomc_calendar)
    transport = client or HttpClient()
    receipts: dict[str, str] = {}

    class BoundedCalendarClient:
        def text(self, url):
            response = transport.get(url, max_bytes=MAX_XML_BYTES)
            if response.status != 200:
                raise ValueError("calendar requires current verified body")
            receipts[url] = response.received_at or iso(now)
            return response.body.decode("utf-8", "replace")

    adapter = MacroEventCalendarAdapter(client=BoundedCalendarClient())
    calendar = []
    calendar.extend(adapter._collect(key="FOMC", url=FOMC_CALENDAR, require_events=True,
        loader=lambda text: _parse_wire_fomc_calendar(text, now=now)))
    calendar.extend(adapter._collect(key="ECB", url=ECB_CALENDAR, require_events=True,
        loader=lambda text: parse_eu_calendar_text(text, now=now, source="European Central Bank", source_ref=ECB_CALENDAR, horizon_days=240)))
    statuses = adapter.source_status()["sources"]
    coverage = {"complete": all(statuses[key].get("status") == "ok" for key in ("FOMC", "ECB")),
                "checked_at": max(receipts.values(), default=iso(now)),
                "sources": {key: statuses[key] for key in ("FOMC", "ECB")}}
    old_rows = {row.get("uid"): row for row in (previous or {}).get("calendar_events", [])}
    rows = []
    for event in calendar:
        if event.source_ref == ECB_CALENDAR:
            # The generic ECB HTML parser can mistake navigation/breadcrumb
            # text for a dated event. Only Day-2 monetary decisions and the
            # matching Governing Council press conference are risk windows.
            title = event.title.lower()
            if not ("governing council" in title and
                    ("monetary policy meeting" in title or "press conference following" in title)
                    and "(day 1)" not in title):
                continue
        first = old_rows.get(event.uid, {}).get("first_seen_at") or receipts[event.source_ref]
        calendar_row = {"uid": event.uid, "title": event.title, "event_at": iso(event.event_at),
                        "bank": "FED" if event.source_ref == FOMC_CALENDAR else "ECB",
                        "source_ref": event.source_ref, "source": event.source, "importance": event.importance,
                        "time_precision": event.time_precision, "first_seen_at": first,
                        "calendar_checked_at": receipts[event.source_ref], **dict(event.metadata)}
        # metadata cannot replace the transport receipt or source identity.
        calendar_row.update(source_ref=event.source_ref, calendar_checked_at=receipts[event.source_ref], first_seen_at=first)
        calendar_row["conference_status"] = "UNKNOWN"
        # Keep immutable first receipt but refresh successful check time.
        if not dt(first) or dt(first) > dt(receipts[event.source_ref]):
            calendar_row["first_seen_at"] = receipts[event.source_ref]
        # Constructed separately from the immutable publication event journal.
        rows.append(calendar_row)
    return rows, coverage


def validate_snapshot(snapshot: Mapping[str, Any], now: datetime | None = None) -> bool:
    try:
        if snapshot.get("schema_version") != SCHEMA_VERSION or snapshot.get("integrity", {}).get("snapshot_sha256") != snapshot_hash(snapshot):
            return False
        generated = dt(snapshot.get("generated_at"))
        as_of = dt(snapshot.get("as_of"))
        if not generated or not as_of or as_of > generated or now is not None and generated > dt(now):
            return False
        events = snapshot.get("events")
        if not isinstance(events, list) or len(events) > MAX_EVENTS:
            return False
        seen = set()
        for row in events:
            published, first, ingested = (dt(row.get(key)) for key in ("published_at", "first_seen_at", "ingested_at"))
            if not published or not first or not ingested or not published <= first <= ingested <= generated:
                return False
            version_first = dt(row.get("version_first_seen_at") or row.get("first_seen_at"))
            if not version_first or not first <= version_first <= ingested:
                return False
            canonical_url(row["source_url"], secondary=row.get("source_class") == "SECONDARY_HEADLINE")
            if row.get("bank") not in {"FED", "ECB"} or row.get("event_id") in seen:
                return False
            if row.get("source_class") == "SECONDARY_HEADLINE" and (row.get("statement_verified") or row.get("speaker_verified")):
                return False
            if row.get("statement_verified"):
                host = urllib.parse.urlsplit(row["source_url"]).hostname.removeprefix("www.")
                if host != ("federalreserve.gov" if row["bank"] == "FED" else "ecb.europa.eu") or not row.get("document_sha256") or not row.get("facts", {}).get("rate_actual") or not row.get("fact_evidence"):
                    return False
            seen.add(row["event_id"])
        if snapshot.get("overall_status") not in {"FRESH", "PARTIAL", "STALE", "UNAVAILABLE"}:
            return False
        if snapshot.get("integrity", {}).get("source_count") != len(snapshot.get("sources", [])):
            return False
        windows = snapshot.get('unresolved_event_windows', [])
        overflow = snapshot.get('unresolved_event_windows_overflow', False)
        if (not isinstance(windows,list) or len(windows)>MAX_UNRESOLVED_WINDOWS
            or len(json.dumps(windows,ensure_ascii=False).encode())>MAX_UNRESOLVED_BYTES
            or type(overflow) is not bool or overflow and snapshot.get('overall_status')=='FRESH'):
            return False
        window_ids=set()
        for window in windows:
            if (not isinstance(window,Mapping) or not isinstance(window.get('event_id'),str)
                or not 0<len(window['event_id'])<=256 or window['event_id'] in window_ids
                or window.get('bank') not in {'FED','ECB'} or not dt(window.get('scheduled_at'))):
                return False
            canonical_url(window.get('source_url') or window.get('calendar_source_url') or '')
            for field in ('first_seen_at','observed_at','calendar_checked_at'):
                if window.get(field) is not None and (not dt(window[field]) or dt(window[field])>generated):
                    return False
            window_ids.add(window['event_id'])
        sources = snapshot.get("sources", [])
        if not isinstance(sources, list) or len(sources) > len(OFFICIAL_RSS) + 1:
            return False
        source_ids = set()
        fresh_sources = 0
        for source in sources:
            if source.get("source_id") not in {*OFFICIAL_RSS, "google_news"} or source.get("status") not in {"OK", "ERROR", "STALE"}:
                return False
            if source["source_id"] in source_ids:
                return False
            source_ids.add(source["source_id"])
            canonical_url(source["url"], secondary=source["source_id"] == "google_news")
            expected_url = SECONDARY_RSS if source["source_id"] == "google_news" else OFFICIAL_RSS[source["source_id"]]
            if source["url"] != expected_url:
                return False
            source_checked = dt(source.get("checked_at"))
            if not source_checked or source_checked > generated:
                return False
            if source["source_id"] != "google_news" and source["status"] == "OK" and (generated - source_checked).total_seconds() <= SOURCE_SLA_SECONDS:
                fresh_sources += 1
        if snapshot["integrity"].get("fresh_source_count") != fresh_sources:
            return False
        if snapshot.get("overall_status") == "FRESH" and (fresh_sources != len(OFFICIAL_RSS) or not _calendar_coverage_valid(snapshot.get("calendar_coverage"), generated)):
            return False
        return True
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False


def _calendar_coverage_valid(coverage: Any, now: datetime) -> bool:
    if not isinstance(coverage, Mapping) or coverage.get("complete") is not True:
        return False
    checked = dt(coverage.get("checked_at"))
    if not checked or not timedelta(0) <= now - checked <= timedelta(hours=24):
        return False
    sources = coverage.get("sources", {})
    return all(isinstance(sources.get(key), Mapping) and sources[key].get("status") == "ok" and sources[key].get("source_ref") == url
               for key, url in (("FOMC", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"),
                                ("ECB", "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html")))


def load_wire(path: str | Path, now: datetime | None = None) -> dict[str, Any]:
    checked = dt(now or datetime.now(UTC))
    try:
        target = Path(path)
        if target.stat().st_size > 8_000_000:
            return _empty(checked, "SNAPSHOT_TOO_LARGE")
        payload = json.loads(target.read_text(encoding="utf-8"))
        if validate_snapshot(payload, checked):
            # Consumer must compare as_of/check times to its own decision time;
            # don't mutate a signed producer snapshot to pretend it was refreshed.
            return payload
    except (OSError, ValueError, TypeError):
        pass
    return _empty(checked, "MISSING_OR_INVALID_SNAPSHOT")


def collect(*, now: datetime, previous: Mapping[str, Any] | None = None,
            calendar_events: Sequence[Mapping[str, Any]] = (), client: HttpClient | None = None,
            include_secondary: bool = False, calendar_coverage: Mapping[str, Any] | None = None) -> dict[str, Any]:
    checked = dt(now)
    if checked is None:
        raise ValueError("timezone-aware now required")
    client = client or HttpClient()
    previous = previous if previous and validate_snapshot(previous, checked) else _empty(checked)
    prior_events = copy.deepcopy(previous.get("events", []))
    event_by_id = {row["event_id"]: row for row in prior_events}
    previous_sources = {row["source_id"]: row for row in previous.get("sources", [])}
    sources = []
    fetched_documents = 0
    documents_per_bank = {"FED": 0, "ECB": 0}
    context_documents_per_bank = {"FED": 0, "ECB": 0}
    bank_document_limit = MAX_DOCUMENT_FETCHES // 2
    # Leave room for a decision and conference discovered in a later feed.
    bank_context_limit = max(0, bank_document_limit - 2)
    document_cache: dict[str, tuple[str | None, str | None, datetime]] = {}
    critical_kinds = {"FOMC_DECISION", "ECB_DECISION", "PRESS_CONFERENCE"}
    feeds = dict(OFFICIAL_RSS)
    if include_secondary:
        feeds["google_news"] = SECONDARY_RSS
    for source_id, url in feeds.items():
        old = previous_sources.get(source_id, {})
        headers = {}
        if old.get("etag"):
            headers["If-None-Match"] = old["etag"]
        if old.get("last_modified"):
            headers["If-Modified-Since"] = old["last_modified"]
        status = {"source_id": source_id, "url": url, "status": "ERROR", "checked_at": iso(checked),
                  "last_successful_check_at": old.get("last_successful_check_at"),
                  "last_item_published_at": old.get("last_item_published_at"),
                  "last_item_first_seen_at": old.get("last_item_first_seen_at"), "latency_seconds": None,
                  "error": None, "etag": old.get("etag"), "last_modified": old.get("last_modified")}
        status.update(document_fetch_count=0, document_cache_hit_count=0, document_budget_skip_count=0)
        try:
            response = client.get(url, headers=headers, max_bytes=MAX_XML_BYTES)
            received = dt(response.received_at) or checked
            checked = max(checked, received)
            status["checked_at"] = iso(received)
            if response.status == 304:
                if not old.get("last_successful_check_at"):
                    raise ValueError("304 without a verified prior response")
                status.update(status="OK", last_successful_check_at=iso(checked), not_modified=True)
                sources.append(status)
                continue
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}")
            canonical_url(response.url, secondary=source_id == "google_news")
            rows = parse_feed(response.body, source_id=source_id, now=received)
            # RSS order is not a priority guarantee. An older speech must not
            # consume the finite document budget ahead of a current decision.
            rows.sort(key=lambda row: (0 if classify(row["headline"], row["bank"]) in {"FOMC_DECISION", "ECB_DECISION"}
                                       else 1 if classify(row["headline"], row["bank"]) == "PRESS_CONFERENCE" else 2,
                                       -dt(row["published_at"]).timestamp(), row["source_url"]))
            status.update(status="OK", last_successful_check_at=iso(checked), not_modified=False,
                          etag=response.headers.get("ETag") or response.headers.get("etag"),
                          last_modified=response.headers.get("Last-Modified") or response.headers.get("last-modified"),
                          accepted_item_count=len(rows))
            for row in rows:
                secondary = source_id == "google_news"
                kind = classify(row["headline"], row["bank"])
                prior = [event for event in event_by_id.values() if event.get("source_url") == row["source_url"]]
                document = None
                document_error = None
                ingested = received
                # Recheck decision documents for corrections; reuse a known
                # document on a duplicate speech to avoid needless requests.
                needs_document = kind in {"FOMC_DECISION", "ECB_DECISION", "PRESS_CONFERENCE", "SPEECH", "INTERVIEW"}
                if not secondary and needs_document:
                    cached = document_cache.get(row["source_url"])
                    bank = row["bank"]
                    if cached is not None:
                        document, document_error, document_received = cached
                        ingested = max(received, document_received)
                        status["document_cache_hit_count"] += 1
                    elif fetched_documents >= MAX_DOCUMENT_FETCHES or documents_per_bank[bank] >= bank_document_limit:
                        document_error = "BANK_DOCUMENT_BUDGET_EXHAUSTED"
                        status["document_budget_skip_count"] += 1
                    elif kind not in critical_kinds and context_documents_per_bank[bank] >= bank_context_limit:
                        document_error = "CRITICAL_DOCUMENT_RESERVE_RETAINED"
                        status["document_budget_skip_count"] += 1
                    else:
                        fetched_documents += 1
                        documents_per_bank[bank] += 1
                        context_documents_per_bank[bank] += int(kind not in critical_kinds)
                        status["document_fetch_count"] += 1
                        try:
                            doc = client.get(row["source_url"], max_bytes=MAX_DOCUMENT_BYTES)
                            if doc.status != 200 or urllib.parse.urlsplit(doc.url).hostname.removeprefix("www.") != urllib.parse.urlsplit(row["source_url"]).hostname.removeprefix("www."):
                                raise ValueError("unverified document response")
                            document = doc.body.decode("utf-8", "replace")
                            ingested = dt(doc.received_at) or received
                            checked = max(checked, ingested)
                        except (OSError, ValueError, urllib.error.URLError) as error:
                            document_error = type(error).__name__
                        # Failures are cached too: duplicate feeds cannot turn
                        # one publication into repeated requests in this poll.
                        document_cache[row["source_url"]] = (document, document_error, ingested)
                source_class = "SECONDARY_HEADLINE" if secondary else "OFFICIAL_SPEECH" if kind in {"SPEECH", "INTERVIEW", "PRESS_CONFERENCE"} or "speech" in source_id else "OFFICIAL_STATEMENT"
                interpretation = interpret_document(bank=row["bank"], headline=row["headline"], source_url=row["source_url"], document=document, source_class=source_class)
                # Stable content fingerprint does not include ingestion time.
                content_hash = digest({"document_sha256": hashlib.sha256(document.encode()).hexdigest()}) if document else digest({"headline": row["headline"], "summary": row["summary"]})
                same = next((event for event in prior if event.get("content_sha256") == content_hash), None)
                if same:
                    continue
                # A temporary fetch outage cannot downgrade a previously verified
                # identical publication or masquerade as its correction.
                if prior and not document and any(event.get("document_sha256") for event in prior):
                    continue
                dedup_key = digest({"url": row["source_url"]})
                event_id = "cb:" + digest({"dedup_key": dedup_key, "content": content_hash})[:32]
                first_seen = min([event["first_seen_at"] for event in prior] + [iso(received)])
                event = {key: value for key, value in row.items() if key != "summary"}
                event.update(interpretation)
                event.update(event_id=event_id, source_class=source_class, first_seen_at=first_seen, ingested_at=iso(ingested),
                             version_first_seen_at=iso(received), content_sha256=content_hash,
                             document_sha256=hashlib.sha256(document.encode()).hexdigest() if document else None,
                             dedup_key=dedup_key, source_domain=urllib.parse.urlsplit(row["source_url"]).hostname,
                             related_calendar_event_id=_calendar_match(row, calendar_events),
                             supersedes_event_id=prior[-1]["event_id"] if prior else None,
                             publication_time_evidence="OFFICIAL_RSS" if not secondary else "SECONDARY_RSS",
                             document_error=document_error)
                event_by_id[event_id] = event
            if rows:
                latest = max(rows, key=lambda row: row["published_at"])
                matching = [event for event in event_by_id.values() if event["source_url"] == latest["source_url"]]
                status["last_item_published_at"] = latest["published_at"]
                status["last_item_first_seen_at"] = min((event["first_seen_at"] for event in matching), default=None)
                if status["last_item_first_seen_at"]:
                    status["latency_seconds"] = max(0, (dt(status["last_item_first_seen_at"]) - dt(latest["published_at"])).total_seconds())
        except (OSError, ValueError, ET.ParseError, urllib.error.URLError) as error:
            status["error"] = type(error).__name__
        sources.append(status)
    events = sorted(event_by_id.values(), key=lambda row: (row["ingested_at"], row["event_id"]))[-MAX_EVENTS:]
    primary = [source for source in sources if source["source_id"] != "google_news"]
    fresh = sum(source["status"] == "OK" for source in primary)
    payload = _empty(checked)
    calendar_ok = _calendar_coverage_valid(calendar_coverage, checked)
    calendar_window=event_window(calendar_events, events, checked)
    release_windows=_unscheduled_release_windows(calendar_events,events,checked)
    current_windows=[row for row in [calendar_window,*release_windows] if row.get('event_id')]
    unresolved,overflow=_unresolved_windows(previous,current_windows,checked)
    selected=release_windows[0] if release_windows else calendar_window
    if not selected.get('event_id') and unresolved:
        selected=max(unresolved,key=lambda row:(row['scheduled_at'],row['event_id']))
    status="FRESH" if fresh == len(primary) and calendar_ok else "PARTIAL" if fresh else "UNAVAILABLE"
    if overflow and status=='FRESH':status='PARTIAL'
    payload.update(sources=sources, events=events, event_window=selected,
                   unresolved_event_windows=unresolved,unresolved_event_windows_overflow=overflow,
                   calendar_coverage=dict(calendar_coverage or {"complete": False, "reason": "CALENDAR_NOT_CHECKED"}),
                   calendar_events=copy.deepcopy(list(calendar_events)),
                   overall_status=status,
                   health_reasons=([] if fresh == len(primary) else ["PRIMARY_SOURCE_FAILURE"]) + ([] if calendar_ok else ["CALENDAR_UNAVAILABLE"]) + (["UNRESOLVED_WINDOW_CAP_MANUAL_RECOVERY_REQUIRED"] if overflow else []),
                   retention={"snapshot_max_events": MAX_EVENTS, "journal_is_append_only": True},
                   document_budget={"maximum_requests": MAX_DOCUMENT_FETCHES, "maximum_requests_per_bank": bank_document_limit,
                                    "reserved_critical_slots_per_bank": bank_document_limit - bank_context_limit,
                                    "fetched_by_bank": documents_per_bank},
                   secondary_policy={"enabled": include_secondary, "coverage": "HEADLINES_ONLY_NO_ARTICLE_FETCH",
                                     "authority": "CONTEXT_ONLY", "publisher_attribution_verified": False})
    payload["integrity"].update(source_count=len(sources), fresh_source_count=fresh)
    payload["integrity"]["snapshot_sha256"] = snapshot_hash(payload)
    return payload


def official_actuals(snapshot: Mapping[str, Any], event: Mapping[str, Any], now: datetime) -> list[dict[str, Any]]:
    if not validate_snapshot(snapshot, now):
        return []
    scheduled = dt(event.get("event_at") or event.get("scheduled_at"))
    bank = _calendar_bank(event)
    if not scheduled or not bank or now < scheduled:
        return []
    rows = []
    for row in snapshot["events"]:
        published, ingested = dt(row["published_at"]), dt(row["ingested_at"])
        if row["bank"] != bank or not row.get("statement_verified") or row["event_type"] not in {"FOMC_DECISION", "ECB_DECISION"}:
            continue
        if published.date() != scheduled.date() or not scheduled - timedelta(hours=1) <= published <= now or not ingested <= now:
            continue
        requested_uid = event.get("uid") or event.get("event_id")
        if row.get("related_calendar_event_id") and requested_uid and requested_uid != row["related_calendar_event_id"]:
            continue
        rows.append({"metric": "fomc_policy_rate" if bank == "FED" else "ecb_policy_rates",
                     "value": row["facts"]["rate_actual"], "unit": "percent", "source": bank,
                     "source_ref": row["source_url"], "source_url": row["source_url"], "bank": bank,
                     "event_id": row["event_id"], "published_at": row["published_at"], "first_seen_at": row["first_seen_at"],
                     "ingested_at": row["ingested_at"], "wire_event_id": row["event_id"], "metadata": dict(row["facts"])})
    # A correction supersedes only prospectively. Earlier as-of snapshots retain
    # their original fact; no historical decision or price is rewritten.
    superseded = {row.get("supersedes_event_id") for row in snapshot["events"] if dt(row["ingested_at"]) <= now}
    rows = [row for row in rows if row["wire_event_id"] not in superseded]
    return sorted(rows, key=lambda row: (row["ingested_at"], row["wire_event_id"]))[-1:]


def _write_snapshot_locked(path: str | Path, snapshot: Mapping[str, Any], *, journal_dir: str | Path | None = None) -> bool:
    """Atomic monotonic snapshot plus immutable content-addressed receipt files."""
    if not validate_snapshot(snapshot):
        raise ValueError("invalid wire snapshot")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        previous = json.loads(target.read_text(encoding="utf-8"))
        if validate_snapshot(previous) and dt(previous["generated_at"]) >= dt(snapshot["generated_at"]):
            if previous["integrity"]["snapshot_sha256"] == snapshot["integrity"]["snapshot_sha256"]:
                return False
            raise ValueError("older or conflicting equal-time snapshot rejected")
    journal = Path(journal_dir) if journal_dir else target.parent / "eurusd_cb_wire_journal"
    journal.mkdir(parents=True, exist_ok=True)
    receipt = journal / (snapshot["integrity"]["snapshot_sha256"] + ".json")
    body = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    try:
        with receipt.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
    except FileExistsError:
        if receipt.read_text(encoding="utf-8") != body:
            raise ValueError("immutable receipt conflict")
    fd, name = tempfile.mkstemp(prefix=".eurusd-cb-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return True


def write_snapshot(path: str | Path, snapshot: Mapping[str, Any], *, journal_dir: str | Path | None = None) -> bool:
    """Serialize the monotonic check and replace against concurrent collectors."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_name(target.name + ".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError("wire writer already active; retry after refreshing latest snapshot") from error
    try:
        os.close(descriptor)
        return _write_snapshot_locked(target, snapshot, journal_dir=journal_dir)
    finally:
        lock.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now", help="Timezone-aware ISO timestamp; HTTP still represents receipt now")
    parser.add_argument("--calendar", type=Path, help="Existing sourced calendar JSON list or {events:[...]}")
    parser.add_argument("--secondary", action="store_true", help="Public Google RSS headlines only; caller must authorize terms of use")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    now = dt(args.now) if args.now else datetime.now(UTC)
    if now is None:
        parser.error("--now must include timezone")
    # An injected past --now cannot turn a current HTTP response into historical
    # knowledge. Deterministic as-of replay uses mocked HTTP in tests instead.
    if args.now and abs((datetime.now(UTC) - now).total_seconds()) > 60:
        parser.error("live collection --now must be within 60 seconds of actual receipt time")
    previous = load_wire(args.output, now)
    calendar: Any = []
    coverage: Mapping[str, Any] | None = None
    if args.calendar:
        calendar = json.loads(args.calendar.read_text(encoding="utf-8"))
        if isinstance(calendar, Mapping):
            coverage = calendar.get("calendar_coverage")
            calendar = calendar.get("calendar_events", calendar.get("events", []))
    else:
        calendar, coverage = collect_calendars(now=now, previous=previous)
        now = max(now, dt(coverage["checked_at"]))
    snapshot = collect(now=now, previous=previous, calendar_events=calendar, calendar_coverage=coverage, include_secondary=args.secondary)
    if not args.dry_run:
        write_snapshot(args.output, snapshot)
    print(json.dumps({"status": snapshot["overall_status"], "sources": snapshot["integrity"]["source_count"],
                      "fresh_sources": snapshot["integrity"]["fresh_source_count"], "events": len(snapshot["events"]),
                      "snapshot_sha256": snapshot["integrity"]["snapshot_sha256"], "dry_run": args.dry_run}))
    return 0 if snapshot["integrity"]["fresh_source_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
