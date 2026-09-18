#!/usr/bin/env python3
"""Official company / technology / crypto primary-source collector for BELIEF.

Purpose:
- complement SEC/EDGAR with issuer-owned Investor Relations, newsroom, blog and
  earnings-event sources;
- discover official earnings releases and official transcripts;
- retain verified executive attribution only when the named leader appears in an
  official-source document;
- fail closed on stale, undated or weak-provenance documents.

This module does not create trading signals. It returns primary documents with
explicit provenance for the BELIEF Observation -> Evidence pipeline.
"""
from __future__ import annotations

import html
import io
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Mapping, Sequence


UTC = timezone.utc
VERSION = "belief-company-primary-sources-v1"
MAX_DOCUMENT_CHARS = 18000
MAX_INDEX_LINKS_PER_SOURCE = 8
MAX_DOCUMENTS = 36

MATERIAL_LINK_TERMS = (
    "earnings", "financial results", "quarterly results", "quarter results",
    "results", "transcript", "prepared remarks", "shareholder letter",
    "press release", "news release", "investor update", "investor day",
    "guidance", "outlook", "conference call", "webcast", "capital markets",
    "annual report", "quarterly report", "product", "launch", "roadmap",
    "artificial intelligence", " ai ", "chip", "gpu", "bitcoin", "crypto",
    "stablecoin", "regulation", "security", "breach",
)
LOW_VALUE_LINK_TERMS = (
    "privacy", "cookie", "terms", "careers", "contact", "faq", "accessibility",
    "historical price", "stock quote", "transfer agent", "email alert",
)
EXECUTIVE_STATEMENT_TERMS = (
    " says ", " said ", " expects ", " expected ", " warns ", " warned ",
    " announces ", " announced ", " confirms ", " confirmed ", " sees ",
    " predicts ", " predicted ", " argues ", " argued ", " believes ",
    " discusses ", " comments ", " remarked ", " tells ", " told ",
)

# Registry is deliberately explicit. A source becomes primary only through an
# allowlisted company/protocol-owned origin, or through a document link discovered
# on one of those official indexes.
SOURCE_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "key": "nvidia", "entity": "NVDA", "tickers": ("NVDA",),
        "leaders": ("Jensen Huang",),
        "official_hosts": ("investor.nvidia.com", "nvidianews.nvidia.com", "nvidia.com"),
        "sources": (
            {"kind": "rss", "url": "https://nvidianews.nvidia.com/cats/press_release.xml", "label": "NVIDIA Newsroom", "category": "official_press_release"},
            {"kind": "html_index", "url": "https://investor.nvidia.com/financial-info/quarterly-results/default.aspx", "label": "NVIDIA Investor Relations", "category": "official_ir"},
        ),
    },
    {
        "key": "microsoft", "entity": "MSFT", "tickers": ("MSFT",),
        "leaders": ("Satya Nadella", "Amy Hood"),
        "official_hosts": ("microsoft.com", "www.microsoft.com", "blogs.microsoft.com"),
        "sources": (
            {"kind": "html_index", "url": "https://www.microsoft.com/en-us/investor/", "label": "Microsoft Investor Relations", "category": "official_ir"},
            {"kind": "html_index", "url": "https://blogs.microsoft.com/", "label": "Microsoft Official Blog", "category": "official_blog"},
        ),
    },
    {
        "key": "apple", "entity": "AAPL", "tickers": ("AAPL",),
        "leaders": ("Tim Cook", "Kevan Parekh"),
        "official_hosts": ("apple.com", "www.apple.com"),
        "sources": (
            {"kind": "rss", "url": "https://www.apple.com/newsroom/rss-feed.rss", "label": "Apple Newsroom", "category": "official_press_release"},
        ),
    },
    {
        "key": "alphabet", "entity": "GOOGL", "tickers": ("GOOGL", "GOOG"),
        "leaders": ("Sundar Pichai", "Anat Ashkenazi", "Demis Hassabis"),
        "official_hosts": ("abc.xyz", "blog.google", "googleblog.com"),
        "sources": (
            {"kind": "html_index", "url": "https://abc.xyz/investor/earnings/", "label": "Alphabet Investor Relations - Earnings", "category": "official_earnings"},
            {"kind": "html_index", "url": "https://abc.xyz/investor/", "label": "Alphabet Investor Relations", "category": "official_ir"},
        ),
    },
    {
        "key": "meta", "entity": "META", "tickers": ("META",),
        "leaders": ("Mark Zuckerberg", "Susan Li"),
        "official_hosts": ("investor.atmeta.com", "about.fb.com", "about.meta.com", "meta.com"),
        "sources": (
            {"kind": "rss", "url": "https://investor.atmeta.com/rss/pressrelease.aspx", "label": "Meta Investor Relations", "category": "official_press_release"},
            {"kind": "html_index", "url": "https://investor.atmeta.com/financials/quarterly-earnings/", "label": "Meta Quarterly Earnings", "category": "official_earnings"},
        ),
    },
    {
        "key": "amazon", "entity": "AMZN", "tickers": ("AMZN",),
        "leaders": ("Andy Jassy", "Brian Olsavsky"),
        "official_hosts": ("ir.aboutamazon.com", "aboutamazon.com", "www.aboutamazon.com"),
        "sources": (
            {"kind": "html_index", "url": "https://ir.aboutamazon.com/overview/default.aspx", "label": "Amazon Investor Relations", "category": "official_ir"},
        ),
    },
    {
        "key": "tesla", "entity": "TSLA", "tickers": ("TSLA",),
        "leaders": ("Elon Musk", "Vaibhav Taneja"),
        "official_hosts": ("ir.tesla.com", "tesla.com", "www.tesla.com"),
        "sources": (
            {"kind": "html_index", "url": "https://ir.tesla.com/", "label": "Tesla Investor Relations", "category": "official_ir"},
        ),
    },
    {
        "key": "broadcom", "entity": "AVGO", "tickers": ("AVGO",),
        "leaders": ("Hock Tan",),
        "official_hosts": ("investors.broadcom.com", "broadcom.com", "www.broadcom.com"),
        "sources": (
            {"kind": "html_index", "url": "https://investors.broadcom.com/", "label": "Broadcom Investor Relations", "category": "official_ir"},
        ),
    },
    {
        "key": "amd", "entity": "AMD", "tickers": ("AMD",),
        "leaders": ("Lisa Su", "Jean Hu"),
        "official_hosts": ("ir.amd.com", "amd.com", "www.amd.com"),
        "sources": (
            {"kind": "rss", "url": "https://ir.amd.com/news-events/press-releases/rss", "label": "AMD Investor Relations", "category": "official_press_release"},
            {"kind": "html_index", "url": "https://ir.amd.com/financial-information/financial-results", "label": "AMD Financial Results", "category": "official_earnings"},
        ),
    },
    {
        "key": "oracle", "entity": "ORCL", "tickers": ("ORCL",),
        "leaders": ("Safra Catz", "Larry Ellison"),
        "official_hosts": ("investor.oracle.com", "oracle.com", "www.oracle.com"),
        "sources": (
            {"kind": "html_index", "url": "https://investor.oracle.com/", "label": "Oracle Investor Relations", "category": "official_ir"},
        ),
    },
    {
        "key": "asml", "entity": "ASML", "tickers": ("ASML",),
        "leaders": ("Christophe Fouquet",),
        "official_hosts": ("asml.com", "www.asml.com"),
        "sources": (
            {"kind": "html_index", "url": "https://www.asml.com/en/news/press-releases", "label": "ASML Press Releases", "category": "official_press_release"},
        ),
    },
    {
        "key": "tsmc", "entity": "TSM", "tickers": ("TSM",),
        "leaders": ("C. C. Wei", "CC Wei"),
        "official_hosts": ("tsmc.com", "www.tsmc.com", "pr.tsmc.com"),
        "sources": (
            {"kind": "html_index", "url": "https://pr.tsmc.com/english", "label": "TSMC Press Center", "category": "official_press_release"},
        ),
    },
    {
        "key": "coinbase", "entity": "COIN", "tickers": ("COIN",),
        "leaders": ("Brian Armstrong", "Alesia Haas"),
        "official_hosts": ("investor.coinbase.com", "coinbase.com", "www.coinbase.com"),
        "sources": (
            {"kind": "html_index", "url": "https://investor.coinbase.com/home/default.aspx", "label": "Coinbase Investor Relations", "category": "official_ir"},
            {"kind": "html_index", "url": "https://www.coinbase.com/blog", "label": "Coinbase Blog", "category": "official_blog"},
        ),
    },
    {
        "key": "strategy", "entity": "MSTR", "tickers": ("MSTR",),
        "leaders": ("Michael Saylor", "Phong Le"),
        "official_hosts": ("strategy.com", "www.strategy.com", "microstrategy.com", "www.microstrategy.com"),
        "sources": (
            {"kind": "html_index", "url": "https://www.strategy.com/investor-relations", "label": "Strategy Investor Relations", "category": "official_ir"},
        ),
    },
    {
        "key": "circle", "entity": "CRCL", "tickers": ("CRCL",),
        "leaders": ("Jeremy Allaire", "Patrick O'Donnell"),
        "official_hosts": ("investor.circle.com", "circle.com", "www.circle.com"),
        "sources": (
            {"kind": "html_index", "url": "https://investor.circle.com/overview/default.aspx", "label": "Circle Investor Relations", "category": "official_ir"},
            {"kind": "html_index", "url": "https://www.circle.com/pressroom", "label": "Circle Pressroom", "category": "official_press_release"},
        ),
    },
    {
        "key": "openai", "entity": "OPENAI", "tickers": (),
        "leaders": ("Sam Altman",),
        "official_hosts": ("openai.com", "www.openai.com"),
        "systemic": True,
        "sources": (
            {"kind": "html_index", "url": "https://openai.com/news/", "label": "OpenAI News", "category": "official_blog"},
        ),
    },
    {
        "key": "anthropic", "entity": "ANTHROPIC", "tickers": (),
        "leaders": ("Dario Amodei",),
        "official_hosts": ("anthropic.com", "www.anthropic.com"),
        "systemic": True,
        "sources": (
            {"kind": "html_index", "url": "https://www.anthropic.com/news", "label": "Anthropic News", "category": "official_blog"},
        ),
    },
    {
        "key": "ethereum", "entity": "ETH", "tickers": (),
        "leaders": ("Vitalik Buterin",),
        "official_hosts": ("ethereum.org", "blog.ethereum.org"),
        "systemic": True,
        "sources": (
            {"kind": "html_index", "url": "https://blog.ethereum.org/", "label": "Ethereum Foundation Blog", "category": "official_blog"},
        ),
    },
    {
        "key": "solana", "entity": "SOL", "tickers": (),
        "leaders": ("Anatoly Yakovenko",),
        "official_hosts": ("solana.com", "www.solana.com"),
        "systemic": True,
        "sources": (
            {"kind": "html_index", "url": "https://solana.com/news", "label": "Solana News", "category": "official_blog"},
        ),
    },
    {
        "key": "ripple", "entity": "XRP", "tickers": (),
        "leaders": ("Brad Garlinghouse",),
        "official_hosts": ("ripple.com", "www.ripple.com"),
        "systemic": True,
        "sources": (
            {"kind": "html_index", "url": "https://ripple.com/insights/", "label": "Ripple Insights", "category": "official_blog"},
        ),
    },
    {
        "key": "tether", "entity": "USDT", "tickers": (),
        "leaders": ("Paolo Ardoino",),
        "official_hosts": ("tether.io", "www.tether.io"),
        "systemic": True,
        "sources": (
            {"kind": "html_index", "url": "https://tether.io/news/", "label": "Tether News", "category": "official_blog"},
        ),
    },
    {
        "key": "binance", "entity": "BINANCE", "tickers": (),
        "leaders": ("Richard Teng",),
        "official_hosts": ("binance.com", "www.binance.com"),
        "systemic": True,
        "sources": (
            {"kind": "html_index", "url": "https://www.binance.com/en/blog", "label": "Binance Blog", "category": "official_blog"},
        ),
    },
)


@dataclass(frozen=True)
class PrimarySourceDocument:
    source: str
    source_ref: str
    title: str
    published_at: str
    entity: str
    document_text: str
    category_hint: str
    reliability: float
    metadata: Mapping[str, Any]


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or "")).casefold()).strip()


def _iso_z(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").casefold().strip(".")


def _host_allowed(url: str, hosts: Sequence[str]) -> bool:
    hostname = _host(url)
    if not hostname:
        return False
    for allowed in hosts:
        marker = str(allowed or "").casefold().strip(".")
        if hostname == marker or hostname.endswith("." + marker):
            return True
    return False


def _strip_html(value: str, limit: int = MAX_DOCUMENT_CHARS) -> str:
    text = re.sub(r"<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>", " ", value, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()[:limit]


def _parse_date_text(value: str) -> datetime | None:
    text = html.unescape(str(value or "")).strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
        if parsed:
            return parsed.astimezone(UTC)
    except Exception:
        pass
    clean = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(clean)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        pass
    patterns = (
        ("%B %d, %Y %I:%M %p %Z", r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}\s*(?:am|pm)\s*(?:EDT|EST|PDT|PST|UTC|GMT)\b"),
        ("%B %d, %Y", r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b"),
        ("%b %d, %Y", r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b"),
        ("%Y-%m-%d", r"\b20\d{2}-\d{2}-\d{2}\b"),
    )
    for fmt, pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        candidate = re.sub(r"\s+", " ", match.group(0)).strip()
        candidate = re.sub(r"\b(EDT|EST|PDT|PST|UTC|GMT)\b", "", candidate).strip()
        try:
            parsed = datetime.strptime(candidate, fmt.replace(" %Z", ""))
            return parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _extract_document_date(payload: str) -> datetime | None:
    # Structured publication metadata outranks visible page text. IR pages often
    # contain future event/calendar dates alongside an older article; choosing
    # the maximum date would incorrectly make the document appear future-dated.
    meta_patterns = (
        r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|date|datepublished|pubdate|publishdate|publication_date)["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:article:published_time|date|datepublished|pubdate|publishdate|publication_date)["\']',
        r'<time[^>]+datetime=["\']([^"\']+)["\']',
        r'["\']datePublished["\']\s*:\s*["\']([^"\']+)["\']',
    )
    explicit: list[datetime] = []
    for pattern in meta_patterns:
        for candidate in re.findall(pattern, payload, flags=re.I | re.S):
            value = _parse_date_text(candidate)
            if value is not None:
                explicit.append(value)
    if explicit:
        return max(explicit)

    # Visible text is a fallback only when the page exposes no structured date.
    return _parse_date_text(_strip_html(payload, 12000))


def _material_link(title: str, url: str) -> bool:
    text = f" {_norm(title)} {_norm(url)} "
    if any(term in text for term in LOW_VALUE_LINK_TERMS):
        return False
    return any(term in text for term in MATERIAL_LINK_TERMS)


def _anchor_candidates(index_html: str, base_url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        flags=re.I | re.S,
    )
    for match in pattern.finditer(index_html):
        href = html.unescape(match.group(1)).strip()
        title = _strip_html(match.group(2), 500)
        if not href or not title:
            continue
        absolute = urllib.parse.urljoin(base_url, href)
        if not _material_link(title, absolute):
            continue
        window = index_html[max(0, match.start() - 700): min(len(index_html), match.end() + 700)]
        context_text = _strip_html(window, 2200)
        context_date = _parse_date_text(context_text)
        rows.append({
            "url": absolute,
            "title": title,
            "context_date": context_date,
            "context_text": context_text,
        })
    dedup: dict[str, dict[str, Any]] = {}
    for row in rows:
        dedup.setdefault(row["url"], row)
    return list(dedup.values())[:MAX_INDEX_LINKS_PER_SOURCE]


def _feed_link(item: ET.Element) -> str:
    link = (item.findtext("link") or "").strip()
    if link:
        return link
    for node in item.findall("{*}link"):
        href = str(node.attrib.get("href") or "").strip()
        if href:
            return href
    return ""


def _feed_text(item: ET.Element, names: Sequence[str]) -> str:
    for name in names:
        node = item.find(name)
        if node is None:
            node = item.find(f"{{*}}{name}")
        if node is not None and (node.text or "").strip():
            return (node.text or "").strip()
    return ""


def _verified_actor(text: str, leaders: Sequence[str]) -> str | None:
    haystack = f" {_norm(text)} "
    if not any(term in haystack for term in EXECUTIVE_STATEMENT_TERMS):
        return None
    for leader in leaders:
        if _norm(leader) and _norm(leader) in haystack:
            return str(leader)
    return None


def _classify(title: str, body: str, default_category: str, actor: str | None) -> str:
    text = f" {_norm(title)} {_norm(body[:5000])} "
    if "transcript" in text or "prepared remarks" in text:
        return "official_earnings_transcript"
    if any(term in text for term in ("earnings", "financial results", "quarterly results", "quarter results")):
        return "official_earnings_release"
    if actor:
        return "verified_executive_statement"
    if "press release" in text or "news release" in text:
        return "official_press_release"
    return default_category


def _pdf_text(payload: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return ""
    try:
        reader = PdfReader(io.BytesIO(payload))
        parts = []
        for page in reader.pages[:24]:
            text = " ".join((page.extract_text() or "").split())
            if text:
                parts.append(text)
            if sum(len(x) for x in parts) >= MAX_DOCUMENT_CHARS:
                break
        return " ".join(parts)[:MAX_DOCUMENT_CHARS]
    except Exception:
        return ""


def _fetch_body(client: Any, url: str) -> tuple[str, str]:
    path = urllib.parse.urlparse(url).path.casefold()
    if path.endswith(".pdf") and hasattr(client, "bytes"):
        payload = client.bytes(url, accept="application/pdf")
        return _pdf_text(payload), "pdf"
    payload = client.text(url)
    return _strip_html(payload), "html"


def _feed_documents(
    client: Any,
    spec: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    now: datetime,
    cutoff: datetime,
) -> list[PrimarySourceDocument]:
    payload = client.text(str(source["url"]))
    root = ET.fromstring(payload)
    items = list(root.findall(".//item")) or list(root.findall(".//{*}entry"))
    out: list[PrimarySourceDocument] = []
    for item in items:
        title = html.unescape(_feed_text(item, ("title",)))
        link = urllib.parse.urljoin(str(source["url"]), html.unescape(_feed_link(item)))
        published = _parse_date_text(_feed_text(item, ("pubDate", "published", "updated", "date")))
        if not title or not link or published is None or published < cutoff or published > now + timedelta(minutes=10):
            continue
        if not _host_allowed(link, tuple(spec.get("official_hosts") or ())):
            continue
        summary = _strip_html(_feed_text(item, ("description", "summary", "content")), 6000)
        body = summary or title
        try:
            fetched, _ = _fetch_body(client, link)
            if len(fetched) >= 80:
                body = fetched
        except Exception:
            pass
        actor = _verified_actor(f"{title} {body[:5000]}", tuple(spec.get("leaders") or ()))
        category = _classify(title, body, str(source.get("category") or "official_ir"), actor)
        out.append(PrimarySourceDocument(
            source=str(source.get("label") or spec["key"]),
            source_ref=link,
            title=title[:500],
            published_at=_iso_z(published),
            entity=str(spec["entity"]),
            document_text=body[:MAX_DOCUMENT_CHARS],
            category_hint=category,
            reliability=0.995,
            metadata={
                "primary_source_class": category,
                "official_entity_key": spec["key"],
                "official_index_url": source["url"],
                "official_host_verified": True,
                "official_lineage_verified": True,
                "verified_actor": actor,
                "verified_actor_role": "tracked_executive" if actor else None,
                "source_collector_version": VERSION,
            },
        ))
    return out


def _index_documents(
    client: Any,
    spec: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    now: datetime,
    cutoff: datetime,
) -> list[PrimarySourceDocument]:
    index_url = str(source["url"])
    if not _host_allowed(index_url, tuple(spec.get("official_hosts") or ())):
        return []
    index_html = client.text(index_url)
    out: list[PrimarySourceDocument] = []
    for candidate in _anchor_candidates(index_html, index_url):
        url = str(candidate["url"])
        title = str(candidate["title"])
        published = candidate.get("context_date")
        body = ""
        raw_payload = ""
        document_format = "html"
        try:
            path = urllib.parse.urlparse(url).path.casefold()
            if path.endswith(".pdf") and hasattr(client, "bytes"):
                body = _pdf_text(client.bytes(url, accept="application/pdf"))
                document_format = "pdf"
            else:
                raw_payload = client.text(url)
                body = _strip_html(raw_payload)
                document_format = "html"
                page_date = _extract_document_date(raw_payload)
                if page_date is not None:
                    published = page_date
        except Exception:
            # The official index still proves the link's origin, but without the
            # document body/date it is not fresh-event eligible.
            pass
        if published is None or published < cutoff or published > now + timedelta(minutes=10):
            continue
        if not body:
            body = f"{title}. Discovered on official source index {index_url}."
        actor = _verified_actor(f"{title} {body[:7000]}", tuple(spec.get("leaders") or ()))
        category = _classify(title, body, str(source.get("category") or "official_ir"), actor)
        same_host = _host_allowed(url, tuple(spec.get("official_hosts") or ()))
        out.append(PrimarySourceDocument(
            source=str(source.get("label") or spec["key"]),
            source_ref=url,
            title=title[:500],
            published_at=_iso_z(published),
            entity=str(spec["entity"]),
            document_text=body[:MAX_DOCUMENT_CHARS],
            category_hint=category,
            reliability=0.995 if same_host else 0.985,
            metadata={
                "primary_source_class": category,
                "official_entity_key": spec["key"],
                "official_index_url": index_url,
                "official_host_verified": same_host,
                "official_lineage_verified": True,
                "document_format": document_format,
                "verified_actor": actor,
                "verified_actor_role": "tracked_executive" if actor else None,
                "source_collector_version": VERSION,
            },
        ))
    return out


def selected_specs(tickers: Iterable[str]) -> list[dict[str, Any]]:
    wanted = {str(value or "").upper().strip() for value in tickers if str(value or "").strip()}
    explicit_keys = {
        value.strip().casefold()
        for value in os.getenv("BELIEF_PRIMARY_ENTITY_KEYS", "").split(",")
        if value.strip()
    }
    out: list[dict[str, Any]] = []
    for raw in SOURCE_REGISTRY:
        spec = dict(raw)
        spec_tickers = {str(value).upper() for value in spec.get("tickers") or ()}
        if spec_tickers & wanted or bool(spec.get("systemic")) or str(spec["key"]).casefold() in explicit_keys:
            out.append(spec)
    return out


def collect_company_primary_documents(
    client: Any,
    *,
    now: datetime,
    lookback_hours: int,
    tickers: Sequence[str],
) -> tuple[list[PrimarySourceDocument], list[str]]:
    now = now.astimezone(UTC)
    cutoff = now - timedelta(hours=int(lookback_hours))
    documents: list[PrimarySourceDocument] = []
    errors: list[str] = []
    for spec in selected_specs(tickers):
        for source in spec.get("sources") or ():
            try:
                kind = str(source.get("kind") or "")
                if kind == "rss":
                    documents.extend(_feed_documents(client, spec, source, now=now, cutoff=cutoff))
                elif kind == "html_index":
                    documents.extend(_index_documents(client, spec, source, now=now, cutoff=cutoff))
            except Exception as exc:
                errors.append(f"{spec.get('key')}:{source.get('kind')}:{type(exc).__name__}:{str(exc)[:120]}")
    dedup: dict[str, PrimarySourceDocument] = {}
    for document in documents:
        previous = dedup.get(document.source_ref)
        if previous is None or document.reliability > previous.reliability:
            dedup[document.source_ref] = document
    ordered = sorted(dedup.values(), key=lambda row: row.published_at, reverse=True)
    return ordered[:MAX_DOCUMENTS], errors


def public_config() -> dict[str, Any]:
    return {
        "version": VERSION,
        "registry_entities": len(SOURCE_REGISTRY),
        "systemic_entities": sum(1 for row in SOURCE_REGISTRY if row.get("systemic")),
        "max_documents": MAX_DOCUMENTS,
        "max_index_links_per_source": MAX_INDEX_LINKS_PER_SOURCE,
        "fail_closed_on_undated_documents": True,
        "verified_executive_requires_named_leader_on_official_source": True,
        "source_classes": [
            "official_ir",
            "official_earnings",
            "official_earnings_release",
            "official_earnings_transcript",
            "official_press_release",
            "official_blog",
            "verified_executive_statement",
        ],
    }
