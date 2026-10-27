#!/usr/bin/env python3
"""Private Saxo SIM EUR/USD quote adapter for optional Daily paper quality checks.

This module is deliberately NOT a public EUR/USD market-data feed. It must
never return raw Saxo quotes to public artifacts, GitHub logs, or site JSON.
Saxo SIM quotes are usable only for explicitly authorized private testing.
No broker orders are sent.
"""
from __future__ import annotations

import base64
import json
import math
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any, Mapping

SAXO_WORKER_URL = (
    "https://briefrooms-saxo-eurusd.szczerbinski0577.workers.dev/saxo/quote"
)
MAX_QUOTE_AGE_SECONDS = 90.0
MAX_FUTURE_SECONDS = 30.0


class PrivateSaxoFeedError(ValueError):
    """Fixed, non-sensitive failure; never attach broker response or credentials."""


def _reject() -> None:
    raise PrivateSaxoFeedError("private_saxo_quote_unavailable")


def _time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        _reject()
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            _reject()
        return stamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        _reject()


def validate_private_saxo_snapshot(
    payload: Mapping[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Validate private SIM bid/ask pair for simulated execution."""
    mid = validate_private_saxo_quote(payload, now=now)
    return {
        "bid": float(payload["bid"]),
        "ask": float(payload["ask"]),
        "mid": mid,
        "observed_at": _time(
            payload.get("last_updated")
            or payload.get("last_updated_at")
            or payload.get("timestamp")
            or payload.get("LastUpdated")
        ),
        "source": "SAXO_SIM",
        "redistributable": False,
    }


def validate_private_saxo_quote(
    payload: Mapping[str, Any], *, now: datetime | None = None
) -> float:
    """Validate an actual Saxo SIM FX spot quote; return mid only in memory.

    The caller MUST NOT serialize or print the returned price as Saxo data.
    A non-redistributable source cannot be substituted for a licensed public
    execution feed. This method enforces freshness and coherent bid/ask.
    """
    if not isinstance(payload, Mapping):
        _reject()
    if payload.get("source") != "SAXO_SIM" or payload.get("instrument") not in (
        "EUR/USD", "EURUSD"
    ):
        _reject()
    if payload.get("redistributable") is not False:
        _reject()
    if payload.get("trading_enabled") is not False:
        _reject()
    if str(payload.get("market_state") or "").lower() != "open":
        _reject()
    try:
        if float(payload.get("delayed_by_minutes", -1)) != 0:
            _reject()
        bid = float(payload["bid"])
        ask = float(payload["ask"])
        mid = float(payload["mid"])
        if not all(math.isfinite(x) for x in (bid, ask, mid)):
            _reject()
        if not (0.8 < bid <= mid <= ask < 1.5):
            _reject()
        if not 0 < (ask - bid) <= 0.0015:
            _reject()
        if abs(mid - (bid + ask) / 2.0) > 0.00002:
            _reject()
    except (TypeError, KeyError, ValueError, OverflowError):
        _reject()
    stamp = _time(
        payload.get("last_updated")
        or payload.get("last_updated_at")
        or payload.get("timestamp")
        or payload.get("LastUpdated")
    )
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current - stamp).total_seconds()
    if age < -MAX_FUTURE_SECONDS or age > MAX_QUOTE_AGE_SECONDS:
        _reject()
    return mid


def fetch_private_saxo_quote(*, now: datetime | None = None) -> dict[str, Any]:
    """Obtain private Bid/Ask/Mid using machine-to-machine Cloudflare auth."""
    username = os.environ.get("SAXO_PRIVATE_BASIC_USER", "admin")
    password = os.environ.get("SAXO_PRIVATE_BASIC_PASSWORD", "")
    if not username or not password or any(ch in username for ch in "\r\n:"):
        _reject()
    header = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        SAXO_WORKER_URL,
        headers={
            "Authorization": f"Basic {header}",
            "Accept": "application/json",
            "Cache-Control": "no-store",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            if getattr(response, "status", 200) != 200:
                _reject()
            payload = json.loads(response.read(16385))
        return validate_private_saxo_snapshot(payload, now=now)
    except Exception:
        _reject()


def fetch_private_saxo_mid(*, now: datetime | None = None) -> float:
    """Access the fixed user-owned Worker over HTTPS using GitHub Actions secret.

    Password is the private Cloudflare Worker's HTTP Basic password, NOT the
    Saxo app key/secret. This function returns only an in-memory validated mid.
    Errors are intentionally sanitized (no body, URL query, or credentials).
    """
    username = os.environ.get("SAXO_PRIVATE_BASIC_USER", "admin")
    password = os.environ.get("SAXO_PRIVATE_BASIC_PASSWORD", "")
    if not username or not password or any(ch in username for ch in "\r\n:"):
        _reject()
    header = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        SAXO_WORKER_URL,
        headers={
            "Authorization": f"Basic {header}",
            "Accept": "application/json",
            "Cache-Control": "no-store",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            if getattr(response, "status", 200) != 200:
                _reject()
            payload = json.loads(response.read(16385))
        return validate_private_saxo_quote(payload, now=now)
    except Exception:
        _reject()
