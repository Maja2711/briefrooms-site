#!/usr/bin/env python3
"""Official GPW Main Market universe provider for Stock Trading v2.

The public GPW company list is server-rendered in batches and loads additional
rows through the exchange's own GPWCompanySearch AJAX endpoint. We first read
its live search form, then submit the same filter contract with all index,
country and voivodship checkboxes enabled. Network reads are retried and pages
are deliberately small because the GPW endpoint can terminate large chunked
responses early. The provider fails closed if the final universe is incomplete.
"""
from __future__ import annotations

import html
import http.client
import http.cookiejar
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

GPW_COMPANIES_URL = "https://www.gpw.pl/spolki"
GPW_AJAX_URL = "https://www.gpw.pl/ajaxindex.php"
DEFAULT_PAGE_SIZE = 40
DEFAULT_MAX_PAGES = 20
DEFAULT_ATTEMPTS = 3


class ProviderUnavailable(RuntimeError):
    pass


class _SearchFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inputs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        data = {str(key): value for key, value in attrs}
        name = str(data.get("name") or "").strip()
        if not name:
            return
        self.inputs[name] = str(data.get("value") or "")


class _CompanyLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current_href: str | None = None
        self.current_text: list[str] = []
        self.companies: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        data = {str(key): value for key, value in attrs}
        href = str(data.get("href") or "")
        if "spolka?isin=" in href:
            self.current_href = href
            self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_href is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self.current_href is None:
            return
        text = " ".join(" ".join(self.current_text).split())
        parsed = urllib.parse.urlparse(self.current_href)
        query = urllib.parse.parse_qs(parsed.query)
        isin = str((query.get("isin") or [""])[0]).strip().upper()
        match = re.search(r"\(([A-Z0-9._-]{1,16})\)\s*$", text.upper())
        if isin and match:
            ticker = match.group(1)
            name = re.sub(r"\s*\([A-Z0-9._-]{1,16}\)\s*$", "", text, flags=re.IGNORECASE).strip()
            self.companies.append({"isin": isin, "ticker": ticker, "name": html.unescape(name)})
        self.current_href = None
        self.current_text = []


def _opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _request_once(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    data: bytes | None = None,
    timeout: int = 25,
) -> str:
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; BriefRooms-Stock-Trading-v2/1.0; +https://www.briefrooms.com/)",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
            "Referer": GPW_COMPANIES_URL,
            "Connection": "close",
            "Cache-Control": "no-cache",
        },
    )
    with opener.open(request, timeout=timeout) as response:  # noqa: S310 - fixed official GPW endpoints
        raw = response.read()
    return raw.decode("utf-8", errors="replace")


def _request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    data: bytes | None = None,
    timeout: int = 25,
    attempts: int = DEFAULT_ATTEMPTS,
    diagnostics: list[str] | None = None,
) -> str:
    """Retry transient GPW transport failures; never accept a truncated page."""
    errors = diagnostics if diagnostics is not None else []
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return _request_once(opener, url, data=data, timeout=timeout)
        except (
            TimeoutError,
            ConnectionError,
            OSError,
            urllib.error.URLError,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
        ) as exc:
            last_error = exc
            errors.append(
                f"{url} attempt={attempt}: {type(exc).__name__}: {' '.join(str(exc).split())}"[:900]
            )
            if attempt < attempts:
                time.sleep(min(5.0, 0.75 * (2 ** (attempt - 1))))
    raise ProviderUnavailable(f"GPW request failed after {attempts} attempts: {last_error}") from last_error


def parse_search_form(page_html: str) -> dict[str, str]:
    parser = _SearchFormParser()
    parser.feed(page_html)
    payload = dict(parser.inputs)
    payload.update(
        {
            "action": "GPWCompanySearch",
            "start": "ajaxSearch",
            "page": "spolki",
            "format": "html",
            "lang": "PL",
            "letter": "",
            "order": "",
            "order_type": "",
            "searchText": "",
        }
    )
    for key in list(payload):
        if key.startswith("country[") or key.startswith("voivodship[") or key.startswith("index["):
            payload[key] = "on"
    return payload


def parse_companies(fragment_html: str) -> list[dict[str, str]]:
    parser = _CompanyLinkParser()
    parser.feed(fragment_html)
    by_ticker: dict[str, dict[str, str]] = {}
    for company in parser.companies:
        by_ticker[company["ticker"]] = company
    return sorted(by_ticker.values(), key=lambda row: row["ticker"])


def expected_company_count(page_html: str) -> int | None:
    text = re.sub(r"<[^>]+>", " ", page_html)
    text = " ".join(html.unescape(text).split())
    match = re.search(r"Lista\s+spółek\s+(\d+)\s+Spółek", text, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def fetch_companies(
    *,
    timeout: int = 25,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    attempts: int = DEFAULT_ATTEMPTS,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    opener = _opener()
    failures: list[str] = []
    landing = _request(
        opener,
        GPW_COMPANIES_URL,
        timeout=timeout,
        attempts=attempts,
        diagnostics=failures,
    )
    payload = parse_search_form(landing)
    target = expected_company_count(landing)
    by_ticker: dict[str, dict[str, str]] = {}
    page_counts: list[int] = []

    for page_index in range(max(1, max_pages)):
        offset = page_index * max(1, page_size)
        page_payload = dict(payload)
        page_payload["offset"] = str(offset)
        page_payload["limit"] = str(max(1, page_size))
        encoded = urllib.parse.urlencode(page_payload).encode("utf-8")
        fragment = _request(
            opener,
            GPW_AJAX_URL,
            data=encoded,
            timeout=timeout,
            attempts=attempts,
            diagnostics=failures,
        )
        companies = parse_companies(fragment)
        page_counts.append(len(companies))
        if not companies:
            break
        before = len(by_ticker)
        for company in companies:
            by_ticker[company["ticker"]] = company
        added = len(by_ticker) - before
        if target is not None and len(by_ticker) >= target:
            break
        if len(companies) < page_size or added == 0:
            break

    companies = sorted(by_ticker.values(), key=lambda row: row["ticker"])
    minimum_expected = 200
    if target is not None and len(companies) < target:
        raise ProviderUnavailable(f"GPW universe incomplete: received {len(companies)} of expected {target}")
    if target is None and len(companies) < minimum_expected:
        raise ProviderUnavailable(f"GPW universe unexpectedly small: {len(companies)}")

    return companies, {
        "provider": "GPW",
        "authority": "official_exchange_company_search",
        "landing_url": GPW_COMPANIES_URL,
        "ajax_url": GPW_AJAX_URL,
        "expected_company_count": target,
        "companies_received": len(companies),
        "page_size": page_size,
        "page_counts": page_counts,
        "request_failures_recovered": failures,
        "complete": target is None or len(companies) >= target,
    }
