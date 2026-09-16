#!/usr/bin/env python3
"""Resilient live US screener provider for Stock Trading v2.

Nasdaq's public screener endpoint occasionally stalls on a single full-market
response.  This provider pages the response, retries transient failures, dedupes
symbols and preserves diagnostics.  It is a market-data input only; it never
makes a portfolio decision.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Mapping

NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
DEFAULT_PAGE_SIZE = 1500
DEFAULT_MAX_PAGES = 8


class ProviderUnavailable(RuntimeError):
    """Raised when the live screener cannot be obtained completely."""


def _request_json(params: Mapping[str, Any], *, timeout: int) -> dict[str, Any]:
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
    request = urllib.request.Request(
        f"{NASDAQ_SCREENER_URL}?{query}",
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; BriefRooms-Stock-Trading-v2/1.0; +https://www.briefrooms.com/)",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed audited HTTPS endpoint
        raw = response.read()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ProviderUnavailable("Nasdaq screener response is not a JSON object")
    return payload


def _parse_page(payload: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int | None, Any]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        raise ProviderUnavailable("Nasdaq screener response has no data object")
    rows = data.get("rows")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ProviderUnavailable("Nasdaq screener rows are not an array")
    parsed = [dict(row) for row in rows if isinstance(row, Mapping)]
    raw_total = data.get("totalrecords", data.get("totalRecords"))
    total: int | None = None
    if raw_total not in (None, ""):
        try:
            total = int(float(str(raw_total).replace(",", "")))
        except ValueError:
            total = None
    return parsed, total, data.get("asOf")


def _page_with_retry(
    *,
    offset: int,
    limit: int,
    timeout: int,
    attempts: int,
    failures: list[str],
) -> tuple[list[dict[str, Any]], int | None, Any]:
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            payload = _request_json(
                {"tableonly": "true", "limit": limit, "offset": offset},
                timeout=timeout,
            )
            return _parse_page(payload)
        except Exception as exc:  # network/service failures are intentionally captured
            last_error = exc
            failures.append(
                f"page offset={offset} attempt={attempt}: {type(exc).__name__}: {' '.join(str(exc).split())}"[:900]
            )
            if attempt < attempts:
                time.sleep(min(4.0, 0.75 * (2 ** (attempt - 1))))
    raise ProviderUnavailable(
        f"Nasdaq page failed after {attempts} attempts at offset {offset}: {last_error}"
    )


def _full_download_fallback(*, timeout: int, failures: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        payload = _request_json({"tableonly": "true", "download": "true"}, timeout=max(timeout, 35))
        rows, total, as_of = _parse_page(payload)
        if not rows:
            raise ProviderUnavailable("Nasdaq full-download fallback returned no rows")
        return rows, {
            "provider": "Nasdaq",
            "endpoint": NASDAQ_SCREENER_URL,
            "mode": "full_download_fallback",
            "rows_received": len(rows),
            "total_records_hint": total,
            "as_of": as_of,
            "failures": failures,
            "complete": total is None or len(rows) >= total,
        }
    except Exception as exc:
        failures.append(f"full-download fallback: {type(exc).__name__}: {' '.join(str(exc).split())}"[:900])
        raise ProviderUnavailable("Nasdaq screener unavailable through paged and full-download modes") from exc


def fetch_rows(
    *,
    timeout: int = 15,
    attempts: int = 2,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch a complete broad-market screener snapshot with retries.

    The function fails closed if a provider reports a total record count that we
    cannot fully retrieve.  Partial market coverage must not silently enter the
    opportunity scanner as if it were complete.
    """
    failures: list[str] = []
    by_symbol: dict[str, dict[str, Any]] = {}
    total_hint: int | None = None
    as_of: Any = None
    pages_completed = 0

    for page_index in range(max(1, max_pages)):
        offset = page_index * max(1, page_size)
        try:
            rows, page_total, page_as_of = _page_with_retry(
                offset=offset,
                limit=max(1, page_size),
                timeout=max(3, timeout),
                attempts=max(1, attempts),
                failures=failures,
            )
        except ProviderUnavailable:
            if page_index == 0:
                return _full_download_fallback(timeout=timeout, failures=failures)
            raise

        pages_completed += 1
        if page_total is not None:
            total_hint = page_total if total_hint is None else max(total_hint, page_total)
        if page_as_of is not None:
            as_of = page_as_of
        if not rows:
            break

        before = len(by_symbol)
        for row in rows:
            symbol = str(row.get("symbol") or "").upper().strip()
            if symbol:
                by_symbol[symbol] = row
        added = len(by_symbol) - before

        if total_hint is not None and len(by_symbol) >= total_hint:
            break
        if len(rows) < page_size:
            break
        if added == 0:
            break

    rows = list(by_symbol.values())
    if not rows:
        return _full_download_fallback(timeout=timeout, failures=failures)
    if total_hint is not None and len(rows) < total_hint:
        raise ProviderUnavailable(
            f"Nasdaq screener incomplete: received {len(rows)} unique rows of {total_hint}"
        )

    return rows, {
        "provider": "Nasdaq",
        "endpoint": NASDAQ_SCREENER_URL,
        "mode": "paged_with_retry",
        "page_size": page_size,
        "pages_completed": pages_completed,
        "rows_received": len(rows),
        "total_records_hint": total_hint,
        "as_of": as_of,
        "failures": failures,
        "complete": True,
    }
