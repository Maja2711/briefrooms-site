#!/usr/bin/env python3
"""Private Saxo SIM OHLC adapter. Never publish Saxo chart values or log responses.

Chart timestamps are bar OPEN times (not price observation times). The last
incomplete bar is excluded from analysis. Entry/exit prices always come from
the separate current Saxo WebSocket BID/ASK (EPE), not Chart.Close.
"""
from __future__ import annotations

import base64
import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from belief_market_data_adapter import Bar
from saxo_private_feed import SAXO_STREAM_WORKER_URL, SAXO_PRIVATE_USER_AGENT

CHART_URL = SAXO_STREAM_WORKER_URL.replace("/saxo/quote", "/saxo/chart")
INSTRUMENT = "EURUSD=X"


class SaxoChartUnavailable(RuntimeError):
    """Fixed error code; do not include provider payloads, credentials or prices."""


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise SaxoChartUnavailable("saxo_chart_invalid_timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("naive")
        return dt.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise SaxoChartUnavailable("saxo_chart_invalid_timestamp") from None


def _side(sample: dict, field: str) -> float:
    bid, ask = sample.get(field + "Bid"), sample.get(field + "Ask")
    if not all(type(v) in (float, int) and math.isfinite(v)
               for v in (bid, ask)):
        raise SaxoChartUnavailable("saxo_chart_missing_bid_ask_ohlc")
    if not (0.8 < bid < ask < 1.5 and 0 < ask - bid <= 0.0015):
        raise SaxoChartUnavailable("saxo_chart_invalid_spread_or_ohlc")
    return (bid + ask) / 2.0


def _bar(sample: dict) -> Bar:
    if not isinstance(sample, dict):
        raise SaxoChartUnavailable("saxo_chart_bad_sample")
    ts = _parse_time(sample.get("Time"))
    op, hi, lo, cl = (_side(sample, part) for part in ("Open", "High", "Low", "Close"))
    # Bid-high and Ask-high may occur at different instants. Never interpret
    # side-specific extrema as a truly executable round-trip price.
    if not (0.8 < lo <= op <= hi < 1.5 and lo <= cl <= hi):
        raise SaxoChartUnavailable("saxo_chart_bad_ohlc_geometry")
    return Bar(timestamp=ts, open=op, high=hi, low=lo, close=cl)


class SaxoPrivateChartClient:
    """Read-only EURUSD chart source for the PRIVATE Daily process only.

    The source must never be instantiated by the public Daily workflow.
    """
    _daily_live_client = True

    def __init__(self, timeout: int = 12) -> None:
        if (os.environ.get("BRIEFROOMS_PRIVATE_RUNTIME") != "1" or
            os.environ.get("SAXO_PRIVATE_PRIMARY") != "1" or
            os.environ.get("SAXO_STREAM_PRIMARY") != "1" or
            os.environ.get("SAXO_CHART_PRIMARY") != "1"):
            raise SaxoChartUnavailable("saxo_chart_private_runtime_required")
        self.timeout = timeout
        self.password = os.environ.get("SAXO_PRIVATE_BASIC_PASSWORD", "")
        if not self.password:
            raise SaxoChartUnavailable("saxo_chart_credentials_missing")

    def _page(self, horizon: int, count: int, time: str | None) -> tuple[int, list[dict], int]:
        params = {"horizon": str(horizon), "count": str(count)}
        if time:
            params["time"] = time
        url = CHART_URL + "?" + urllib.parse.urlencode(params)
        basic = base64.b64encode(("admin:" + self.password).encode()).decode()
        req = urllib.request.Request(url, headers={
            "Authorization": "Basic " + basic,
            "Accept": "application/json",
            "Cache-Control": "no-store",
            "User-Agent": SAXO_PRIVATE_USER_AGENT,
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if getattr(response, "status", 200) != 200:
                    raise ValueError("bad response")
                data = json.loads(response.read(1_500_000))
            if (data.get("source") != "SAXO_SIM_CHART" or
                data.get("instrument") != "EUR/USD" or
                data.get("redistributable") is not False or
                data.get("trading_enabled") is not False or
                data.get("delayed_by_minutes") != 0 or
                data.get("horizon_minutes") != horizon or
                type(data.get("data_version")) is not int or
                not isinstance(data.get("data"), list) or
                not 1 <= len(data["data"]) <= count):
                raise ValueError("bad chart contract")
            return data["data_version"], data["data"], len(data["data"])
        except Exception:
            # Never expose Saxo URL, prices or response body in logs/CI.
            raise SaxoChartUnavailable("saxo_chart_private_page_unavailable") from None

    def bars(self, symbol: str, range_: str = "10d", interval: str = "30m") -> list[Bar]:
        if symbol != INSTRUMENT or (range_, interval) not in (
            ("5d", "1m"), ("10d", "30m")
        ):
            raise SaxoChartUnavailable("saxo_chart_unsupported_market_request")
        horizon = 1 if interval == "1m" else 30
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=5 if horizon == 1 else 10)
        collected: dict[datetime, Bar] = {}
        previous_first = None
        page_limit = 9 if horizon == 1 else 2
        for _ in range(page_limit):
            _data_version, samples, _count = self._page(horizon, 1200, previous_first)
            # Chart DataVersion can legitimately increment while a live bar
            # forms between pages. Check overlap content instead of demanding
            # a globally frozen version across multiple API calls.
            page = [_bar(item) for item in samples]
            page.sort(key=lambda x: x.timestamp)
            if not page:
                raise SaxoChartUnavailable("saxo_chart_empty")
            if previous_first is not None and page[0].timestamp >= _parse_time(previous_first):
                raise SaxoChartUnavailable("saxo_chart_pagination_not_advancing")
            for bar in page:
                # Saxo samples may be still forming; only CLOSED bars are valid
                # for technical indicators or retrospective SL/TP touch evidence.
                if cutoff <= bar.timestamp and bar.timestamp + timedelta(minutes=horizon) <= now:
                    if bar.timestamp in collected and collected[bar.timestamp] != bar:
                        raise SaxoChartUnavailable("saxo_chart_overlapping_sample_changed")
                    collected[bar.timestamp] = bar
            if page[0].timestamp <= cutoff:
                break
            previous_first = page[0].timestamp.isoformat().replace("+00:00", "Z")
        else:
            raise SaxoChartUnavailable("saxo_chart_insufficient_pagination")
        out = sorted(collected.values(), key=lambda x: x.timestamp)
        if len(out) < (200 if horizon == 1 else 65):
            raise SaxoChartUnavailable("saxo_chart_insufficient_history")
        max_age = timedelta(minutes=3 if horizon == 1 else 65)
        if now - (out[-1].timestamp + timedelta(minutes=horizon)) > max_age:
            raise SaxoChartUnavailable("saxo_chart_latest_bar_stale")
        return out
