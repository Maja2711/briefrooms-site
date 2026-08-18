from __future__ import annotations

import html
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from belief_adapter_contract import AdapterResult, EvidenceAssessment, Observation, clamp, observation_to_evidence, stable_id
from belief_core import iso_z
from belief_news_event_adapter import HttpClient

NY = ZoneInfo("America/New_York")

BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
BEA_SCHEDULE = "https://www.bea.gov/news/schedule"
FOMC_CALENDAR = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

HIGH_IMPACT_TERMS = (
    "consumer price index",
    "employment situation",
    "fomc",
    "federal open market committee",
    "gross domestic product",
    "personal income and outlays",
    "pce",
)
MEDIUM_IMPACT_TERMS = (
    "producer price index",
    "job openings",
    "jolts",
    "employment cost index",
    "productivity and costs",
    "international trade",
)

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}


@dataclass(frozen=True)
class CalendarEvent:
    source: str
    source_ref: str
    uid: str
    title: str
    event_at: datetime
    time_precision: str
    importance: str
    metadata: Mapping[str, Any]


def _importance(title: str) -> str:
    text = title.lower()
    if any(term in text for term in HIGH_IMPACT_TERMS):
        return "high"
    if any(term in text for term in MEDIUM_IMPACT_TERMS):
        return "medium"
    return "low"


def _unfold_ics(text: str) -> List[str]:
    lines: List[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _parse_ics_dt(value: str) -> Optional[datetime]:
    text = value.strip()
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d").replace(tzinfo=NY)
    if text.endswith("Z"):
        try:
            return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=NY)
        except ValueError:
            pass
    return None


def parse_bls_ics(text: str, *, now: datetime, horizon_days: int = 45) -> List[CalendarEvent]:
    events: List[CalendarEvent] = []
    current: Dict[str, str] = {}
    in_event = False
    for line in _unfold_ics(text):
        if line == "BEGIN:VEVENT":
            current = {}
            in_event = True
            continue
        if line == "END:VEVENT":
            if in_event:
                title = current.get("SUMMARY", "").strip()
                dt_raw = current.get("DTSTART", "")
                event_at = _parse_ics_dt(dt_raw)
                if title and event_at is not None:
                    event_at = event_at.astimezone(NY)
                    delta = event_at - now.astimezone(NY)
                    if -timedelta(days=1) <= delta <= timedelta(days=horizon_days):
                        uid = current.get("UID") or f"bls:{event_at.date()}:{title}"
                        url = current.get("URL") or BLS_ICS
                        events.append(
                            CalendarEvent(
                                source="U.S. Bureau of Labor Statistics",
                                source_ref=url,
                                uid=uid,
                                title=html.unescape(title),
                                event_at=event_at,
                                time_precision="exact" if "T" in dt_raw else "date_only",
                                importance=_importance(title),
                                metadata={"calendar_source": BLS_ICS},
                            )
                        )
            current = {}
            in_event = False
            continue
        if not in_event or ":" not in line:
            continue
        key_part, value = line.split(":", 1)
        key = key_part.split(";", 1)[0].upper()
        if key in {"SUMMARY", "DTSTART", "UID", "URL", "DESCRIPTION"}:
            current[key] = value.strip()
    return events


class _ScheduleTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: List[List[Tuple[str, str]]] = []
        self._in_row = False
        self._in_cell = False
        self._href = ""
        self._parts: List[str] = []
        self._row: List[Tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._in_row = True
            self._row = []
        elif tag in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._parts = []
            self._href = ""
        elif tag == "a" and self._in_cell:
            self._href = dict(attrs).get("href", "")

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            value = " ".join(data.split())
            if value:
                self._parts.append(value)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell:
            self._row.append((" ".join(self._parts).strip(), self._href))
            self._in_cell = False
            self._parts = []
            self._href = ""
        elif tag == "tr" and self._in_row:
            if self._row:
                self.rows.append(self._row)
            self._in_row = False
            self._row = []


def parse_bea_schedule(text: str, *, now: datetime, horizon_days: int = 60) -> List[CalendarEvent]:
    parser = _ScheduleTableParser()
    parser.feed(text)
    events: List[CalendarEvent] = []
    year = now.astimezone(NY).year
    date_re = re.compile(
        r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
        r"(\d{1,2})\s+(\d{1,2}):(\d{2})\s+(AM|PM)$",
        re.I,
    )
    for row in parser.rows:
        cells = [cell[0] for cell in row]
        if len(cells) < 2:
            continue
        date_cell = cells[0]
        match = date_re.match(date_cell)
        if not match:
            continue
        month_name, day, hour, minute, ap = match.groups()
        hour_i = int(hour)
        if ap.upper() == "PM" and hour_i != 12:
            hour_i += 12
        if ap.upper() == "AM" and hour_i == 12:
            hour_i = 0
        event_at = datetime(
            year, MONTHS[month_name.capitalize()], int(day), hour_i, int(minute), tzinfo=NY
        )
        if event_at < now.astimezone(NY) - timedelta(days=1):
            if event_at.month < now.astimezone(NY).month:
                try:
                    event_at = event_at.replace(year=year + 1)
                except ValueError:
                    pass
        delta = event_at - now.astimezone(NY)
        if not (-timedelta(days=1) <= delta <= timedelta(days=horizon_days)):
            continue

        title = ""
        href = ""
        for text_value, link in row[1:]:
            if text_value.lower() in {"news", "data", "visual data", "article", "n", "d", "v", "a"}:
                continue
            if len(text_value) > len(title):
                title = text_value
                href = link
        if not title:
            continue
        source_ref = urllib.parse.urljoin("https://www.bea.gov", href) if href else BEA_SCHEDULE
        uid = f"bea:{event_at.isoformat()}:{title}"
        events.append(
            CalendarEvent(
                source="U.S. Bureau of Economic Analysis",
                source_ref=source_ref,
                uid=uid,
                title=html.unescape(title),
                event_at=event_at,
                time_precision="exact",
                importance=_importance(title),
                metadata={"calendar_source": BEA_SCHEDULE},
            )
        )
    return events


def _strip_html(text: str) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return html.unescape(re.sub(r"\s+", " ", value)).strip()


def parse_fomc_calendar(text: str, *, now: datetime, horizon_days: int = 240) -> List[CalendarEvent]:
    clean = _strip_html(text)
    year = now.astimezone(NY).year
    marker = f"{year} FOMC Meetings"
    pos = clean.find(marker)
    if pos < 0:
        return []
    tail = clean[pos + len(marker):]
    next_marker = re.search(rf"{year + 1}\s+FOMC Meetings", tail)
    if next_marker:
        tail = tail[:next_marker.start()]

    pattern = re.compile(
        r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:-(\d{1,2}))?\*?\b"
    )
    events: List[CalendarEvent] = []
    for match in pattern.finditer(tail):
        month, start_day, end_day = match.groups()
        decision_day = int(end_day or start_day)
        try:
            event_date = datetime(year, MONTHS[month], decision_day, 14, 0, tzinfo=NY)
        except ValueError:
            continue
        delta = event_date - now.astimezone(NY)
        if not (-timedelta(days=1) <= delta <= timedelta(days=horizon_days)):
            continue
        events.append(
            CalendarEvent(
                source="Federal Open Market Committee",
                source_ref=FOMC_CALENDAR,
                uid=f"fomc:{event_date.date().isoformat()}",
                title=f"FOMC meeting decision window — {month} {start_day}-{end_day or start_day}",
                event_at=event_date,
                time_precision="date_only",
                importance="high",
                metadata={
                    "calendar_source": FOMC_CALENDAR,
                    "decision_time_note": "calendar provides meeting dates; 14:00 ET is a scheduling anchor, not a sourced timestamp",
                },
            )
        )
    return events


def calendar_event_to_observation(event: CalendarEvent, now: datetime) -> Observation:
    hours_until = (event.event_at - now.astimezone(event.event_at.tzinfo)).total_seconds() / 3600.0
    observed_at = iso_z(now)
    event_at = iso_z(event.event_at.astimezone(timezone.utc))
    return Observation(
        observation_id=stable_id("obs-calendar", event.uid, event_at),
        adapter="macro_event_calendar",
        metric="scheduled_macro_event",
        entity="US_MACRO",
        observed_at=observed_at,
        value=round(hours_until, 4),
        unit="hours_until_event",
        source=event.source,
        source_type="primary",
        source_ref=event.source_ref,
        reliability=.98,
        independence_cluster=f"calendar:{event.uid}",
        status="ok",
        tags=("macro_calendar", event.importance),
        metadata={
            "uid": event.uid,
            "title": event.title,
            "event_at": event_at,
            "time_precision": event.time_precision,
            "importance": event.importance,
            **dict(event.metadata),
        },
    )


def event_risk_evidence(primary: Observation, *, now: datetime):
    importance = str(primary.metadata.get("importance") or "low")
    event_at_raw = primary.metadata.get("event_at")
    if importance != "high" or not event_at_raw:
        return ()
    try:
        event_at = datetime.fromisoformat(str(event_at_raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return ()
    hours = (event_at - now.astimezone(timezone.utc)).total_seconds() / 3600.0

    time_precision = str(primary.metadata.get("time_precision") or "")
    eligible = 0.0 <= hours <= 6.0
    if time_precision == "date_only":
        eligible = event_at.astimezone(NY).date() == now.astimezone(NY).date()
    if not eligible:
        return ()

    proximity = 1.0 if time_precision == "date_only" else clamp(1.0 - hours / 6.0)
    strength = 0.22 + 0.28 * proximity
    derived = Observation.make(
        adapter="macro_event_calendar",
        metric="scheduled_event_risk",
        entity="SPX",
        observed_at=iso_z(now),
        value={
            "event_uid": primary.metadata.get("uid"),
            "event_title": primary.metadata.get("title"),
            "hours_until": round(hours, 4),
        },
        unit="risk_proxy",
        source=f"Deterministic event-risk transform of {primary.source}",
        source_type="derived",
        source_ref=f"derived:{primary.observation_id}",
        reliability=primary.reliability,
        independence_cluster=primary.independence_cluster,
        tags=("macro_event_risk", "deterministic"),
        metadata={
            "upstream_observation_id": primary.observation_id,
            "primary_source_ref": primary.source_ref,
            "importance": importance,
            "time_precision": time_precision,
        },
    )
    evidence = observation_to_evidence(
        derived,
        EvidenceAssessment(
            belief_id="spx.volatility.benign",
            direction=-1,
            strength=strength,
            evidence_type="scheduled_macro_event_risk",
            note=f"High-impact scheduled event is imminent: {primary.metadata.get('title')}",
            independence_cluster=primary.independence_cluster,
            metadata={
                "primary_observation_id": primary.observation_id,
                "primary_source_ref": primary.source_ref,
                "importance": importance,
            },
        ),
    )
    return derived, evidence


class MacroEventCalendarAdapter:
    name = "macro_event_calendar"
    version = "1.0.0"

    def __init__(self, *, client: Optional[HttpClient] = None) -> None:
        self.client = client or HttpClient()

    def collect_events(self, now: datetime) -> List[CalendarEvent]:
        events: List[CalendarEvent] = []
        try:
            events.extend(parse_bls_ics(self.client.text(BLS_ICS), now=now))
        except Exception:
            pass
        try:
            events.extend(parse_bea_schedule(self.client.text(BEA_SCHEDULE), now=now))
        except Exception:
            pass
        try:
            events.extend(parse_fomc_calendar(self.client.text(FOMC_CALENDAR), now=now))
        except Exception:
            pass
        dedup: Dict[str, CalendarEvent] = {}
        for event in events:
            dedup[event.uid] = event
        return sorted(dedup.values(), key=lambda x: x.event_at)

    def run(self, now: datetime) -> AdapterResult:
        observations: List[Observation] = []
        evidence = []
        for event in self.collect_events(now):
            primary = calendar_event_to_observation(event, now)
            observations.append(primary)
            risk = event_risk_evidence(primary, now=now)
            if risk:
                derived, ev = risk
                observations.append(derived)
                evidence.append(ev)
        return AdapterResult(self.name, tuple(observations), tuple(evidence))
