from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from email.utils import parsedate_to_datetime

from belief_adapter_contract import AdapterResult, Observation
from belief_core import iso_z, parse_time
from belief_llm_interpreter import GeminiEvidenceInterpreter

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_PATH = ROOT / "data" / "investments" / "portfolio_10k_usd.json"

FED_FEEDS = (
    ("Federal Reserve press releases", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("Federal Reserve speeches and testimony", "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml"),
)
BLS_FEEDS = (
    ("BLS latest numbers", "https://www.bls.gov/feed/bls_latest.rss"),
)
BEA_CURRENT_RELEASES = "https://www.bea.gov/news/current-releases"
SEC_TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_FORMS = frozenset({"8-K", "10-Q", "10-K", "6-K", "20-F", "40-F"})
DEFAULT_LOOKBACK_HOURS = 36
MAX_DOCUMENT_CHARS = 16000


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)


def html_to_text(payload: str, limit: int = MAX_DOCUMENT_CHARS) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(payload)
        text = " ".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", payload)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]


@dataclass(frozen=True)
class SourceDocument:
    source: str
    source_ref: str
    title: str
    published_at: str
    entity: str
    document_text: str
    category_hint: str
    reliability: float
    metadata: Mapping[str, Any]


class HttpClient:
    def __init__(self, timeout: int = 15, user_agent: Optional[str] = None) -> None:
        self.timeout = timeout
        self.user_agent = user_agent or os.getenv(
            "BELIEF_HTTP_USER_AGENT",
            "BriefRooms-BeliefCore/1.0 research https://briefrooms.com",
        )

    def _request(self, url: str, *, accept: str) -> bytes:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": accept,
                "Accept-Encoding": "identity",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            return response.read()

    def text(self, url: str) -> str:
        return self._request(url, accept="text/html,application/xml,text/xml,text/plain;q=0.9").decode("utf-8", "replace")

    def json(self, url: str) -> Mapping[str, Any]:
        return json.loads(self._request(url, accept="application/json").decode("utf-8"))

    def sec_text(self, url: str) -> str:
        ua = os.getenv("SEC_USER_AGENT", "").strip() or self.user_agent
        req = urllib.request.Request(
            url,
            headers={"User-Agent": ua, "Accept": "text/html,application/json,text/plain;q=0.9"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            return response.read().decode("utf-8", "replace")

    def sec_json(self, url: str) -> Mapping[str, Any]:
        return json.loads(self.sec_text(url))


def _parse_date(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        return parse_time(text)
    except Exception:
        return None


def parse_rss(xml_text: str, *, source: str, now: datetime, lookback_hours: int) -> List[SourceDocument]:
    root = ET.fromstring(xml_text)
    out: List[SourceDocument] = []
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=lookback_hours)
    seen = set()

    items = list(root.findall(".//item"))
    if not items:
        items = list(root.findall(".//{*}entry"))

    for item in items:
        def first_text(names: Sequence[str]) -> str:
            for name in names:
                node = item.find(name)
                if node is None:
                    node = item.find(f"{{*}}{name}")
                if node is not None and (node.text or "").strip():
                    return (node.text or "").strip()
            return ""

        title = first_text(("title",))
        link = first_text(("link",))
        if not link:
            link_node = item.find("{*}link")
            if link_node is not None:
                link = str(link_node.attrib.get("href") or "")
        published_raw = first_text(("pubDate", "published", "updated", "date"))
        published = _parse_date(published_raw) or now.astimezone(timezone.utc)
        if published < cutoff:
            continue
        description = first_text(("description", "summary", "content"))
        description = html_to_text(description, 5000)
        link = html.unescape(link.strip())
        if not link or link in seen:
            continue
        seen.add(link)
        hint = "fed_speech" if "speech" in source.lower() or "testimony" in source.lower() else "other"
        out.append(
            SourceDocument(
                source=source,
                source_ref=link,
                title=html.unescape(title),
                published_at=iso_z(published),
                entity="FED" if "Federal Reserve" in source else "US_MACRO",
                document_text=description or title,
                category_hint=hint,
                reliability=.98,
                metadata={"feed_published_at": iso_z(published)},
            )
        )
    return out


class _BEAReleaseParser(HTMLParser):
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
            self._href = ""
            self._parts = []
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


def parse_bea_current_releases(html_text: str, *, now: datetime, lookback_hours: int) -> List[SourceDocument]:
    parser = _BEAReleaseParser()
    parser.feed(html_text)
    out: List[SourceDocument] = []
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=lookback_hours)
    for row in parser.rows:
        if len(row) < 2:
            continue
        title = ""
        href = ""
        published: Optional[datetime] = None
        for text_value, link in row:
            if link and "/news/" in link and text_value:
                title = text_value
                href = link
            if published is None and text_value:
                for fmt in ("%B %d, %Y", "%b %d, %Y"):
                    try:
                        published = datetime.strptime(text_value, fmt).replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        pass
        if not title or not href or published is None or published < cutoff:
            continue
        absolute = urllib.parse.urljoin("https://www.bea.gov", href)
        out.append(
            SourceDocument(
                source="U.S. Bureau of Economic Analysis",
                source_ref=absolute,
                title=title,
                published_at=iso_z(published),
                entity="US_MACRO",
                document_text=title,
                category_hint="macro_release",
                reliability=.98,
                metadata={"listing_source": BEA_CURRENT_RELEASES, "published_date": published.date().isoformat()},
            )
        )
    dedup: Dict[str, SourceDocument] = {}
    for doc in out:
        dedup[doc.source_ref] = doc
    return list(dedup.values())[:12]


def _watch_tickers() -> Tuple[str, ...]:
    tickers = set()
    env = os.getenv("BELIEF_EVENT_TICKERS", "")
    for value in env.split(","):
        ticker = value.strip().upper()
        if ticker:
            tickers.add(ticker)
    try:
        payload = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
        for position in payload.get("positions") or []:
            if str(position.get("asset_type") or "").lower() != "stock":
                continue
            symbol = str(position.get("market_symbol") or "").upper()
            if symbol.endswith(".US"):
                symbol = symbol[:-3]
            if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
                tickers.add(symbol)
    except Exception:
        pass
    return tuple(sorted(tickers))


def _sec_ticker_index(payload: Mapping[str, Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in payload.values():
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("ticker") or "").upper()
        try:
            cik = int(row.get("cik_str"))
        except (TypeError, ValueError):
            continue
        if ticker:
            out[ticker] = cik
    return out


def _sec_documents(
    client: HttpClient,
    *,
    now: datetime,
    lookback_hours: int,
    tickers: Sequence[str],
) -> List[SourceDocument]:
    if not tickers:
        return []
    try:
        index = _sec_ticker_index(client.sec_json(SEC_TICKER_MAP))
    except Exception:
        return []
    cutoff_date = (now.astimezone(timezone.utc) - timedelta(hours=lookback_hours)).date()
    out: List[SourceDocument] = []

    for ticker in tickers:
        cik = index.get(ticker)
        if not cik:
            continue
        try:
            payload = client.sec_json(SEC_SUBMISSIONS.format(cik=cik))
        except Exception:
            continue
        recent = ((payload.get("filings") or {}).get("recent") or {})
        forms = recent.get("form") or []
        accessions = recent.get("accessionNumber") or []
        dates = recent.get("filingDate") or []
        docs = recent.get("primaryDocument") or []
        descriptions = recent.get("primaryDocDescription") or []
        for form, accession, filing_date, primary_doc, description in zip(
            forms, accessions, dates, docs, descriptions
        ):
            if str(form) not in SEC_FORMS:
                continue
            try:
                date_value = datetime.fromisoformat(str(filing_date)).date()
            except ValueError:
                continue
            if date_value < cutoff_date:
                continue
            accession_clean = str(accession).replace("-", "")
            archive = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession_clean}/{primary_doc}"
            )
            document_text = f"{ticker} {form} {description}".strip()
            try:
                fetched = client.sec_text(archive)
                document_text = html_to_text(fetched)
            except Exception:
                pass
            published = datetime.combine(date_value, datetime.min.time(), tzinfo=timezone.utc)
            out.append(
                SourceDocument(
                    source="SEC EDGAR",
                    source_ref=archive,
                    title=f"{ticker} {form}: {description or primary_doc}",
                    published_at=iso_z(published),
                    entity=ticker,
                    document_text=document_text,
                    category_hint="sec_filing",
                    reliability=.99,
                    metadata={
                        "ticker": ticker,
                        "cik": cik,
                        "form": str(form),
                        "accession_number": str(accession),
                        "filing_date": str(filing_date),
                        "primary_document": str(primary_doc),
                    },
                )
            )
    return out


def document_to_observation(document: SourceDocument) -> Observation:
    return Observation.make(
        adapter="news_event",
        metric="primary_event_document",
        entity=document.entity,
        observed_at=document.published_at,
        value=document.title,
        unit="text_event",
        source=document.source,
        source_type="primary",
        source_ref=document.source_ref,
        reliability=document.reliability,
        independence_cluster=f"primary-event:{document.source_ref}",
        tags=("news_event", "primary_source", document.category_hint),
        metadata={
            "title": document.title,
            "document_text": document.document_text[:MAX_DOCUMENT_CHARS],
            "category_hint": document.category_hint,
            **dict(document.metadata),
        },
    )


class NewsEventAdapter:
    name = "news_event"
    version = "1.0.0"

    def __init__(
        self,
        *,
        client: Optional[HttpClient] = None,
        interpreter: Optional[GeminiEvidenceInterpreter] = None,
        lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
        enable_sec: Optional[bool] = None,
    ) -> None:
        self.client = client or HttpClient()
        self.interpreter = interpreter
        self.lookback_hours = int(lookback_hours)
        self.enable_sec = (
            bool(enable_sec)
            if enable_sec is not None
            else os.getenv("BELIEF_SEC_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
        )

    def collect_documents(self, now: datetime) -> List[SourceDocument]:
        documents: List[SourceDocument] = []
        for source, url in FED_FEEDS + BLS_FEEDS:
            try:
                documents.extend(
                    parse_rss(
                        self.client.text(url),
                        source=source,
                        now=now,
                        lookback_hours=self.lookback_hours,
                    )
                )
            except Exception:
                continue

        try:
            documents.extend(
                parse_bea_current_releases(
                    self.client.text(BEA_CURRENT_RELEASES),
                    now=now,
                    lookback_hours=self.lookback_hours,
                )
            )
        except Exception:
            pass

        if self.enable_sec:
            documents.extend(
                _sec_documents(
                    self.client,
                    now=now,
                    lookback_hours=self.lookback_hours,
                    tickers=_watch_tickers(),
                )
            )

        enriched: List[SourceDocument] = []
        allowed_hosts = {"www.federalreserve.gov", "federalreserve.gov", "www.bls.gov", "bls.gov", "www.bea.gov", "bea.gov"}
        for doc in documents:
            parsed = urllib.parse.urlparse(doc.source_ref)
            text = doc.document_text
            if parsed.hostname in allowed_hosts:
                try:
                    fetched = self.client.text(doc.source_ref)
                    extracted = html_to_text(fetched)
                    if len(extracted) >= 80:
                        text = extracted
                except Exception:
                    pass
            enriched.append(
                SourceDocument(
                    source=doc.source,
                    source_ref=doc.source_ref,
                    title=doc.title,
                    published_at=doc.published_at,
                    entity=doc.entity,
                    document_text=text,
                    category_hint=doc.category_hint,
                    reliability=doc.reliability,
                    metadata=doc.metadata,
                )
            )

        dedup: Dict[str, SourceDocument] = {}
        for doc in enriched:
            dedup[doc.source_ref] = doc
        return list(dedup.values())

    def run(
        self,
        now: datetime,
        *,
        seen_primary_observation_ids: Sequence[str] = (),
    ) -> AdapterResult:
        seen = set(seen_primary_observation_ids)
        observations: List[Observation] = []
        evidence = []

        for document in self.collect_documents(now):
            primary = document_to_observation(document)
            observations.append(primary)
            if primary.observation_id in seen:
                continue
            if self.interpreter is None or not self.interpreter.available:
                continue
            result = self.interpreter.interpret(primary)
            if result is None:
                continue
            observations.append(result.observation)
            evidence.append(result.evidence)

        return AdapterResult(self.name, tuple(observations), tuple(evidence))
